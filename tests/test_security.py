from finance_balance.security import DatabaseSecurity, SecurityConfig
from finance_balance.web import create_app


def test_password_hash_and_database_profiles(tmp_path):
    config = SecurityConfig(tmp_path / "config.json")
    assert not config.configured
    config.set_password("secure-password")
    first = tmp_path / "shared" / "igor.db"
    second = tmp_path / "shared" / "yura.db"
    config.set_database(first, "Игорь")
    config.set_database(second, "Юра")
    config.set_network_access(True)

    loaded = SecurityConfig(config.path)
    assert loaded.configured
    assert loaded.verify("secure-password")
    assert not loaded.verify("wrong-password")
    assert loaded.network_access
    assert loaded.database_path == str(second)
    assert {item["name"] for item in loaded.databases} >= {"Игорь", "Юра"}
    loaded.set_database(first)
    assert loaded.database_path == str(first)
    raw = config.path.read_text(encoding="utf-8")
    assert "secure-password" not in raw


def test_database_has_separate_password(tmp_path):
    access = DatabaseSecurity(tmp_path / "fitnes.db")
    assert not access.configured
    access.set_password("database-secret")
    assert access.configured
    assert access.verify("database-secret")
    assert not access.verify("application-secret")


def test_web_requires_both_passwords(tmp_path):
    config = SecurityConfig(tmp_path / "config.json")
    config.set_password("correct-horse")
    database = DatabaseSecurity(tmp_path / "web.db")
    database.set_password("database-horse")
    app = create_app(database.db_path, config, database)
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    wrong = client.post("/login", data={"password": "correct-horse", "database_password": "wrong"})
    assert "Неверный пароль".encode("utf-8") in wrong.data
    accepted = client.post("/login", data={"password": "correct-horse", "database_password": "database-horse"}, follow_redirects=True)
    assert accepted.status_code == 200
    assert "Fitnes".encode("utf-8") in accepted.data
