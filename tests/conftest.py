import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sources"))

from rpimon.config import Config  # noqa: E402


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "rpimon.conf"
    p.write_text(
        "[storage]\ndb = %s\nraw_days = 7\nhourly_days = 730\ndaily_days = 0\n"
        "[collect]\ninterval = 60\n"
        "[web]\nlisten = 127.0.0.1\nport = 0\n" % (tmp_path / "data" / "rpimon.db").as_posix(),
        encoding="utf-8")
    return str(p)


@pytest.fixture
def cfg(cfg_path):
    return Config(cfg_path)
