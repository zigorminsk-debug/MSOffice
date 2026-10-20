import io

from finance_balance.web import create_app


def test_web_add_and_export(tmp_path):
    app = create_app(tmp_path / "web.db")
    app.config.update(TESTING=True)
    client = app.test_client()
    response = client.post("/entries", data={
        "operation_date": "2026-01-01", "kind": "credit", "amount": "1200,50",
        "sign": "1", "owner": "Собственник 2", "currency": "BYN",
        "from_party": "Банк", "to_party": "Собственник 2", "description": "Кредит"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "1\u00a0200,50".encode() in response.data
    assert b'data-calc-byn="120050"' in response.data
    assert "Встроенный калькулятор".encode("utf-8") in response.data
    edit_page = client.get("/entries/1/edit")
    assert edit_page.status_code == 200
    edited = client.post("/entries/1/edit", data={
        "operation_date": "2026-01-02", "kind": "purchase", "amount": "900",
        "sign": "1", "owner": "Собственник 1", "currency": "BYN", "fx_rate": "",
        "reference_rate": "", "from_party": "Компания", "to_party": "Магазин",
        "description": "Исправлено",
    }, follow_redirects=True)
    assert "Операция обновлена".encode("utf-8") in edited.data
    assert "900,00".encode() in edited.data
    assert "Изменено:".encode("utf-8") in edited.data
    deposited = client.post("/entries", data={
        "operation_date": "2026-01-03", "kind": "loan_deposit", "amount": "5000",
        "sign": "1", "owner": "Собственник 2", "currency": "BYN", "deposit_source": "ООО Тест",
    }, follow_redirects=True)
    assert "На депозите от".encode("utf-8") in deposited.data
    assert "ООО Тест".encode("utf-8") in deposited.data
    assert client.get("/health").json == {"status": "ok"}
    help_page = client.get("/help")
    assert help_page.status_code == 200
    assert "Личная покупка собственника и налоги".encode("utf-8") in help_page.data
    imported = client.post("/import.csv", data={
        "csv_file": (io.BytesIO("Дата;Операция;Баланс;Знак;Сумма;Валюта\n2026-02-01;Штраф;Собственник 1;+;50;BYN\n".encode("utf-8")), "import.csv")
    }, content_type="multipart/form-data", follow_redirects=True)
    assert imported.status_code == 200
    assert "Импортировано операций: 1".encode("utf-8") in imported.data
    exported = client.get("/export.csv")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["Content-Disposition"]


def test_web_personal_purchase_tooltip_and_settings(tmp_path):
    app = create_app(tmp_path / "owners.db")
    app.config.update(TESTING=True)
    client = app.test_client()
    configured = client.post("/settings", data={
        "owner1": "Первый", "owner2": "Второй", "firms": "ООО Альфа\nООО Бета",
        "vat_rate": "20", "profit_tax_rate": "20",
    }, follow_redirects=True)
    assert "ООО Альфа".encode("utf-8") in configured.data
    result = client.post("/entries", data={
        "operation_date": "2026-10-20", "kind": "personal_purchase", "amount": "100",
        "sign": "1", "owner": "Второй", "currency": "BYN",
        "from_party": "ООО Альфа", "to_party": "Второй",
    }, follow_redirects=True)
    assert 'долг 66,66 Br'.encode("utf-8") in result.data
    assert b'class="tax-row"' in result.data
    assert "Второй должен Первый".encode("utf-8") in result.data
