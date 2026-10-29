"""Ligne de commande.

  rpimon run              daemon : collecte + interface web (lancé par systemd)
  rpimon sample           affiche une mesure instantanée (diagnostic)
  rpimon stats            état de la base : taille, plages couvertes, nb de points
  rpimon disk-scan        occupation du disque par dossier (service rpimon-disk, chaque nuit)
  rpimon migrate [DIR]    importe les anciens fichiers de température (dry-run par défaut)
  rpimon rollup           recalcule les agrégats et applique la rétention
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time

from . import __version__, diskusage, legacy
from .collector import Collector
from .config import Config
from .metrics import METRICS
from .store import TABLES, Store, connect_ro, db_files
from .util import decode_flags, fix_owner, fmt_bytes, fmt_int, fmt_ts, local_midnight, safe_since
from .web import start_web

DEFAULT_CONFIG = "/etc/rpimon.conf"
ROLLUP_EVERY = 600       # secondes entre deux recalculs d'agrégats
BACKLOG_MAX = 1440       # mesures gardées en mémoire si la base est indisponible

log = logging.getLogger("rpimon")


# --------------------------------------------------------------------------- #
# run (daemon)
# --------------------------------------------------------------------------- #
def cmd_run(cfg, args):
    store = Store(cfg.db)
    col = Collector(cfg)
    log.info("rpimon %s - base %s, mesure toutes les %ds, disque=%s, vcgencmd=%s",
             __version__, cfg.db, cfg.interval, col.disk or "-", col.vcgencmd or "absent")
    try:
        store.rollup(safe_since(cfg))
        store.prune(cfg)
    except sqlite3.Error as e:
        log.warning("agrégation initiale impossible : %s", e)

    srv = start_web(cfg, col) if cfg.web else None

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())

    col.sample()                     # amorce les compteurs (CPU, réseau, disque)
    pending, last_ts = [], None
    last_rollup = time.time()
    first_seen = store.meta_get("first_collect") is not None

    def flush():
        nonlocal pending, first_seen
        if not pending:
            return
        try:
            store.insert(pending)
            if not first_seen:
                store.meta_set("first_collect", pending[0][0], keep=True)
                first_seen = True
            pending = []
        except sqlite3.Error as e:
            log.warning("écriture impossible (%s) - %d mesure(s) gardée(s) en mémoire", e, len(pending))
            pending = pending[-BACKLOG_MAX:]

    while not stop.is_set():
        now = time.time()
        nxt = (int(now) // cfg.interval + 1) * cfg.interval   # mesures calées sur la minute
        if stop.wait(max(0.0, nxt - now)):
            break
        try:
            v = col.sample()
        except Exception:
            log.exception("échec de la mesure")
            continue
        if nxt == last_ts:
            continue
        last_ts = nxt
        pending.append((nxt, v))
        flush()
        if time.time() - last_rollup >= ROLLUP_EVERY:
            try:
                store.rollup(local_midnight(time.time() - 86400))
                store.prune(cfg)
                last_rollup = time.time()
            except sqlite3.Error as e:
                log.warning("agrégation impossible : %s", e)

    flush()
    try:
        store.rollup(local_midnight(time.time() - 86400))
    except sqlite3.Error:
        pass
    if srv:
        srv.shutdown()
    store.close()
    log.info("arrêt propre")
    return 0


# --------------------------------------------------------------------------- #
# sample / stats / rollup
# --------------------------------------------------------------------------- #
def cmd_sample(cfg, args):
    col = Collector(cfg)
    col.sample()
    time.sleep(1.0)
    v = col.sample()
    w = max(len(lb) for _, lb, _, _ in METRICS)
    for k, label, unit, d in METRICS:
        x = v.get(k)
        print("  %-*s  %s" % (w, label, "-" if x is None else ("%.*f %s" % (d, x, unit)).strip()))
    f = decode_flags(col.flags)
    print("  %-*s  %s" % (w, "get_throttled", f["raw"] if f else "indisponible"))
    print("  %-*s  %s" % (w, "disque suivi", col.disk or "-"))
    return 0


def cmd_stats(cfg, args):
    if not os.path.exists(cfg.db):
        print("Pas encore de base : %s" % cfg.db)
        return 1
    con = connect_ro(cfg.db)
    size = os.path.getsize(cfg.db)
    wal = os.path.getsize(cfg.db + "-wal") if os.path.exists(cfg.db + "-wal") else 0
    print("Base     %s  (%s, WAL %s)" % (cfg.db, fmt_bytes(size), fmt_bytes(wal)))
    print("Rétention  brut %d j · horaire %s · journalier %s" % (
        cfg.raw_days, "%d j" % cfg.hourly_days if cfg.hourly_days else "illimitée",
        "%d j" % cfg.daily_days if cfg.daily_days else "illimitée"))
    for t in TABLES:
        mn, mx, n = con.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM %s" % t).fetchone()
        print("  %-8s %10s lignes  %s → %s" % (t, fmt_int(n), fmt_ts(mn), fmt_ts(mx)))
    r = con.execute("SELECT ts, temp FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
    if r:
        print("Dernière mesure  il y a %d s%s" % (time.time() - r[0],
                                                  "  (%.1f °C)" % r[1] if r[1] is not None else ""))
    for k in ("first_collect", "migrated_at", "migrated_from"):
        r = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        if r:
            v = fmt_ts(int(float(r[0]))) if k != "migrated_from" else r[0]
            print("  %-14s %s" % (k, v))
    con.close()
    return 0


def cmd_disk_scan(cfg, args):
    snap = diskusage.snapshot(cfg.mount)
    store = Store(cfg.db)
    try:
        store.meta_set("disk_usage", json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
    finally:
        store.close()
    fix_owner(*db_files(cfg.db))
    print("Partition %s : %s utilisés sur %s, %s libres (scan en %.0f s%s)" % (
        snap["mount"], fmt_bytes(snap["used"]), fmt_bytes(snap["total"]), fmt_bytes(snap["free"]),
        snap["duration"], ", %s entrées illisibles" % fmt_int(snap["errors"]) if snap["errors"] else ""))
    w = max(len(it["label"]) for it in snap["items"])
    for it in snap["items"]:
        print("  %-*s  %9s  %4.1f %%  %s" % (w, it["label"], fmt_bytes(it["size"]),
                                          100.0 * it["size"] / max(snap["used"], 1), it["path"] or ""))
    return 0


def cmd_rollup(cfg, args):
    store = Store(cfg.db)
    since = 0 if args.all else safe_since(cfg)
    store.rollup(since)
    store.prune(cfg)
    if args.vacuum:
        store.db.execute("VACUUM")
    store.close()
    fix_owner(*db_files(cfg.db))
    print("Agrégats recalculés depuis %s, rétention appliquée%s." % (
        fmt_ts(since) if since else "le début", ", base compactée" if args.vacuum else ""))
    return 0


# --------------------------------------------------------------------------- #
# migrate
# --------------------------------------------------------------------------- #
def _first_collect(cfg):
    if not os.path.exists(cfg.db):
        return None
    try:
        con = connect_ro(cfg.db)
        try:
            r = con.execute("SELECT v FROM meta WHERE k = 'first_collect'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return int(float(r[0])) if r else None


def cmd_migrate(cfg, args):
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        print("Dossier introuvable : %s" % src)
        return 1
    a = legacy.analyze(src)
    if not a.files:
        print("Aucun fichier daté (JJ-MM-AAAA.html) dans %s" % src)
        return 1

    # Passe 1 : analyse
    mode = args.decimal if args.decimal != "auto" else a.mode
    med = a.per_day
    print("Dossier   %s - %s fichiers, %s" % (src, fmt_int(len(a.files)), fmt_bytes(a.total_size)))
    print("Mesures   %s reconnues, %s lignes ignorées" % (fmt_int(a.recognized), fmt_int(a.ignored)))
    if a.examples:
        print("          exemples ignorés : %s" % " | ".join(repr(e) for e in a.examples[:3]))
    print("Période   %s → %s, ~%s mesures/jour (cadence ≈ %s)" % (
        a.files[0][0], a.files[-1][0], fmt_int(int(med)), "%d min" % round(1440 / med) if med else "?"))
    print("Décimales %s" % ("« 44.12 » lu comme 44,012 °C (correction du bug $TEMP_INT.$TEMP_FLOAT)"
                           if mode == "milli" else "lecture décimale classique"))
    if a.recognized == 0:
        print("Rien à importer : le format des lignes n'est pas reconnu.")
        return 1

    # Passe 2 : conversion
    now = time.time()
    plan = legacy.convert(a.files, mode, cfg, _first_collect(cfg), now)
    if plan.vavg is not None:
        print("Valeurs   min %.1f · moy %.1f · max %.1f °C%s" % (
            plan.vmin, plan.vavg, plan.vmax,
            " (%s hors plage ignorées)" % fmt_int(plan.invalid) if plan.invalid else ""))
    if plan.skipped_overlap:
        print("Doublons  %s mesures postérieures au démarrage de rpimon, ignorées"
              % fmt_int(plan.skipped_overlap))
    print("Import    %s brutes (≥ %s), %s horaires, %s journalières" % (
        fmt_int(len(plan.raw)), fmt_ts(plan.cutoff_raw), fmt_int(len(plan.hourly)),
        fmt_int(len(plan.daily))))

    cron = legacy.find_legacy_cron(src)
    for path, line in cron:
        print("ATTENTION l'ancienne tâche cron est toujours active (%s) :\n          %s" % (path, line))

    if not args.apply:
        print("\nDry-run : rien n'a été écrit. Pour importer :\n"
              "  sudo rpimon migrate %s --apply            # import seul\n"
              "  sudo rpimon migrate %s --apply --purge    # import + archive .tar.xz + suppression"
              % (src, src))
        return 0

    store = Store(cfg.db)
    try:
        store.import_legacy(plan.raw, plan.hourly, plan.daily, src, now)
        if plan.raw:
            store.rollup(plan.cutoff_raw)   # fusionne l'historique récent avec les mesures du collecteur
        store.prune(cfg)
    finally:
        store.close()
    fix_owner(*db_files(cfg.db))
    print("OK        historique importé dans %s" % cfg.db)

    if args.purge:
        if cron and not args.ignore_cron:
            print("Suppression annulée : désactive d'abord l'ancienne tâche cron (sinon le dossier revient).")
            return 2
        archive_dir = args.archive_dir or os.path.join(os.path.dirname(os.path.abspath(cfg.db)), "archives")
        try:
            dest = legacy.archive(src, archive_dir)
        except legacy.ArchiveError as e:
            print("Échec     %s" % e)
            return 2
        fix_owner(archive_dir)
        print("Archive   %s (%s) - dossier %s supprimé (%s libérés)" % (
            dest, fmt_bytes(os.path.getsize(dest)), src, fmt_bytes(a.total_size)))
    return 0


# --------------------------------------------------------------------------- #
def build_parser():
    ap = argparse.ArgumentParser(prog="rpimon", description="Monitoring léger pour Raspberry Pi")
    ap.add_argument("-c", "--config", default=os.environ.get("RPIMON_CONFIG", DEFAULT_CONFIG))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version="rpimon " + __version__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="daemon : collecte + interface web")
    sub.add_parser("sample", help="affiche une mesure instantanée")
    sub.add_parser("stats", help="état de la base")
    sub.add_parser("disk-scan", help="mesure l'occupation du disque par dossier (à lancer en root)")
    r = sub.add_parser("rollup", help="recalcule les agrégats + rétention")
    r.add_argument("--all", action="store_true", help="tout recalculer depuis les données brutes")
    r.add_argument("--vacuum", action="store_true", help="compacter la base ensuite")
    m = sub.add_parser("migrate", help="importe les anciens fichiers de température")
    m.add_argument("dir", nargs="?", default=legacy.LEGACY_DIR)
    m.add_argument("--apply", action="store_true", help="écrire dans la base (sinon : dry-run)")
    m.add_argument("--purge", action="store_true",
                   help="après import : archiver en .tar.xz puis supprimer le dossier")
    m.add_argument("--archive-dir", default=None,
                   help="dossier de l'archive (défaut : archives/ à côté de la base)")
    m.add_argument("--ignore-cron", action="store_true",
                   help="supprimer même si une tâche cron semble encore écrire dans le dossier")
    m.add_argument("--decimal", choices=("auto", "milli", "plain"), default="auto",
                   help="lecture des décimales (auto = détection du bug $TEMP_INT.$TEMP_FLOAT)")
    return ap


COMMANDS = {"run": cmd_run, "sample": cmd_sample, "stats": cmd_stats, "disk-scan": cmd_disk_scan,
            "rollup": cmd_rollup, "migrate": cmd_migrate}


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    if args.cmd is None:
        ap.print_help()
        return 1
    if getattr(args, "purge", False) and not args.apply:
        ap.error("--purge nécessite --apply")
    return COMMANDS[args.cmd](Config(args.config), args)


if __name__ == "__main__":
    sys.exit(main())
