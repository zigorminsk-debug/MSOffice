from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, url_for

from .core import CURRENCIES, KINDS, OWNERS, Ledger, format_money


def create_app(db_path: str | Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("BALANCE_SECRET", "local-balance-app")
    app.config["LEDGER"] = Ledger(db_path)
    app.jinja_env.filters["money"] = format_money

    @app.get("/")
    def index():
        ledger: Ledger = app.config["LEDGER"]
        return render_template(
            "index.html",
            entries=ledger.list(),
            summary=ledger.summary(),
            kinds=KINDS,
            owners=OWNERS,
            currencies=CURRENCIES,
            today=date.today().isoformat(),
        )

    @app.post("/entries")
    def add_entry():
        ledger: Ledger = app.config["LEDGER"]
        try:
            ledger.add(
                operation_date=request.form.get("operation_date", ""),
                kind=request.form.get("kind", ""),
                amount=request.form.get("amount", ""),
                sign=request.form.get("sign", ""),
                owner=request.form.get("owner", ""),
                currency=request.form.get("currency", "BYN"),
                fx_rate=request.form.get("fx_rate", ""),
                from_party=request.form.get("from_party", ""),
                to_party=request.form.get("to_party", ""),
                description=request.form.get("description", ""),
            )
            flash("Операция добавлена", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("index"))

    @app.post("/entries/<int:entry_id>/delete")
    def delete_entry(entry_id: int):
        ledger: Ledger = app.config["LEDGER"]
        if ledger.delete(entry_id):
            flash("Операция удалена", "success")
        return redirect(url_for("index"))

    @app.get("/export.csv")
    def export_csv():
        ledger: Ledger = app.config["LEDGER"]
        export_dir = Path(tempfile.gettempdir()) / "balance_exports"
        export_dir.mkdir(exist_ok=True)
        target = ledger.export_csv(export_dir / f"balance-{date.today().isoformat()}.csv")
        return send_file(target, as_attachment=True, download_name=target.name)

    @app.get("/help")
    def help_page():
        return render_template("help.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def run_web(db_path: str | Path | None = None, host: str = "127.0.0.1", port: int = 8080) -> None:
    create_app(db_path).run(host=host, port=port, debug=False)
