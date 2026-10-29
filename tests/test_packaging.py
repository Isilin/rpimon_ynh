"""Cohérence du paquet YunoHost (manifest, modèles de configuration, scripts)."""
import os
import re
import sys

import pytest

import rpimon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Variables disponibles dans les scripts au moment de ynh_config_add
KNOWN_VARS = {"app", "install_dir", "data_dir", "port", "path", "domain",
              "raw_days", "hourly_days", "daily_days", "interval"}


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")
def test_manifest_version_matches_code():
    import tomllib
    with open(os.path.join(ROOT, "manifest.toml"), "rb") as f:
        manifest = tomllib.load(f)
    assert manifest["version"].split("~ynh")[0] == rpimon.__version__
    assert manifest["id"] == "rpimon"


@pytest.mark.parametrize("name", sorted(os.listdir(os.path.join(ROOT, "conf"))))
def test_conf_templates_use_known_vars(name):
    with open(os.path.join(ROOT, "conf", name), encoding="utf-8") as f:
        text = f.read()
    used = {m.lower() for m in re.findall(r"__([A-Z0-9_]+?)__", text)}
    assert used <= KNOWN_VARS


def test_scripts_use_lf():
    for folder in ("scripts", "conf"):
        for name in os.listdir(os.path.join(ROOT, folder)):
            with open(os.path.join(ROOT, folder, name), "rb") as f:
                assert b"\r\n" not in f.read(), "%s/%s" % (folder, name)
