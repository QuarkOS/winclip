"""Ask the daemon to show or hide the panel."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

from winclip.paths import socket_path
from winclip.wire import Toggle, encode


def main() -> None:
    path = socket_path()
    started = False
    for _attempt in range(20):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.2)
            sock.connect(str(path))
            sock.sendall(encode(Toggle()))
            sock.shutdown(socket.SHUT_WR)
            sock.close()
            return
        except OSError:
            if not started:
                started = True
                _ensure_daemon()
            time.sleep(0.05)
    raise SystemExit(1)


def _ensure_daemon() -> None:
    completed = subprocess.run(
        ["systemctl", "--user", "start", "winclip.service"],
        check=False,
    )
    if completed.returncode == 0:
        return
    subprocess.Popen(
        [sys.executable, "-m", "winclip", "run"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
