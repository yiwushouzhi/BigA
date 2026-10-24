from __future__ import annotations

import sys


def main() -> int:
    try:
        from .gui.app import run
    except ImportError as exc:
        print(f"无法启动图形界面: {exc}", file=sys.stderr)
        print("请先执行: pip install -r requirements.txt", file=sys.stderr)
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
