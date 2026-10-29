import sqlite3
import time

import pytest

from rpimon.metrics import AGG_COLS, KEYS
from rpimon.store import Store


def test_rollup_and_prune(cfg):
    store = Store(cfg.db)
    base = 1_700_000_000 - 1_700_000_000 % 3600
    store.insert([(base + i * 60, {"temp": 40.0 + i, "cpu": 10.0}) for i in range(120)])
    store.rollup(0)
    rows = store.db.execute("SELECT ts, n, temp_avg, temp_min, temp_max FROM hourly ORDER BY ts").fetchall()
    assert rows == [(base, 60, pytest.approx(69.5), 40.0, 99.0),
                    (base + 3600, 60, pytest.approx(129.5), 100.0, 159.0)]
    assert store.db.execute("SELECT SUM(n) FROM daily").fetchone()[0] == 120

    store.prune(cfg, now=base + 120 * 60 + cfg.raw_days * 86400 - 1800)
    assert store.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 30
    store.close()


def test_insert_ignores_duplicates(cfg):
    store = Store(cfg.db)
    store.insert([(1000, {"temp": 40.0})])
    store.insert([(1000, {"temp": 99.0})])
    assert store.db.execute("SELECT temp FROM samples").fetchall() == [(40.0,)]
    store.close()


def test_schema_adds_missing_columns(cfg, tmp_path):
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, temp REAL)")
    con.execute("INSERT INTO samples VALUES (1, 42.0)")
    con.commit()
    con.close()
    store = Store(db)
    cols = {r[1] for r in store.db.execute("PRAGMA table_info(samples)")}
    assert set(KEYS) <= cols
    cols = {r[1] for r in store.db.execute("PRAGMA table_info(hourly)")}
    assert set(AGG_COLS) <= cols
    assert store.db.execute("SELECT temp FROM samples").fetchall() == [(42.0,)]
    store.close()


def test_meta(cfg):
    store = Store(cfg.db)
    store.meta_set("first_collect", 123, keep=True)
    store.meta_set("first_collect", 456, keep=True)
    assert store.meta_get("first_collect") == "123"
    assert store.meta_get("absent") is None
    store.close()


def test_import_never_overwrites_collector_rows(cfg):
    store = Store(cfg.db)
    now = time.time()
    store.insert([(1000, {"temp": 50.0, "cpu": 5.0})])
    store.import_legacy([(1000, 40.0), (1060, 41.0)], [], [], "/x", now)
    assert store.db.execute("SELECT ts, temp FROM samples ORDER BY ts").fetchall() == [(1000, 50.0), (1060, 41.0)]
    store.close()
