from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Сводный баланс Игоря и Юры")
    parser.add_argument("mode", nargs="?", choices=("gui", "web"), default="gui")
    parser.add_argument("--db", help="Путь к файлу базы данных")
    parser.add_argument("--host", default=os.environ.get("BALANCE_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("BALANCE_PORT", "8080")), type=int)
    args = parser.parse_args()
    if args.mode == "web":
        from .web import run_web
        run_web(args.db, args.host, args.port)
    else:
        from .gui import run_gui
        run_gui(args.db)


if __name__ == "__main__":
    main()
