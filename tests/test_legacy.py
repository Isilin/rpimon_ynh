import datetime as dt
import os
import time

import pytest

from rpimon import cli, legacy
from rpimon.store import Store, connect_ro
from rpimon.util import day_start


# --------------------------------------------------------------------------- #
# Décodage des valeurs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ip, fp, mode, expected", [
    ("44", "12", "milli", 44.012),     # bug $TEMP_INT.$TEMP_FLOAT : 44012 m°C écrit "44.12"
    ("44", "5", "milli", 44.005),
    ("44", "123", "milli", 44.123),
    ("44", "12", "plain", 44.12),
    ("44", "1234", "milli", 44.1234),  # plus de 3 décimales : forcément une vraie décimale
    ("44123", None, "plain", 44.123),  # milli-degrés bruts
    ("-5", "5", "plain", -5.5),
    ("200", None, "plain", None),      # hors plage
])
def test_decode_temp(ip, fp, mode, expected):
    v = legacy.decode_temp(ip, fp, mode)
    assert v == pytest.approx(expected) if expected is not None else v is None


def test_detect_decimal():
    assert legacy.detect_decimal({1: 3, 2: 40, 3: 400}, leading_zero=False) == "milli"
    assert legacy.detect_decimal({1: 3, 2: 40, 3: 400}, leading_zero=True) == "plain"
    assert legacy.detect_decimal({1: 100}, leading_zero=False) == "plain"


# --------------------------------------------------------------------------- #
# Lecture des fichiers
# --------------------------------------------------------------------------- #
def test_parse_formats(tmp_path):
    p = tmp_path / "01-02-2025.html"
    p.write_text(
        "<html><body><h1>Températures</h1>\n"
        "<p>08:00 44.12 °C</p>\n"
        "08:01:30 - 45,5°C<br>\n"
        "<tr><td>08:02</td><td>46123</td></tr>\n"
        "08:03\n47.25\n"               # heure et valeur sur deux lignes
        "Test\n</body></html>\n", encoding="utf-8")
    found, ignored, examples = legacy.parse_legacy(str(p))
    assert found == [(8, 0, 0, "44", "12"), (8, 1, 30, "45", "5"), (8, 2, 0, "46123", None),
                     (8, 3, 0, "47", "25")]
    assert "Test" in examples
    assert ignored == 2                 # titre + "Test"


def test_legacy_files(tmp_path):
    for name in ("02-01-2025.html", "2025-01-01.html", "31-02-2025.html", "notes.txt"):
        (tmp_path / name).write_text("x")
    (tmp_path / "05-01-2025.d").mkdir()
    files = legacy.legacy_files(str(tmp_path))
    assert [d for d, _ in files] == [dt.date(2025, 1, 1), dt.date(2025, 1, 2)]


# --------------------------------------------------------------------------- #
# Conversion et migration complète
# --------------------------------------------------------------------------- #
def write_day(folder, d, values, step_min=5):
    """Fichier au format de l'ancien script : une ligne par mesure."""
    lines = ["<html><body>"]
    for i, v in enumerate(values):
        t = i * step_min
        lines.append("<p>%02d:%02d - %s °C</p>" % (t // 60, t % 60, v))
    lines.append("</body></html>")
    (folder / ("%s.html" % d.strftime("%d-%m-%Y"))).write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def legacy_dir(tmp_path):
    folder = tmp_path / "temperatures"
    folder.mkdir()
    today = dt.date.today()
    # 3 jours anciens (agrégats) + aujourd'hui (brut) ; décimales de longueur variable
    for back in (30, 29, 28, 0):
        write_day(folder, today - dt.timedelta(days=back), ["44.12", "45.5", "46.123", "43.9"])
    return folder


def test_convert_splits_raw_and_aggregates(legacy_dir, cfg):
    a = legacy.analyze(str(legacy_dir))
    assert a.recognized == 16 and a.mode == "milli"
    now = time.time()
    plan = legacy.convert(a.files, a.mode, cfg, None, now)
    today = day_start(dt.date.today())
    assert [ts for ts, _ in plan.raw] == [today, today + 300, today + 600, today + 900]
    assert plan.raw[0][1] == pytest.approx(44.01)       # 44.012 arrondi à 2 décimales
    assert len(plan.daily) == 3
    assert all(n == 4 for _, n, *_ in plan.daily)
    ts, n, avg, mn, mx = plan.daily[0]
    assert (mn, mx) == (43.01, 46.12)                    # min/max arrondis à 2 décimales
    assert avg == pytest.approx((44.012 + 45.005 + 46.123 + 43.009) / 4, abs=1e-3)


def test_convert_skips_overlap_with_collector(legacy_dir, cfg):
    a = legacy.analyze(str(legacy_dir))
    first_collect = day_start(dt.date.today()) + 600
    plan = legacy.convert(a.files, a.mode, cfg, first_collect)
    assert plan.skipped_overlap == 2
    assert len(plan.raw) == 2


def test_migrate_end_to_end(legacy_dir, cfg_path, cfg, monkeypatch, capsys):
    monkeypatch.setattr(legacy, "find_legacy_cron", lambda src: [])
    store = Store(cfg.db)                # une mesure du collecteur, qui ne doit jamais être écrasée
    today = day_start(dt.date.today())
    store.insert([(today + 900, {"temp": 50.0, "cpu": 12.0})])
    store.close()

    assert cli.main(["-c", cfg_path, "migrate", str(legacy_dir)]) == 0
    assert "Dry-run" in capsys.readouterr().out
    con = connect_ro(cfg.db)
    assert con.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1     # rien d'écrit
    con.close()

    for _ in range(2):                   # ré-exécutable sans doublon
        assert cli.main(["-c", cfg_path, "migrate", str(legacy_dir), "--apply"]) == 0
    con = connect_ro(cfg.db)
    assert con.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 4
    assert con.execute("SELECT temp, cpu FROM samples WHERE ts = ?", (today + 900,)).fetchone() == (50.0, 12.0)
    assert con.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 4
    assert con.execute("SELECT v FROM meta WHERE k = 'migrated_from'").fetchone()[0] == str(legacy_dir)
    con.close()

    archives = os.path.join(os.path.dirname(cfg.db), "archives")
    assert cli.main(["-c", cfg_path, "migrate", str(legacy_dir), "--apply", "--purge"]) == 0
    assert not legacy_dir.exists()
    [archive] = os.listdir(archives)
    assert archive.startswith("temperatures-legacy-") and archive.endswith(".tar.xz")


def test_purge_refused_while_cron_active(legacy_dir, cfg_path, monkeypatch):
    monkeypatch.setattr(legacy, "find_legacy_cron", lambda src: [("/etc/crontab", "* * * * * root /x.sh")])
    assert cli.main(["-c", cfg_path, "migrate", str(legacy_dir), "--apply", "--purge"]) == 2
    assert legacy_dir.exists()


def test_purge_requires_apply(legacy_dir, cfg_path):
    with pytest.raises(SystemExit):
        cli.main(["-c", cfg_path, "migrate", str(legacy_dir), "--purge"])
