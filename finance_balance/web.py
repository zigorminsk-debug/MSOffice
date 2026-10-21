from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for

from .core import CURRENCIES, KINDS, Ledger, format_money
from .help_content import FEATURE_SECTIONS
from .grammar import to_dative
from .security import DatabaseSecurity, SecurityConfig


def create_app(
    db_path: str | Path | None = None,
    security: SecurityConfig | None = None,
    database_security: DatabaseSecurity | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("BALANCE_SECRET", security.session_secret if security else "local-balance-app")
    app.config["LEDGER"] = Ledger(db_path)
    app.config["SECURITY"] = security
    app.config["DATABASE_SECURITY"] = database_security
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["dative"] = to_dative

    @app.before_request
    def require_password():
        access: SecurityConfig | None = app.config["SECURITY"]
        if not access or not access.configured:
            return None
        if request.endpoint in {"login", "static", "health"}:
            return None
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        access: SecurityConfig | None = app.config["SECURITY"]
        if not access or not access.configured:
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            app_ok = access.verify(request.form.get("password", ""))
            db_access: DatabaseSecurity | None = app.config["DATABASE_SECURITY"]
            db_ok = not db_access or not db_access.configured or db_access.verify(request.form.get("database_password", ""))
            if app_ok and db_ok:
                session.clear()
                session["authenticated"] = True
                destination = request.args.get("next", "")
                return redirect(destination if destination.startswith("/") else url_for("index"))
            error = "Неверный пароль Fitnes или пароль базы"
        return render_template("login.html", error=error, require_database_password=bool(app.config["DATABASE_SECURITY"]))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        ledger: Ledger = app.config["LEDGER"]
        return render_template(
            "index.html",
            entries=ledger.list(),
            summary=ledger.summary(),
            deposits=ledger.deposit_balances(),
            kinds=KINDS,
            owners=ledger.owners,
            parties=ledger.parties,
            settings=ledger.settings(),
            currencies=CURRENCIES,
            protected=security is not None and security.configured,
            active_database=security.database_name(db_path) if security else Path(str(db_path or "fitnes.db")).stem,
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
                reference_rate=request.form.get("reference_rate", ""),
                deposit_source=request.form.get("deposit_source", ""),
                repayment_method=request.form.get("repayment_method", ""),
                from_party=request.form.get("from_party", ""),
                to_party=request.form.get("to_party", ""),
                description=request.form.get("description", ""),
            )
            flash("Операция добавлена", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("index"))

    @app.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
    def edit_entry(entry_id: int):
        ledger: Ledger = app.config["LEDGER"]
        entry = ledger.get(entry_id)
        if not entry:
            flash("Операция не найдена", "error")
            return redirect(url_for("index"))
        if request.method == "POST":
            try:
                ledger.update(
                    entry_id, operation_date=request.form.get("operation_date", ""),
                    kind=request.form.get("kind", ""), amount=request.form.get("amount", ""),
                    sign=request.form.get("sign", ""), owner=request.form.get("owner", ""),
                    currency=request.form.get("currency", "BYN"), fx_rate=request.form.get("fx_rate", ""),
                    reference_rate=request.form.get("reference_rate", ""),
                    deposit_source=request.form.get("deposit_source", ""),
                    repayment_method=request.form.get("repayment_method", ""),
                    from_party=request.form.get("from_party", ""), to_party=request.form.get("to_party", ""),
                    description=request.form.get("description", ""),
                )
                flash("Операция обновлена", "success")
                return redirect(url_for("index"))
            except ValueError as error:
                flash(str(error), "error")
        return render_template("edit.html", entry=entry, kinds=KINDS, owners=ledger.owners, parties=ledger.parties, currencies=CURRENCIES)

    @app.post("/entries/<int:entry_id>/delete")
    def delete_entry(entry_id: int):
        ledger: Ledger = app.config["LEDGER"]
        if ledger.delete(entry_id):
            flash("Операция удалена", "success")
        return redirect(url_for("index"))

    @app.post("/import.csv")
    def import_csv():
        ledger: Ledger = app.config["LEDGER"]
        uploaded = request.files.get("csv_file")
        if not uploaded or not uploaded.filename:
            flash("Выберите CSV-файл", "error")
            return redirect(url_for("index"))
        try:
            count = ledger.import_csv_bytes(uploaded.read())
            flash(f"Импортировано операций: {count}", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("index"))

    @app.get("/export.csv")
    def export_csv():
        ledger: Ledger = app.config["LEDGER"]
        export_dir = Path(tempfile.gettempdir()) / "balance_exports"
        export_dir.mkdir(exist_ok=True)
        target = ledger.export_csv(export_dir / f"fitnes-{date.today().isoformat()}.csv")
        return send_file(target, as_attachment=True, download_name=target.name)

    @app.get("/currency")
    def currency_page():
        ledger: Ledger = app.config["LEDGER"]
        return render_template("currency.html", trades=ledger.list_currency_trades(),
                               summary=ledger.currency_summary(), owners=ledger.owners,
                               today=date.today().isoformat())

    @app.post("/currency/trades")
    def add_currency_trade():
        ledger: Ledger = app.config["LEDGER"]
        try:
            ledger.add_currency_trade(
                operation_date=request.form.get("operation_date", ""),
                owner=request.form.get("owner", ""), kind=request.form.get("kind", ""),
                usd_amount=request.form.get("usd_amount", ""), rate=request.form.get("rate", ""),
            )
            flash("Валютная операция добавлена", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("currency_page"))

    @app.post("/currency/trades/<int:trade_id>/transfer")
    def transfer_currency_loss(trade_id: int):
        ledger: Ledger = app.config["LEDGER"]
        try:
            ledger.transfer_currency_loss(trade_id)
            flash("Курсовая потеря добавлена в общий расчёт", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("currency_page"))

    @app.post("/currency/trades/<int:trade_id>/delete")
    def delete_currency_trade(trade_id: int):
        ledger: Ledger = app.config["LEDGER"]
        try:
            ledger.delete_currency_trade(trade_id)
            flash("Валютная операция удалена", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("currency_page"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        ledger: Ledger = app.config["LEDGER"]
        if request.method == "POST":
            try:
                ledger.set_settings(
                    request.form.get("owner1", ""), request.form.get("owner2", ""),
                    request.form.get("firms", "").splitlines(), request.form.get("vat_rate", ""),
                    request.form.get("profit_tax_rate", ""),
                )
                flash("Настройки сохранены", "success")
                return redirect(url_for("index"))
            except ValueError as error:
                flash(str(error), "error")
        return render_template("settings.html", settings=ledger.settings())

    @app.get("/help")
    def help_page():
        return render_template("help.html", sections=FEATURE_SECTIONS)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def run_web(db_path: str | Path | None = None, host: str = "127.0.0.1", port: int = 8080) -> None:
    security = SecurityConfig()
    if not security.configured:
        raise SystemExit("Сначала запустите GUI и создайте пароль при первом запуске.")
    selected_db = db_path or security.database_path
    create_app(selected_db, security, DatabaseSecurity(selected_db)).run(host=host, port=port, debug=False)
