"""argv dispatch. GTK is imported only for run."""

from __future__ import annotations

import sys


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        from winclip.daemon import main as run_main

        run_main()
    elif command == "toggle":
        from winclip.client import main as toggle_main

        toggle_main()
    elif command == "install":
        from winclip.install import main as install_main

        install_main()
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
