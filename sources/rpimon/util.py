"""Petits utilitaires : lecture de /proc et /sys, dates locales, formatage."""
from __future__ import annotations

import datetime as dt
import os
import platform
import socket
import time


def read(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def local_midnight(ts):
    d = dt.datetime.fromtimestamp(ts)
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


def day_start(d):
    """Minuit local d'une date (gère les changements d'heure)."""
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


def safe_since(cfg, now=None):
    """Début du plus ancien jour encore *entièrement* présent en brut."""
    now = time.time() if now is None else now
    return local_midnight(now - (cfg.raw_days - 1) * 86400)


def meminfo():
    out = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        k, _, rest = line.partition(":")
        f = rest.split()
        if f and f[0].isdigit():
            out[k] = int(f[0]) * 1024
    return out


def fmt_bytes(n):
    for unit in ("o", "ko", "Mo", "Go", "To"):
        if abs(n) < 1000 or unit == "To":
            return ("%.0f %s" if unit == "o" else "%.1f %s") % (n, unit)
        n /= 1000.0


def fmt_ts(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def fmt_int(n):
    return f"{n:,}".replace(",", " ")


def system_info():
    model = (read("/proc/device-tree/model") or "").strip("\x00\n ")
    pretty = ""
    for line in (read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
    return {"hostname": socket.gethostname(), "model": model or None,
            "os": pretty or None, "kernel": platform.release()}


def decode_flags(f):
    """Décode le résultat de `vcgencmd get_throttled`."""
    if f is None:
        return None
    return {
        "raw": "0x%x" % f,
        "undervolt_now": bool(f & 0x1), "freq_capped_now": bool(f & 0x2),
        "throttled_now": bool(f & 0x4), "soft_temp_now": bool(f & 0x8),
        "undervolt_boot": bool(f & 0x10000), "freq_capped_boot": bool(f & 0x20000),
        "throttled_boot": bool(f & 0x40000), "soft_temp_boot": bool(f & 0x80000),
    }


def fix_owner(*paths):
    """Après un passage en root, rend les fichiers au propriétaire de leur dossier."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    for path in paths:
        if not os.path.exists(path):
            continue
        st = os.stat(os.path.dirname(os.path.abspath(path)))
        os.chown(path, st.st_uid, st.st_gid)
