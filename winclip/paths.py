"""State directory, database path, and daemon socket path."""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """$XDG_STATE_HOME/winclip, or ~/.local/state/winclip."""
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured) / "winclip"
    return Path.home() / ".local" / "state" / "winclip"


def history_path() -> Path:
    return state_dir() / "history.sqlite"


def socket_path() -> Path:
    """$XDG_RUNTIME_DIR/winclip.sock, or the state directory when that is unset."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "winclip.sock"
    return state_dir() / "winclip.sock"
