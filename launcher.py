"""Small Windows WebView launcher for the FITNES currency-balance screen."""

from __future__ import annotations

import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional


PREFERRED_PORT = 47831


def resource_directory() -> Path:
    """Return the folder containing the web assets in source and frozen builds."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "app"


class QuietAssetHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        # A windowed executable has no console; keep requests quiet.
        pass


def make_server(assets: Path) -> ThreadingHTTPServer:
    handler = lambda *args, **kwargs: QuietAssetHandler(  # noqa: E731
        *args, directory=str(assets), **kwargs
    )
    last_error: Optional[OSError] = None
    # Usually the first port is reused so browser localStorage persists between runs.
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 10):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError as error:
            last_error = error
    raise RuntimeError("Не удалось запустить локальный интерфейс. Освободите порт 47831–47840.") from last_error


def show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("FITNES — валютный баланс USD", message)
        root.destroy()
    except Exception:
        # If the system has no Tk runtime, fail without an additional traceback window.
        pass


def main() -> None:
    assets = resource_directory()
    if not (assets / "index.html").is_file():
        show_error("Не найдены файлы интерфейса. Переустановите или скачайте приложение заново.")
        return

    server: Optional[ThreadingHTTPServer] = None
    try:
        import webview

        server = make_server(assets)
        threading.Thread(target=server.serve_forever, name="fitnes-local-web", daemon=True).start()
        address = f"http://127.0.0.1:{server.server_port}/index.html"
        webview.create_window(
            "FITNES — валютный баланс USD",
            address,
            width=1260,
            height=820,
            min_size=(900, 620),
            background_color="#f1f3f1",
        )
        # On Windows pywebview uses the installed Microsoft Edge WebView2 runtime.
        webview.start(debug=False)
    except Exception as error:
        show_error(
            "Не удалось открыть окно приложения. Установите Microsoft Edge WebView2 Runtime "
            f"и попробуйте снова.\n\nПодробности: {error}"
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
