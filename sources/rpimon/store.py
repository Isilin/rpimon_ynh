"""Stockage SQLite : mesures brutes, agrégats horaires et journaliers, métadonnées."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

from .metrics import AGG_COLS, AGGS, KEYS
from .util import local_midnight

# Seau horaire (UTC, la France a des décalages en heures entières) et
# seau journalier aligné sur minuit *local*.
BUCKETS = {
    "hourly": "(ts / 3600) * 3600",
    "daily": "CAST(strftime('%s', date(ts, 'unixepoch', 'localtime'), 'utc') AS INTEGER)",
}
TABLES = ("samples", "hourly", "daily")


class Store:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._schema()

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _schema(self):
        cols = ", ".join("%s REAL" % k for k in KEYS)
        agg = ", ".join("%s REAL" % c for c in AGG_COLS)
        with self.tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, %s)" % cols)
            for t in BUCKETS:
                db.execute("CREATE TABLE IF NOT EXISTS %s (ts INTEGER PRIMARY KEY, n INTEGER, %s)"
                           % (t, agg))
            db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            want = {"samples": KEYS, "hourly": AGG_COLS, "daily": AGG_COLS}
            for table, columns in want.items():
                have = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                for c in columns:
                    if c not in have:
                        db.execute("ALTER TABLE %s ADD COLUMN %s REAL" % (table, c))

    def meta_get(self, k):
        r = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return r[0] if r else None

    def meta_set(self, k, v, keep=False):
        verb = "INSERT OR IGNORE" if keep else "INSERT OR REPLACE"
        self.db.execute("%s INTO meta (k, v) VALUES (?, ?)" % verb, (k, str(v)))

    def insert(self, rows):
        sql = "INSERT OR IGNORE INTO samples (ts, %s) VALUES (?%s)" % (", ".join(KEYS), ", ?" * len(KEYS))
        with self.tx() as db:
            db.executemany(sql, [(ts,) + tuple(v.get(k) for k in KEYS) for ts, v in rows])

    def rollup(self, since):
        names = ", ".join(AGG_COLS)
        exprs = ", ".join("%s(%s)" % (a.upper(), k) for k in KEYS for a in AGGS)
        with self.tx() as db:
            for table, bucket in BUCKETS.items():
                if since <= 0:              # tout recalculer
                    start = 0
                else:
                    start = since - since % 3600 if table == "hourly" else local_midnight(since)
                db.execute(
                    "INSERT OR REPLACE INTO %s (ts, n, %s) SELECT %s AS b, COUNT(*), %s "
                    "FROM samples WHERE ts >= ? GROUP BY b" % (table, names, bucket, exprs),
                    (start,))

    def prune(self, cfg, now=None):
        now = time.time() if now is None else now
        with self.tx() as db:
            db.execute("DELETE FROM samples WHERE ts < ?", (int(now - cfg.raw_days * 86400),))
            if cfg.hourly_days:
                db.execute("DELETE FROM hourly WHERE ts < ?", (int(now - cfg.hourly_days * 86400),))
            if cfg.daily_days:
                db.execute("DELETE FROM daily WHERE ts < ?", (int(now - cfg.daily_days * 86400),))

    def import_legacy(self, raw, hourly, daily, source, now):
        """Écrit l'historique importé. Ré-exécutable : écrase les lignes importées, jamais
        une ligne écrite par le collecteur (reconnaissable à sa colonne cpu renseignée)."""
        with self.tx() as db:
            db.executemany("INSERT INTO samples (ts, temp) VALUES (?, ?) ON CONFLICT(ts) DO UPDATE "
                           "SET temp = excluded.temp WHERE samples.cpu IS NULL", raw)
            for table, rows in (("hourly", hourly), ("daily", daily)):
                db.executemany(
                    f"INSERT INTO {table} (ts, n, temp_avg, temp_min, temp_max) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(ts) DO UPDATE SET n = excluded.n, temp_avg = excluded.temp_avg, "
                    "temp_min = excluded.temp_min, temp_max = excluded.temp_max "
                    f"WHERE {table}.cpu_avg IS NULL", rows)
            db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('migrated_at', ?)", (str(int(now)),))
            db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('migrated_from', ?)", (source,))

    def close(self):
        self.db.close()


def connect_ro(path):
    con = sqlite3.connect(path, timeout=10)
    con.execute("PRAGMA query_only=1")
    return con


def db_files(path):
    return [p for p in (path, path + "-wal", path + "-shm") if os.path.exists(p)]
