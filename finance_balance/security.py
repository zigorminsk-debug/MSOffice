from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

ITERATIONS = 600_000


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    return application_dir() / "balance-config.json"


class SecurityConfig:
    """Настройки доступа. Пароль хранится только как PBKDF2-хеш."""

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
        return self.data.get("database_path", str(application_dir() / "balance.db"))

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
        if len(password) < 6:
            raise ValueError("Пароль должен содержать не менее 6 символов")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
        self.data["password_salt"] = base64.b64encode(salt).decode("ascii")
        self.data["password_hash"] = base64.b64encode(digest).decode("ascii")
        if not self.data.get("session_secret"):
            self.data["session_secret"] = secrets.token_urlsafe(48)
        self.save()

    def verify(self, password: str) -> bool:
        if not self.configured:
            return False
        try:
            salt = base64.b64decode(self.data["password_salt"])
            expected = base64.b64decode(self.data["password_hash"])
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
            return hmac.compare_digest(actual, expected)
        except (ValueError, KeyError):
            return False

    def set_database(self, path: str | Path) -> None:
        value = str(path).strip()
        if not value:
            raise ValueError("Укажите путь к базе данных")
        self.data["database_path"] = value
        self.save()

    def set_network_access(self, enabled: bool) -> None:
        self.data["network_access"] = bool(enabled)
        self.save()
