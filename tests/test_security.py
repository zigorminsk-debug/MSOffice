from finance_balance.security import SecurityConfig
from finance_balance.web import create_app


def test_password_hash_and_settings(tmp_path):
    config = SecurityConfig(tmp_path / "config.json")
    assert not config.configured
    config.set_password("secure-password")
    config.set_database(tmp_path / "shared" / "balance.db")
    config.set_network_access(True)

    loaded = SecurityConfig(config.path)
    assert loaded.configured
    assert loaded.verify("secure-password")
    assert not loaded.verify("wrong-password")
    assert loaded.network_access
    assert loaded.database_path.endswith("balance.db")
    raw = config.path.read_text(encoding="utf-8")
    assert "secure-password" not in raw


def test_web_requires_application_password(tmp_path):
    config = SecurityConfig(tmp_path / "config.json")
    config.set_password("correct-horse")
    app = create_app(tmp_path / "web.db", config)
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    wrong = client.post("/login", data={"password": "wrong"})
    assert "Неверный пароль".encode("utf-8") in wrong.data
    accepted = client.post("/login", data={"password": "correct-horse"}, follow_redirects=True)
    assert accepted.status_code == 200
    assert "Сводный баланс".encode("utf-8") in accepted.data
