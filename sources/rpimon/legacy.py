"""Import des anciens relevés de température (/var/log/temperatures/JJ-MM-AAAA.html).

Trois étapes indépendantes, toutes testables sans root :
  analyze()  lit les fichiers, détecte le format décimal, compte ce qui est reconnu ;
  convert()  transforme en mesures brutes (jours récents) et agrégats horaires/journaliers ;
  archive()  archive le dossier en .tar.xz, vérifie l'archive, puis supprime le dossier.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import shutil
import statistics
import tarfile
import time
from dataclasses import dataclass, field

from .util import day_start, read, safe_since

LEGACY_DIR = "/var/log/temperatures"

FILE_DATE_RES = (
    (re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{4})(?!\d)"), (3, 2, 1)),   # JJ-MM-AAAA
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), (1, 2, 3)),   # AAAA-MM-JJ
)
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)")
DEG_RE = re.compile(r"(?<![\d.,])(-?\d{1,6})(?:[.,](\d+))?\s*(?:°|º|deg)\s*C?", re.I)
NUM_RE = re.compile(r"(?<![\d.,:])(-?\d{1,6})(?:[.,](\d+))?(?![\d:])")
BREAK_RE = re.compile(r"<\s*br\s*/?\s*>|</\s*(?:p|div|li|tr|h\d)\s*>", re.I)
TAG_RE = re.compile(r"<[^>]*>")


# --------------------------------------------------------------------------- #
# Lecture des fichiers
# --------------------------------------------------------------------------- #
def legacy_files(src):
    """[(date, chemin)] des fichiers dont le nom contient une date, triés."""
    out = []
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if not os.path.isfile(p):
            continue
        for rx, (yi, mi, di) in FILE_DATE_RES:
            m = rx.search(name)
            if m:
                try:
                    d = dt.date(int(m.group(yi)), int(m.group(mi)), int(m.group(di)))
                except ValueError:
                    break
                out.append((d, p))
                break
    out.sort()
    return out


def legacy_lines(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    text = BREAK_RE.sub("\n", text)
    text = html.unescape(TAG_RE.sub(" ", text))
    return text.splitlines()


def parse_legacy(path):
    """Renvoie [(h, m, s, partie_entière, partie_décimale)], nb de lignes ignorées, exemples."""
    found, ignored, examples = [], 0, []
    pending = None
    for line in legacy_lines(path):
        line = line.strip()
        if not line:
            continue
        tm = TIME_RE.search(line)
        if tm:
            rest = line[tm.end():]
            t = DEG_RE.search(rest) or DEG_RE.search(line[:tm.start()]) or NUM_RE.search(rest)
            if t:
                found.append((int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0),
                              t.group(1), t.group(2)))
                pending = None
            else:
                pending = tm                       # heure et température sur deux lignes
            continue
        t = DEG_RE.search(line)
        if t is None:
            n = NUM_RE.search(line)
            t = n if n and n.group(2) else None   # sans heure : on exige un nombre décimal
        if t and pending:
            found.append((int(pending.group(1)), int(pending.group(2)), int(pending.group(3) or 0),
                          t.group(1), t.group(2)))
            pending = None
        else:
            ignored += 1
            if len(examples) < 5:
                examples.append(line[:120])
    return found, ignored, examples


def decode_temp(ip, fp, mode):
    """Partie entière + partie décimale (chaînes) -> °C, ou None si hors plage."""
    v = abs(int(ip))
    if fp:
        if mode == "milli" and len(fp) <= 3:
            v += int(fp) / 1000.0           # "44.12" écrit par $INT.$FLOAT = 44,012 °C
        else:
            v += int(fp) / 10.0 ** len(fp)
    if ip.startswith("-"):
        v = -v
    if abs(v) >= 1000:                      # valeur brute en milli-degrés (44123)
        v /= 1000.0
    return v if -40 <= v <= 125 else None


def detect_decimal(frac_lengths, leading_zero):
    """L'ancien script affiche $TEMP_INT.$TEMP_FLOAT sans zéro de remplissage :
    44012 m°C devient "44.12". Signature : longueurs variables ET jamais de zéro en tête."""
    lengths = {k for k, n in frac_lengths.items() if n}
    if len(lengths) > 1 and not leading_zero:
        return "milli"
    return "plain"


# --------------------------------------------------------------------------- #
# Analyse et conversion
# --------------------------------------------------------------------------- #
@dataclass
class Analysis:
    files: list
    total_size: int
    recognized: int = 0
    ignored: int = 0
    examples: list = field(default_factory=list)
    per_file: list = field(default_factory=list)
    frac_len: dict = field(default_factory=dict)
    leading_zero: bool = False

    @property
    def mode(self):
        return detect_decimal(self.frac_len, self.leading_zero)

    @property
    def per_day(self):
        return statistics.median([n for n in self.per_file if n] or [0])


def analyze(src):
    files = legacy_files(src)
    a = Analysis(files=files, total_size=sum(os.path.getsize(p) for _, p in files))
    for _, p in files:
        found, ign, ex = parse_legacy(p)
        a.recognized += len(found)
        a.ignored += ign
        a.per_file.append(len(found))
        if len(a.examples) < 5:
            a.examples += ex[:5 - len(a.examples)]
        for f in found:
            fp = f[4]
            if fp and len(fp) <= 3:
                a.frac_len[len(fp)] = a.frac_len.get(len(fp), 0) + 1
                if len(fp) > 1 and fp[0] == "0":
                    a.leading_zero = True
    return a


@dataclass
class Plan:
    raw: list = field(default_factory=list)        # [(ts, temp)]
    hourly: list = field(default_factory=list)     # [(ts, n, avg, min, max)]
    daily: list = field(default_factory=list)
    cutoff_raw: int = 0
    skipped_overlap: int = 0
    invalid: int = 0
    tmin: int | None = None
    tmax: int | None = None
    vmin: float | None = None
    vmax: float | None = None
    vavg: float | None = None


def _agg_rows(agg):
    return [(b, a[0], round(a[1] / a[0], 3), round(a[2], 2), round(a[3], 2))
            for b, a in sorted(agg.items())]


def convert(files, mode, cfg, first_collect=None, now=None):
    """Mesures des jours encore couverts en brut -> samples ; le reste -> agrégats.
    Les mesures postérieures au premier relevé de rpimon sont ignorées (doublons)."""
    now = time.time() if now is None else now
    plan = Plan(cutoff_raw=safe_since(cfg, now))
    cut_h = now - cfg.hourly_days * 86400 if cfg.hourly_days else None
    cut_d = now - cfg.daily_days * 86400 if cfg.daily_days else None
    agg_h, agg_d = {}, {}
    vsum, vn = 0.0, 0

    for d, p in files:
        found, _, _ = parse_legacy(p)
        midnight = day_start(d)
        regular = day_start(d + dt.timedelta(days=1)) - midnight == 86400
        for h, m, s, ip, fp in found:
            val = decode_temp(ip, fp, mode)
            if val is None:
                plan.invalid += 1
                continue
            ts = (midnight + h * 3600 + m * 60 + s) if regular else \
                int(time.mktime((d.year, d.month, d.day, h, m, s, 0, 0, -1)))
            if first_collect and ts >= first_collect:
                plan.skipped_overlap += 1          # déjà couvert par le nouveau collecteur
                continue
            plan.tmin = ts if plan.tmin is None else min(plan.tmin, ts)
            plan.tmax = ts if plan.tmax is None else max(plan.tmax, ts)
            plan.vmin = val if plan.vmin is None else min(plan.vmin, val)
            plan.vmax = val if plan.vmax is None else max(plan.vmax, val)
            vsum += val
            vn += 1
            if ts >= plan.cutoff_raw:
                plan.raw.append((ts, round(val, 2)))
                continue
            for bucket, agg, cut in ((ts - ts % 3600, agg_h, cut_h), (midnight, agg_d, cut_d)):
                if cut is not None and bucket < cut:
                    continue
                a = agg.get(bucket)
                if a is None:
                    agg[bucket] = [1, val, val, val]
                else:
                    a[0] += 1
                    a[1] += val
                    a[2] = min(a[2], val)
                    a[3] = max(a[3], val)

    plan.vavg = vsum / vn if vn else None
    plan.hourly, plan.daily = _agg_rows(agg_h), _agg_rows(agg_d)
    return plan


# --------------------------------------------------------------------------- #
# Ancienne tâche cron, archivage
# --------------------------------------------------------------------------- #
def find_legacy_cron(src=LEGACY_DIR):
    """Repère les lignes cron qui alimentent encore l'ancien dossier."""
    hits = []
    sources = ["/etc/crontab"]
    for d in ("/etc/cron.d", "/var/spool/cron/crontabs"):
        if os.path.isdir(d):
            try:
                sources += [os.path.join(d, n) for n in os.listdir(d)]
            except OSError:
                pass
    for path in sources:
        for line in (read(path) or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if os.path.basename(src) in s or "thermal_zone" in s:
                hits.append((path, s))
                continue
            for tok in s.split():
                if tok.startswith("/") and os.path.isfile(tok):
                    body = read(tok) or ""
                    if src in body or "thermal_zone0" in body:
                        hits.append((path, s))
                        break
    return hits


class ArchiveError(Exception):
    pass


def archive(src, archive_dir):
    """Archive src en .tar.xz dans archive_dir, vérifie l'archive, puis supprime src."""
    os.makedirs(archive_dir, exist_ok=True)
    dest = os.path.join(archive_dir, "temperatures-legacy-%s.tar.xz" % time.strftime("%Y%m%d-%H%M%S"))
    with tarfile.open(dest, "w:xz") as tar:
        tar.add(src, arcname=os.path.basename(src))
    with tarfile.open(dest, "r:xz") as tar:
        n_arch = sum(1 for m in tar.getmembers() if m.isfile())
    n_src = sum(len(fs) for _, _, fs in os.walk(src))
    if n_arch != n_src:
        raise ArchiveError("archive incomplète (%d/%d fichiers) : %s conservé" % (n_arch, n_src, src))
    os.chmod(dest, 0o600)
    shutil.rmtree(src)
    return dest
