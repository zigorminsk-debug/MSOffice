from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator

OWNERS = ("Игорь", "Юра")
KINDS = {
    "credit": ("Кредит", "Приход", 1),
    "income": ("Иное поступление", "Приход", 1),
    "interest": ("Проценты банка", "Расход", 1),
    "fine": ("Штраф", "Расход", 1),
    "leasing": ("Лизинг", "Расход", 1),
    "investment": ("Инвестиционные расходы", "Расход", 1),
    "repayment_bank": ("Возврат долга — перечисление", "Возврат", -1),
    "repayment_cash": ("Возврат долга — наличные", "Возврат", -1),
    "igor_personal": ("Личная покупка Игоря", "Личная покупка", -1),
    "adjustment": ("Корректировка", "Корректировка", 1),
}


def default_db_path() -> Path:
    return Path.home() / ".igor_yura_balance" / "balance.db"


def money_to_cents(value: str | int | Decimal) -> int:
    if isinstance(value, int):
        return value
    normalized = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(normalized).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Введите корректную сумму") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return int(amount * 100)


def format_money(cents: int) -> str:
    value = Decimal(cents) / 100
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


@dataclass(frozen=True)
class Entry:
    id: int
    operation_date: str
    kind: str
    amount_cents: int
    sign: int
    from_party: str
    to_party: str
    description: str
    created_at: str

    @property
    def effect_cents(self) -> int:
        return self.amount_cents * self.sign

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, (self.kind, "", 0))[0]

    @property
    def section(self) -> str:
        return KINDS.get(self.kind, ("", "Корректировка", 0))[1]


class Ledger:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_date TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                    sign INTEGER NOT NULL CHECK(sign IN (-1, 1)),
                    from_party TEXT NOT NULL DEFAULT '',
                    to_party TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(operation_date, id)")

    def add(
        self,
        *,
        operation_date: str,
        kind: str,
        amount: str | int | Decimal,
        sign: int | str,
        from_party: str = "",
        to_party: str = "",
        description: str = "",
    ) -> int:
        if kind not in KINDS:
            raise ValueError("Неизвестный вид операции")
        try:
            date.fromisoformat(operation_date)
        except ValueError as exc:
            raise ValueError("Некорректная дата") from exc
        sign_int = int(sign)
        if sign_int not in (-1, 1):
            raise ValueError("Знак должен быть + или −")
        cents = money_to_cents(amount) if not isinstance(amount, int) else amount
        if cents <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO entries
                (operation_date, kind, amount_cents, sign, from_party, to_party, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_date,
                    kind,
                    cents,
                    sign_int,
                    from_party.strip(),
                    to_party.strip(),
                    description.strip(),
                ),
            )
            return int(cursor.lastrowid)

    def delete(self, entry_id: int) -> bool:
        with self.connect() as db:
            result = db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            return result.rowcount > 0

    def list(self) -> list[Entry]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM entries ORDER BY operation_date DESC, id DESC"
            ).fetchall()
        return [Entry(**dict(row)) for row in rows]

    def summary(self) -> dict[str, int]:
        entries = self.list()
        debt = sum(item.effect_cents for item in entries)
        incoming = sum(item.amount_cents for item in entries if item.section == "Приход")
        expenses = sum(item.amount_cents for item in entries if item.section == "Расход")
        repayments = sum(-item.effect_cents for item in entries if item.effect_cents < 0)
        increases = sum(item.effect_cents for item in entries if item.effect_cents > 0)
        return {
            "debt": debt,
            "igor": debt,
            "yura": -debt,
            "incoming": incoming,
            "expenses": expenses,
            "repayments": repayments,
            "increases": increases,
        }

    def export_csv(self, destination: str | Path) -> Path:
        target = Path(destination)
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(
                ["Дата", "Раздел", "Операция", "Знак", "Сумма", "От кого", "Кому", "Пояснение", "Изменение долга"]
            )
            for item in reversed(self.list()):
                writer.writerow(
                    [
                        item.operation_date,
                        item.section,
                        item.kind_label,
                        "+" if item.sign > 0 else "−",
                        f"{Decimal(item.amount_cents) / 100:.2f}",
                        item.from_party,
                        item.to_party,
                        item.description,
                        f"{Decimal(item.effect_cents) / 100:.2f}",
                    ]
                )
        return target
