import json
import os

import pytest

from rpimon import cli, diskusage
from rpimon.config import Config
from rpimon.store import Store
from rpimon.web import Api


def write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    write(str(root / "home" / "yunohost.app" / "nextcloud" / "data" / "big.bin"), 600_000)
    write(str(root / "home" / "yunohost.app" / "vault" / "db.bin"), 150_000)
    write(str(root / "usr" / "lib" / "lib.bin"), 200_000)
    write(str(root / "var" / "log" / "syslog"), 40_000)
    write(str(root / "tiny" / "t.bin"), 1_000)
    return str(root)


def test_scan_sums_directories(tree):
    sizes, errors = diskusage.scan(tree)
    assert errors == 0
    j = os.path.join
    app = sizes[j(tree, "home", "yunohost.app")]
    assert app >= 750_000
    assert app == pytest.approx(sizes[j(tree, "home", "yunohost.app", "nextcloud")]
                                + sizes[j(tree, "home", "yunohost.app", "vault")], rel=0.05)
    assert sizes[tree] >= sum(sizes[j(tree, d)] for d in ("home", "usr", "var", "tiny"))


def test_scan_counts_hardlinks_once(tree):
    src = os.path.join(tree, "usr", "lib", "lib.bin")
    before, _ = diskusage.scan(tree)
    try:
        os.link(src, os.path.join(tree, "usr", "lib", "lib-link.bin"))
    except OSError:
        pytest.skip("liens physiques indisponibles")
    after, _ = diskusage.scan(tree)
    assert after[tree] - before[tree] < 50_000          # le contenu n'est pas compté deux fois


def test_scan_does_not_follow_symlinks(tree, tmp_path):
    outside = tmp_path / "outside"
    write(str(outside / "huge.bin"), 2_000_000)
    try:
        os.symlink(str(outside), os.path.join(tree, "lien"))
    except (OSError, NotImplementedError):
        pytest.skip("liens symboliques indisponibles")
    sizes, _ = diskusage.scan(tree)
    assert sizes[tree] < 1_500_000


def test_breakdown_drills_into_dominant_dirs(tree):
    sizes, _ = diskusage.scan(tree)
    items = diskusage.breakdown(sizes, tree, used=sizes[tree])
    paths = [it["path"] for it in items]
    j = os.path.join
    labels = {it["path"]: it["label"] for it in items}
    # /home (76 %) puis /home/yunohost.app sont détaillés jusqu'aux apps, qui restent entières
    assert labels[j(tree, "home", "yunohost.app", "nextcloud")] == "Données de l'app nextcloud"
    assert labels[j(tree, "home", "yunohost.app", "vault")] == "Données de l'app vault"
    assert labels[j(tree, "usr")] == "Système (programmes)"   # feuille : pas découpé en /usr/lib
    assert j(tree, "home") not in paths
    assert j(tree, "tiny") not in paths                 # < 2 % : dans « Autres »
    assert items[-1]["label"] == "Autres"
    assert sum(it["size"] for it in items) == pytest.approx(sizes[tree], rel=0.01)


def test_breakdown_from_filesystem_root():
    """Racine « / » : elle ne doit pas se compter parmi ses propres enfants."""
    root = os.path.abspath(os.sep)
    j = os.path.join
    sizes = {root: 1000, j(root, "home"): 800, j(root, "home", "yunohost.app"): 790,
             j(root, "home", "yunohost.app", "nextcloud"): 600, j(root, "home", "yunohost.app", "vault"): 190,
             j(root, "usr"): 200}
    for n in range(20):                                 # une vingtaine de petits dossiers, comme un vrai /
        sizes[j(root, "petit%d" % n)] = 0
    items = diskusage.breakdown(sizes, root, used=1000)
    assert [it["path"] for it in items] == [j(root, "home", "yunohost.app", "nextcloud"), j(root, "usr"),
                                            j(root, "home", "yunohost.app", "vault"), None]
    assert sum(it["size"] for it in items) == 1000


def test_breakdown_on_a_typical_yunohost_pi():
    """Arborescence calquée sur une vraie Pi : on veut les responsables, pas les conteneurs."""
    root = os.path.abspath(os.sep)
    tree = {(): 26.3, ("usr",): 5.79, ("home",): 8.64, ("home", "yunohost.backup"): 5.17,
            ("home", "yunohost.app"): 3.47, ("home", "yunohost.app", "nextcloud"): 2.4,
            ("home", "yunohost.app", "vaultwarden"): 0.6, ("opt",): 3.93, ("opt", "node_n"): 3.5,
            ("var",): 7.4, ("var", "lib"): 2.71, ("var", "lib", "fail2ban"): 1.3, ("var", "lib", "mongodb"): 0.8,
            ("var", "log"): 2.05, ("var", "log", "journal"): 1.7, ("var", "cache"): 1.26,
            ("var", "cache", "apt"): 1.2, ("var", "www"): 1.21, ("var", "www", "nextcloud"): 0.9}
    sizes = {os.path.join(root, *k): v * 1e9 for k, v in tree.items()}
    labels = [it["label"] for it in diskusage.breakdown(sizes, root, 26.7e9)]
    assert labels == ["Système (programmes)", "Sauvegardes YunoHost", "Node.js (versions installées)",
                      "Données de l'app nextcloud", "Journal systemd", "Base fail2ban",
                      "Cache apt (paquets téléchargés)", "Code de l'app nextcloud", "Autres"]


def test_breakdown_counts_unscanned_space_in_others():
    r = os.path.abspath("racine")
    sizes = {r: 100, os.path.join(r, "usr"): 60, os.path.join(r, "var"): 40}
    items = diskusage.breakdown(sizes, r, used=150)
    assert items[-1] == {"path": None, "size": 50, "label": "Autres"}


def test_describe():
    assert diskusage.describe("/home/yunohost.app/nextcloud") == ("Données de l'app nextcloud", True)
    assert diskusage.describe("/home/yunohost.app") == ("Données des apps", False)
    assert diskusage.describe("/home/yunohost.backup") == ("Sauvegardes YunoHost", True)
    assert diskusage.describe("/home/isilin") == ("Dossier de isilin", True)
    assert diskusage.describe("/var/lib/mysql") == ("Bases MariaDB", True)
    assert diskusage.describe("/srv/truc") == ("/srv/truc", False)


def test_disk_scan_command_and_api(tree, tmp_path, monkeypatch, capsys):
    # partition simulée : le dossier de test en occupe l'essentiel
    monkeypatch.setattr(diskusage.shutil, "disk_usage", lambda p: (10_000_000, 1_000_000, 8_500_000))
    conf = tmp_path / "disk.conf"
    db = (tmp_path / "data" / "rpimon.db").as_posix()
    conf.write_text("[storage]\ndb = %s\n[collect]\nmount = %s\n" % (db, tree.replace("\\", "/")),
                    encoding="utf-8")
    cfg = Config(str(conf))
    Store(cfg.db).close()
    assert Api(cfg).disk() == {"ts": None}

    assert cli.main(["-c", str(conf), "disk-scan"]) == 0
    assert "Données de l'app nextcloud" in capsys.readouterr().out
    snap = Api(cfg).disk()
    assert snap["used"] == 1_000_000 and snap["free"] == 8_500_000 and snap["reserved"] == 500_000
    assert any(it["path"] and it["path"].endswith("nextcloud") for it in snap["items"])
    assert json.dumps(snap)                              # servi tel quel par l'API
