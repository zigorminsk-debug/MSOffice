from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ITERATIONS = 600_000


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    directory = application_dir()
    new_path = directory / "fitnes-config.json"
    old_path = directory / "balance-config.json"
    if not new_path.exists() and old_path.exists():
        try:
            os.replace(old_path, new_path)
        except OSError:
            return old_path
    return new_path


def _password_record(password: str) -> tuple[str, str]:
    if len(password) < 6:
        raise ValueError("Пароль должен содержать не менее 6 символов")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


def _verify_password(password: str, salt_text: str, digest_text: str) -> bool:
    try:
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
        return hmac.compare_digest(actual, expected)
    except (ValueError, KeyError):
        return False


class SecurityConfig:
    """Настройки Fitnes и список сохранённых баз."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_config_path()
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}
        # Миграция настроек предыдущей версии с одной базой.
        old_path = self.data.get("database_path")
        if "databases" not in self.data:
            legacy_db = application_dir() / "balance.db"
            default_db = legacy_db if legacy_db.exists() else application_dir() / "fitnes.db"
            path = old_path or str(default_db)
            self.data["databases"] = [{"name": Path(path).stem or "Основная", "path": path}]
            self.data["current_database"] = path

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    @property
    def configured(self) -> bool:
        return bool(self.data.get("password_hash") and self.data.get("password_salt"))

    @property
    def database_path(self) -> str:
        return self.data.get("current_database") or self.databases[0]["path"]

    @property
    def databases(self) -> list[dict[str, str]]:
        return list(self.data.get("databases", []))

    @property
    def network_access(self) -> bool:
        return bool(self.data.get("network_access", False))

    @property
    def session_secret(self) -> str:
        if not self.data.get("session_secret"):
            self.data["session_secret"] = secrets.token_urlsafe(48)
            self.save()
        return self.data["session_secret"]

    def set_password(self, password: str) -> None:
        salt, digest = _password_record(password)
        self.data["password_salt"] = salt
        self.data["password_hash"] = digest
        if not self.data.get("session_secret"):
            self.data["session_secret"] = secrets.token_urlsafe(48)
        self.save()

    def verify(self, password: str) -> bool:
        if not self.configured:
            return False
        return _verify_password(password, self.data["password_salt"], self.data["password_hash"])

    def set_database(self, path: str | Path, name: str | None = None) -> None:
        value = str(path).strip()
        if not value:
            raise ValueError("Укажите путь к базе данных")
        profiles = self.data.setdefault("databases", [])
        existing = next((item for item in profiles if os.path.normcase(item["path"]) == os.path.normcase(value)), None)
        if existing:
            if name:
                existing["name"] = name.strip()
        else:
            base_name = (name or Path(value).stem or "База").strip()
            used = {item["name"] for item in profiles}
            unique_name = base_name
            index = 2
            while unique_name in used:
                unique_name = f"{base_name} {index}"
                index += 1
            profiles.append({"name": unique_name, "path": value})
        self.data["current_database"] = value
        self.data["database_path"] = value  # совместимость со старой версией
        self.save()

    def remove_database(self, path: str | Path) -> None:
        value = str(path)
        profiles = [item for item in self.databases if os.path.normcase(item["path"]) != os.path.normcase(value)]
        if not profiles:
            raise ValueError("Нельзя удалить единственную базу из списка")
        self.data["databases"] = profiles
        if os.path.normcase(self.database_path) == os.path.normcase(value):
            self.data["current_database"] = profiles[0]["path"]
        self.save()

    def database_name(self, path: str | Path | None = None) -> str:
        target = os.path.normcase(str(path or self.database_path))
        item = next((item for item in self.databases if os.path.normcase(item["path"]) == target), None)
        return item["name"] if item else Path(str(path)).stem

    def set_network_access(self, enabled: bool) -> None:
        self.data["network_access"] = bool(enabled)
        self.save()


class DatabaseSecurity:
    """Отдельный пароль конкретной базы. Не является шифрованием SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS fitnes_security (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _get(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM fitnes_security WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    @property
    def configured(self) -> bool:
        return bool(self._get("password_salt") and self._get("password_hash"))

    def set_password(self, password: str) -> None:
        salt, digest = _password_record(password)
        with self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO fitnes_security(key, value) VALUES (?, ?)",
                [("password_salt", salt), ("password_hash", digest)],
            )

    def verify(self, password: str) -> bool:
        salt = self._get("password_salt")
        digest = self._get("password_hash")
        return bool(salt and digest and _verify_password(password, salt, digest))
