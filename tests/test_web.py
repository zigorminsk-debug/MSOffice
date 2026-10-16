from finance_balance.web import create_app


def test_web_add_and_export(tmp_path):
    app = create_app(tmp_path / "web.db")
    app.config.update(TESTING=True)
    client = app.test_client()
    response = client.post("/entries", data={
        "operation_date": "2026-01-01", "kind": "credit", "amount": "1200,50",
        "sign": "1", "from_party": "Банк", "to_party": "Юра", "description": "Кредит"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "1\u00a0200,50".encode() in response.data
    assert client.get("/health").json == {"status": "ok"}
    exported = client.get("/export.csv")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["Content-Disposition"]
