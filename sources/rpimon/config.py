"""Lecture du fichier de configuration (format INI)."""
from __future__ import annotations

import configparser
import os

DEFAULTS = {
    "storage": {"db": "/var/lib/rpimon/rpimon.db", "raw_days": "7",
                "hourly_days": "730", "daily_days": "0"},
    "collect": {"interval": "60", "disk": "auto", "net": "auto", "mount": "/"},
    "web": {"enabled": "yes", "listen": "127.0.0.1", "port": "8095"},
}


class Config:
    def __init__(self, path=None):
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cp.read_dict(DEFAULTS)
        self.path = path if path and os.path.exists(path) else None
        if self.path:
            cp.read(self.path, encoding="utf-8")
        s, c, w = cp["storage"], cp["collect"], cp["web"]
        self.db = s.get("db")
        self.raw_days = max(2, s.getint("raw_days"))
        self.hourly_days = max(0, s.getint("hourly_days"))
        self.daily_days = max(0, s.getint("daily_days"))
        self.interval = max(10, c.getint("interval"))
        self.disk = c.get("disk").strip() or "auto"
        self.net = c.get("net").strip() or "auto"
        self.mount = c.get("mount").strip() or "/"
        self.web = w.getboolean("enabled")
        self.listen = w.get("listen").strip()
        self.port = w.getint("port")
