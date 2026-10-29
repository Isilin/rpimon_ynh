import json
import time
import urllib.error
import urllib.request

import pytest

from rpimon.store import Store
from rpimon.web import Api, csv_number, start_web


@pytest.fixture
def filled(cfg):
    store = Store(cfg.db)
    now = int(time.time()) // 60 * 60
    rows = [(now - i * 60, {"temp": 45.0 + (i % 3), "cpu": 10.0, "net_rx": 1234567.891}) for i in range(180)]
    rows = [r for r in rows if not (now - 7200 <= r[0] < now - 6000)]     # un trou de 20 min
    store.insert(rows)
    store.rollup(0)
    store.close()
    return now


def test_series_raw_with_gap(cfg, filled):
    api = Api(cfg)
    r = api.series({"keys": ["temp"], "from": [str(filled - 3 * 3600)], "to": [str(filled)]})
    assert r["res"] == "raw" and r["step"] == 60
    vals = r["s"]["temp"]["avg"]
    assert len(vals) == len(r["t"])
    assert vals.count(None) == 1                     # le trou coupe la courbe une fois


def test_series_mixed_cadence_after_migration(cfg):
    """Historique importé à 5 min puis collecteur à la minute : seul le vrai trou coupe."""
    store = Store(cfg.db)
    now = int(time.time()) // 60 * 60
    start = now - 4 * 3600
    legacy = [(start + i * 300, {"temp": 45.0}) for i in range(24)]                  # 2 h à 5 min
    live = [(start + 7200 + i * 60, {"temp": 46.0, "cpu": 5.0}) for i in range(120)]  # 2 h à 1 min
    live = [r for r in live if not (now - 3600 <= r[0] < now - 2400)]                # trou de 20 min
    store.insert(legacy + live)
    store.close()
    r = Api(cfg).series({"keys": ["temp"], "from": [str(start)], "to": [str(now)]})
    assert r["res"] == "raw" and r["step"] == 60
    vals = r["s"]["temp"]["avg"]
    assert vals.count(None) == 1
    assert vals.index(None) > 24                     # pas dans la partie importée


def test_series_auto_picks_hourly_for_long_ranges(cfg, filled):
    api = Api(cfg)
    r = api.series({"keys": ["temp"], "from": [str(filled - 10 * 86400)], "to": [str(filled)]})
    assert r["res"] == "hourly"
    assert set(r["s"]["temp"]) == {"avg", "min", "max"}


@pytest.mark.parametrize("q", [{"keys": ["nope"]}, {"from": ["10"], "to": ["5"]}, {"res": ["weekly"]},
                               {"to": ["abc"]}])
def test_bad_params(cfg, filled, q):
    with pytest.raises(ValueError):
        Api(cfg).series(q)


def test_csv_number():
    assert csv_number(1234567.891, "net_rx") == "1234567,89"
    assert csv_number(45.0, "temp") == "45"
    assert csv_number(0.125, "load1") == "0,125"


def test_export_csv(cfg, filled):
    body, name = Api(cfg).export_csv({"keys": ["temp,net_rx"], "from": [str(filled - 600)], "to": [str(filled)]})
    text = body.decode("utf-8-sig")
    header, first = text.splitlines()[:2]
    assert header == "date;ts;temp;net_rx"
    assert first.endswith(";1234567,89") and "e+" not in text
    assert name.startswith("rpimon-raw-")


def test_http_routes(cfg, filled):
    srv = start_web(cfg, None)
    assert srv is not None
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()

        status, ctype, body = get("/rpimon/api/meta")     # préfixe toléré
        meta = json.loads(body)
        assert status == 200 and meta["ranges"]["raw"]["rows"] > 0
        assert get("/api/current")[0] == 200
        assert get("/")[1].startswith("text/html")
        assert get("/static/app.js")[1].startswith("text/javascript")
        with pytest.raises(urllib.error.HTTPError) as e:
            get("/static/../rpimon.conf")
        assert e.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as e:
            get("/api/series?keys=nope")
        assert e.value.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
