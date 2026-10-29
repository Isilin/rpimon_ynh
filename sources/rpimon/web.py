"""Interface web : API JSON + fichiers statiques (HTTP minimal, derrière le nginx de YunoHost)."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import os
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .metrics import AGGS, DEC, KEYS, METRICS
from .store import connect_ro, db_files
from .util import decode_flags, meminfo, read, system_info

log = logging.getLogger("rpimon")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
RESOLUTIONS = ("raw", "hourly", "daily")


def load_static(directory=STATIC_DIR):
    """Charge les fichiers servis en mémoire : {nom: (contenu, type, etag)}."""
    out = {}
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            p = os.path.join(directory, name)
            ext = os.path.splitext(name)[1]
            if os.path.isfile(p) and ext in CTYPES:
                with open(p, "rb") as f:
                    data = f.read()
                out[name] = (data, CTYPES[ext], '"%s"' % hashlib.sha1(data).hexdigest()[:16])
    return out


def csv_number(x, key):
    """Nombre au format français (virgule décimale), sans notation scientifique."""
    s = "%.*f" % (DEC[key] + 1, x)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


class Api:
    def __init__(self, cfg, collector=None):
        self.cfg = cfg
        self.col = collector
        self.info = system_info()
        self.static = load_static()

    # -- sélection de la résolution -------------------------------------------
    @staticmethod
    def pick_res(con, t0, t1):
        span = t1 - t0
        start = 0 if span <= 2.5 * 86400 else 1 if span <= 120 * 86400 else 2
        for r in RESOLUTIONS[start:]:
            mn = con.execute("SELECT MIN(ts) FROM %s" % ("samples" if r == "raw" else r)).fetchone()[0]
            if mn is not None and mn <= t0 + span * 0.5:
                return r
        return RESOLUTIONS[start]

    def query(self, keys, t0, t1, res):
        con = connect_ro(self.cfg.db)
        try:
            if res == "auto":
                res = self.pick_res(con, t0, t1)
            if res == "raw":
                rows = con.execute("SELECT ts, %s FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts"
                                   % ", ".join(keys), (t0, t1)).fetchall()
                cols = [(k, "avg") for k in keys]
            else:
                cols = [(k, a) for k in keys for a in AGGS]
                rows = con.execute("SELECT ts, %s FROM %s WHERE ts BETWEEN ? AND ? ORDER BY ts"
                                   % (", ".join("%s_%s" % c for c in cols), res), (t0, t1)).fetchall()
        finally:
            con.close()
        return res, cols, rows

    def series(self, q):
        keys, t0, t1, res = self._params(q)
        res, cols, rows = self.query(keys, t0, t1, res)
        ts = [r[0] for r in rows]
        if res == "raw":
            diffs = [b - a for a, b in zip(ts, ts[1:])]
            step = int(statistics.median(diffs)) if diffs else self.cfg.interval
        else:
            step = 3600 if res == "hourly" else 86400
        out_t = []
        out = {c: [] for c in cols}
        prev = None
        for r in rows:
            t = r[0]
            if prev is not None and t - prev > step * 2.5:   # trou => coupure de la courbe
                out_t.append(prev + step)
                for c in cols:
                    out[c].append(None)
            out_t.append(t)
            for i, c in enumerate(cols, 1):
                x = r[i]
                out[c].append(None if x is None else round(x, DEC[c[0]] + 1))
            prev = t
        s = {}
        for (k, a), vals in out.items():
            s.setdefault(k, {})[a] = vals
        return {"res": res, "step": step, "from": t0, "to": t1, "t": out_t, "s": s}

    def export_csv(self, q):
        keys, t0, t1, res = self._params(q)
        res, cols, rows = self.query(keys, t0, t1, res)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["date", "ts"] + [k if res == "raw" else "%s_%s" % (k, a) for k, a in cols])
        for r in rows:
            w.writerow([dt.datetime.fromtimestamp(r[0]).strftime("%Y-%m-%d %H:%M:%S"), r[0]]
                       + ["" if x is None else csv_number(x, k) for (k, _), x in zip(cols, r[1:])])
        name = "rpimon-%s-%s.csv" % (res, dt.datetime.fromtimestamp(t1).strftime("%Y%m%d"))
        return buf.getvalue().encode("utf-8-sig"), name

    @staticmethod
    def _params(q):
        now = int(time.time())
        keys = [k for k in (q.get("keys", [""])[0] or ",".join(KEYS)).split(",") if k]
        bad = [k for k in keys if k not in KEYS]
        if bad or not keys:
            raise ValueError("métrique inconnue : %s" % ",".join(bad))
        t1 = int(q.get("to", [now])[0])
        t0 = int(q.get("from", [t1 - 86400])[0])
        res = q.get("res", ["auto"])[0]
        if t0 >= t1 or res not in ("auto",) + RESOLUTIONS:
            raise ValueError("paramètres invalides")
        return keys, t0, t1, res

    def current(self):
        con = connect_ro(self.cfg.db)
        try:
            row = con.execute("SELECT ts, %s FROM samples ORDER BY ts DESC LIMIT 1"
                              % ", ".join(KEYS)).fetchone()
        finally:
            con.close()
        mi = meminfo()
        up = (read("/proc/uptime") or "0").split()[0]
        la = (read("/proc/loadavg") or "0 0 0").split()[:3]
        disk_total = None
        try:
            st = os.statvfs(self.cfg.mount)
            disk_total = st.f_blocks * st.f_frsize
        except (OSError, AttributeError):
            pass
        fmax = (read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq") or "").strip()
        return {
            "now": time.time(),
            "ts": row[0] if row else None,
            "v": dict(zip(KEYS, row[1:])) if row else {},
            "uptime": float(up),
            "load": [float(x) for x in la],
            "mem_total": mi.get("MemTotal"), "swap_total": mi.get("SwapTotal"),
            "disk_total": disk_total,
            "freq_max": int(fmax) / 1000.0 if fmax.isdigit() else None,
            "flags": decode_flags(self.col.flags if self.col else None),
            "vcgencmd": bool(self.col and self.col.vcgencmd),
        }

    def meta(self):
        con = connect_ro(self.cfg.db)
        try:
            ranges = {}
            for t in ("samples", "hourly", "daily"):
                mn, mx, n = con.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM %s" % t).fetchone()
                ranges["raw" if t == "samples" else t] = {"from": mn, "to": mx, "rows": n}
            migrated = con.execute("SELECT v FROM meta WHERE k = 'migrated_at'").fetchone()
        finally:
            con.close()
        size = sum(os.path.getsize(p) for p in db_files(self.cfg.db) if not p.endswith("-shm"))
        return dict(self.info, version=__version__, interval=self.cfg.interval,
                    retention={"raw": self.cfg.raw_days, "hourly": self.cfg.hourly_days,
                               "daily": self.cfg.daily_days},
                    metrics=[{"key": k, "label": lb, "unit": u, "dec": d} for k, lb, u, d in METRICS],
                    ranges=ranges, db_size=size, migrated=bool(migrated))


class Handler(BaseHTTPRequestHandler):
    server_version = "rpimon/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # pas de log d'accès : nginx s'en charge déjà
        pass

    def _send(self, code, body, ctype, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj):
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _file(self, name):
        f = self.server.api.static.get(name)
        if not f:
            return self._send(404, b"introuvable", "text/plain; charset=utf-8")
        data, ctype, etag = f
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, data, ctype, {"ETag": etag, "Cache-Control": "no-cache"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        api = self.server.api
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        # Tolère n'importe quel préfixe (/rpimon/…) au cas où le reverse-proxy le transmet.
        i, j = path.find("/api/"), path.find("/static/")
        route = path[i:] if i >= 0 else path[j:] if j >= 0 else path
        try:
            if route.startswith("/static/"):
                return self._file(route[len("/static/"):])
            if route == "/api/meta":
                return self._json(api.meta())
            if route == "/api/current":
                return self._json(api.current())
            if route == "/api/series":
                return self._json(api.series(q))
            if route == "/api/export.csv":
                body, name = api.export_csv(q)
                return self._send(200, body, "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s"' % name,
                                   "Cache-Control": "no-store"})
            if route.endswith("/") or route.endswith("/index.html"):
                return self._file("index.html")
            self._send(404, b"introuvable", "text/plain; charset=utf-8")
        except ValueError as e:
            self._send(400, str(e).encode("utf-8"), "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("erreur sur %s", self.path)
            self._send(500, b"erreur interne", "text/plain; charset=utf-8")


def start_web(cfg, collector):
    try:
        srv = ThreadingHTTPServer((cfg.listen, cfg.port), Handler)
    except OSError as e:
        log.error("interface web indisponible : impossible d'écouter sur %s:%d (%s)",
                  cfg.listen, cfg.port, e)
        return None
    srv.daemon_threads = True
    srv.api = Api(cfg, collector)
    threading.Thread(target=srv.serve_forever, name="web", daemon=True).start()
    log.info("interface web sur http://%s:%d/", cfg.listen, cfg.port)
    return srv
