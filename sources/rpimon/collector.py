"""Collecte des mesures depuis /proc, /sys et vcgencmd."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time

from .metrics import DEC, KEYS
from .util import meminfo, read

log = logging.getLogger("rpimon")

NET_RE = re.compile(r"^(eth|en|wl|usb)")

# Bits de `vcgencmd get_throttled` : 0-3 = état actuel, 16-19 = « s'est produit depuis le boot »
UNDERVOLT_NOW, UNDERVOLT_BOOT = 0x1, 0x10000
THROTTLED_NOW, THROTTLED_BOOT = 0x2 | 0x4 | 0x8, 0x20000 | 0x40000 | 0x80000


class Collector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.vcgencmd = shutil.which("vcgencmd") or next(
            (p for p in ("/usr/bin/vcgencmd", "/opt/vc/bin/vcgencmd") if os.access(p, os.X_OK)), None)
        self._vc_warned = False
        self.disk = self._find_disk() if cfg.disk == "auto" else cfg.disk
        self.net = None if cfg.net == "auto" else set(cfg.net.replace(",", " ").split())
        self.prev = None
        self.flags = None        # dernier résultat brut de get_throttled
        self.flags_prev = None

    def _find_disk(self):
        try:
            st = os.stat(self.cfg.mount)
            p = os.path.realpath("/sys/dev/block/%d:%d" % (os.major(st.st_dev), os.minor(st.st_dev)))
            if os.path.exists(p):
                if os.path.exists(os.path.join(p, "partition")):
                    p = os.path.dirname(p)
                return os.path.basename(p)
        except (OSError, AttributeError):
            pass
        for name in ("mmcblk0", "sda", "nvme0n1"):
            if os.path.exists("/sys/block/" + name):
                return name
        return None

    def _vc(self, *args):
        if not self.vcgencmd:
            return None
        try:
            r = subprocess.run([self.vcgencmd] + list(args), capture_output=True,
                               text=True, timeout=5)
            if r.returncode == 0:
                return r.stdout.strip()
            if not self._vc_warned:
                log.warning("vcgencmd a échoué (%s) - l'utilisateur est-il dans le groupe video ?",
                            (r.stderr or r.stdout).strip()[:200])
                self._vc_warned = True
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _counters(self):
        c = {"t": time.monotonic()}
        for line in (read("/proc/stat") or "").splitlines():
            if line.startswith("cpu "):
                v = [int(x) for x in line.split()[1:9]]
                c["cpu_total"], c["cpu_idle"] = sum(v), v[3] + v[4]
            elif line.startswith("btime"):
                c["btime"] = int(line.split()[1])
        rx = tx = 0
        seen = False
        for line in (read("/proc/net/dev") or "").splitlines()[2:]:
            name, _, data = line.partition(":")
            name = name.strip()
            if (name in self.net) if self.net is not None else NET_RE.match(name):
                f = data.split()
                rx += int(f[0])
                tx += int(f[8])
                seen = True
        if seen:
            c["net_rx"], c["net_tx"] = rx, tx
        if self.disk:
            for line in (read("/proc/diskstats") or "").splitlines():
                f = line.split()
                if len(f) > 9 and f[2] == self.disk:
                    c["io_read"], c["io_write"] = int(f[5]) * 512, int(f[9]) * 512
                    break
        return c

    def _throttle_events(self, flags):
        """Un événement compte si l'état est actif maintenant OU si un bit « depuis le boot »
        vient d'apparaître (rattrape les chutes de tension brèves entre deux mesures)."""
        pf = self.flags_prev

        def event(now_bits, boot_bits):
            if flags & now_bits:
                return 1.0
            if pf is not None and (flags & boot_bits) & ~pf:
                return 1.0
            return 0.0

        self.flags_prev = self.flags = flags
        return event(UNDERVOLT_NOW, UNDERVOLT_BOOT), event(THROTTLED_NOW, THROTTLED_BOOT)

    def sample(self):
        now = self._counters()
        v = dict.fromkeys(KEYS)

        t = (read("/sys/class/thermal/thermal_zone0/temp") or "").strip()
        if t.lstrip("-").isdigit():
            v["temp"] = int(t) / 1000.0

        la = read("/proc/loadavg")
        if la:
            v["load1"] = float(la.split()[0])

        mi = meminfo()
        if mi.get("MemTotal") and "MemAvailable" in mi:
            v["mem"] = 100.0 * (1 - mi["MemAvailable"] / mi["MemTotal"])
        if mi.get("SwapTotal"):
            v["swap"] = 100.0 * (1 - mi.get("SwapFree", 0) / mi["SwapTotal"])

        try:
            st = os.statvfs(self.cfg.mount)
            used = (st.f_blocks - st.f_bfree) * st.f_frsize
            avail = st.f_bavail * st.f_frsize
            if used + avail > 0:
                v["disk"] = 100.0 * used / (used + avail)
                v["disk_free"] = avail / 1e9
        except (OSError, AttributeError):
            pass

        # fréquence réelle (tient compte du bridage firmware), sinon cpufreq
        out = self._vc("measure_clock", "arm")
        if out and "=" in out:
            try:
                v["freq"] = int(out.split("=")[1]) / 1e6
            except ValueError:
                pass
        if v["freq"] is None:
            f = (read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") or "").strip()
            if f.isdigit():
                v["freq"] = int(f) / 1000.0

        p = self.prev
        if p and p.get("btime") == now.get("btime"):
            elapsed = now["t"] - p["t"]
            if elapsed > 0:
                if "cpu_total" in now and "cpu_total" in p:
                    dtot = now["cpu_total"] - p["cpu_total"]
                    if dtot > 0:
                        v["cpu"] = 100.0 * (1 - (now["cpu_idle"] - p["cpu_idle"]) / dtot)
                for k in ("net_rx", "net_tx", "io_read", "io_write"):
                    if k in now and k in p and now[k] >= p[k]:
                        v[k] = (now[k] - p[k]) / elapsed / 1000.0
        self.prev = now

        out = self._vc("get_throttled")
        if out and "=" in out:
            try:
                flags = int(out.split("=")[1], 16)
            except ValueError:
                flags = None
            if flags is not None:
                v["undervolt"], v["throttled"] = self._throttle_events(flags)

        for k, x in v.items():
            if x is not None:
                v[k] = round(max(x, 0.0) if k != "temp" else x, DEC[k] + 1)
        return v
