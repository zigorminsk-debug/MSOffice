from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator

OWNERS = ("Игорь", "Юра")
CURRENCIES = ("BYN", "USD")
# Код: название, раздел, рекомендуемый знак, режим расчёта.
KINDS = {
    "credit": ("Кредит", "Приход", 1, "debt"),
    "income": ("Иное поступление", "Приход", 1, "debt"),
    "purchase": ("Покупка товаров", "Расход", 1, "debt"),
    "cash_withdrawal": ("Снятие наличных", "Расход", 1, "debt"),
    "interest": ("Проценты банка", "Расход", 1, "debt"),
    "fine": ("Штраф", "Расход", 1, "debt"),
    "leasing": ("Лизинг", "Расход", 1, "debt"),
    "investment": ("Инвестиционные расходы", "Расход", 1, "debt"),
    "repayment_bank": ("Возврат долга — перечисление", "Возврат", -1, "debt"),
    "repayment_cash": ("Возврат долга — наличные", "Возврат", -1, "debt"),
    "personal_purchase": ("Личная покупка", "Личная покупка", 1, "debt"),
    "loan_deposit": ("Выдан займ (зачислить на депозит)", "Займы", 1, "deposit_add"),
    "loan_use": ("Перечислено из займа / снято", "Займы", -1, "deposit_use"),
    "adjustment": ("Корректировка задолженности", "Корректировка", 1, "debt"),
}


def default_db_path() -> Path:
    """Хранит базу рядом с Fitnes и сохраняет совместимость с balance.db."""
    directory = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    legacy = directory / "balance.db"
    return legacy if legacy.exists() else directory / "fitnes.db"


def decimal_value(value: str | int | Decimal) -> Decimal:
    normalized = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Введите корректное число") from exc
    if result <= 0:
        raise ValueError("Значение должно быть больше нуля")
    return result


def money_to_cents(value: str | int | Decimal) -> int:
    if isinstance(value, int):
        return value
    amount = decimal_value(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount * 100)


def format_number(cents: int) -> str:
    value = Decimal(cents) / 100
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def format_money(cents: int, currency: str = "BYN") -> str:
    symbol = "Br" if currency == "BYN" else "$"
    return f"{format_number(cents)} {symbol}"


class NBRBRateProvider:
    """Получение официального курса НБРБ с api.nbrb.by."""

    endpoint = "https://api.nbrb.by/exrates/rates/USD"

    def get_usd_rate(self, operation_date: str) -> Decimal:
        try:
            date.fromisoformat(operation_date)
        except ValueError as exc:
            raise ValueError("Некорректная дата для получения курса") from exc
        query = urllib.parse.urlencode({"parammode": "2", "ondate": operation_date})
        request = urllib.request.Request(
            f"{self.endpoint}?{query}",
            headers={"User-Agent": "Fitnes/2.0", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)
            rate = Decimal(str(payload["Cur_OfficialRate"])) / Decimal(str(payload.get("Cur_Scale", 1)))
        except Exception as exc:
            raise ValueError(
                "Не удалось получить курс USD с сайта НБРБ. Проверьте интернет или укажите курс вручную."
            ) from exc
        if rate <= 0:
            raise ValueError("НБРБ вернул некорректный курс")
        return rate


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
    owner: str = "Юра"
    currency: str = "BYN"
    fx_rate: str = "1"
    amount_byn_cents: int = 0

    @property
    def mode(self) -> str:
        return KINDS.get(self.kind, ("", "", 0, "debt"))[3]

    @property
    def effect_cents(self) -> int:
        if self.mode != "debt":
            return 0
        owner_direction = 1 if self.owner == "Юра" else -1
        return self.byn_cents * self.sign * owner_direction

    @property
    def deposit_effect_cents(self) -> int:
        if self.mode == "deposit_add":
            return self.byn_cents
        if self.mode == "deposit_use":
            return -self.byn_cents
        return 0

    @property
    def byn_cents(self) -> int:
        return self.amount_byn_cents or self.amount_cents

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, (self.kind, "", 0, "debt"))[0]

    @property
    def section(self) -> str:
        return KINDS.get(self.kind, ("", "Корректировка", 0, "debt"))[1]

    @property
    def original_money(self) -> str:
        return format_money(self.amount_cents, self.currency)


class Ledger:
    def __init__(self, db_path: str | Path | None = None, rate_provider: NBRBRateProvider | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.rate_provider = rate_provider or NBRBRateProvider()
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
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
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    owner TEXT NOT NULL DEFAULT 'Юра',
                    currency TEXT NOT NULL DEFAULT 'BYN',
                    fx_rate TEXT NOT NULL DEFAULT '1',
                    amount_byn_cents INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            existing = {row[1] for row in db.execute("PRAGMA table_info(entries)")}
            migrations = {
                "owner": "ALTER TABLE entries ADD COLUMN owner TEXT NOT NULL DEFAULT 'Юра'",
                "currency": "ALTER TABLE entries ADD COLUMN currency TEXT NOT NULL DEFAULT 'BYN'",
                "fx_rate": "ALTER TABLE entries ADD COLUMN fx_rate TEXT NOT NULL DEFAULT '1'",
                "amount_byn_cents": "ALTER TABLE entries ADD COLUMN amount_byn_cents INTEGER NOT NULL DEFAULT 0",
            }
            for column, sql in migrations.items():
                if column not in existing:
                    db.execute(sql)
            db.execute("UPDATE entries SET amount_byn_cents = amount_cents WHERE amount_byn_cents = 0")
            db.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(operation_date, id)")

    def add(
        self,
        *,
        operation_date: str,
        kind: str,
        amount: str | int | Decimal,
        sign: int | str,
        owner: str = "Юра",
        currency: str = "BYN",
        fx_rate: str | Decimal | None = None,
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
        if owner not in OWNERS:
            raise ValueError("Выберите, на чей баланс относится сумма")
        if currency not in CURRENCIES:
            raise ValueError("Поддерживаются только BYN и USD")
        sign_int = int(sign)
        if sign_int not in (-1, 1):
            raise ValueError("Знак должен быть + или −")
        cents = money_to_cents(amount) if not isinstance(amount, int) else amount
        if cents <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        if currency == "USD":
            rate = decimal_value(fx_rate) if fx_rate and str(fx_rate).strip() else self.rate_provider.get_usd_rate(operation_date)
        else:
            rate = Decimal("1")
        byn_cents = int((Decimal(cents) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO entries
                (operation_date, kind, amount_cents, sign, from_party, to_party, description,
                 owner, currency, fx_rate, amount_byn_cents)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (operation_date, kind, cents, sign_int, from_party.strip(), to_party.strip(),
                 description.strip(), owner, currency, str(rate), byn_cents),
            )
            return int(cursor.lastrowid)

    def delete(self, entry_id: int) -> bool:
        with self.connect() as db:
            result = db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            return result.rowcount > 0

    def list(self) -> list[Entry]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM entries ORDER BY operation_date DESC, id DESC").fetchall()
        return [Entry(**dict(row)) for row in rows]

    def summary(self) -> dict[str, int]:
        entries = self.list()
        debt = sum(item.effect_cents for item in entries)
        return {
            "debt": debt,
            "igor": debt,
            "yura": -debt,
            "incoming": sum(item.byn_cents for item in entries if item.section == "Приход"),
            "expenses": sum(item.byn_cents for item in entries if item.section == "Расход"),
            "repayments": sum(item.byn_cents for item in entries if item.mode == "debt" and item.sign < 0),
            "increases": sum(item.byn_cents for item in entries if item.mode == "debt" and item.sign > 0),
            "igor_deposit": sum(item.deposit_effect_cents for item in entries if item.owner == "Игорь"),
            "yura_deposit": sum(item.deposit_effect_cents for item in entries if item.owner == "Юра"),
        }

    def import_csv(self, source: str | Path) -> int:
        raw = Path(source).read_bytes()
        return self.import_csv_bytes(raw)

    def import_csv_bytes(self, raw: bytes) -> int:
        """Импортирует CSV целиком или не добавляет ни одной строки при ошибке."""
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("cp1251")
            except UnicodeDecodeError as exc:
                raise ValueError("CSV должен быть в кодировке UTF-8 или Windows-1251") from exc
        if not text.strip():
            raise ValueError("CSV-файл пуст")
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";"
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("В CSV отсутствует строка заголовков")

        def normalized(value: str) -> str:
            return value.strip().lower().replace("ё", "е")

        headers = {normalized(name): name for name in reader.fieldnames if name}

        def value(row: dict, *aliases: str, required: bool = False) -> str:
            for alias in aliases:
                original = headers.get(normalized(alias))
                if original is not None:
                    result = (row.get(original) or "").strip()
                    if result or not required:
                        return result
            if required:
                raise ValueError(f"отсутствует обязательное поле «{aliases[0]}»")
            return ""

        kind_by_label = {normalized(item[0]): code for code, item in KINDS.items()}
        prepared: list[tuple] = []
        errors: list[str] = []
        for line_number, row in enumerate(reader, start=2):
            if not any((cell or "").strip() for cell in row.values()):
                continue
            try:
                raw_date = value(row, "Дата", "Date", required=True)
                try:
                    operation_date = date.fromisoformat(raw_date).isoformat()
                except ValueError:
                    operation_date = datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
                raw_kind = value(row, "Операция", "Вид операции", "Kind", required=True)
                kind = raw_kind if raw_kind in KINDS else kind_by_label.get(normalized(raw_kind), "")
                if not kind:
                    raise ValueError(f"неизвестная операция «{raw_kind}»")
                owner = value(row, "Баланс", "На чей баланс", "Владелец", "Owner", required=True).capitalize()
                if owner not in OWNERS:
                    raise ValueError("баланс должен быть «Игорь» или «Юра»")
                raw_sign = value(row, "Знак", "Действие", "Sign", required=True).replace("−", "-")
                if raw_sign.strip().lower() in {"+", "+1", "1", "плюс"}:
                    sign = 1
                elif raw_sign.strip().lower() in {"-", "-1", "минус"}:
                    sign = -1
                else:
                    raise ValueError("знак должен быть + или −")
                amount_cents = money_to_cents(value(row, "Сумма", "Amount", required=True))
                currency = (value(row, "Валюта", "Currency") or "BYN").upper()
                if currency not in CURRENCIES:
                    raise ValueError("валюта должна быть BYN или USD")
                raw_rate = value(row, "Курс НБРБ", "Курс", "Rate")
                if currency == "USD":
                    rate = decimal_value(raw_rate) if raw_rate else self.rate_provider.get_usd_rate(operation_date)
                else:
                    rate = Decimal("1")
                byn_cents = int((Decimal(amount_cents) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                prepared.append((
                    operation_date, kind, amount_cents, sign,
                    value(row, "От кого", "From"), value(row, "Кому", "To"),
                    value(row, "Пояснение", "Описание", "Description"), owner, currency,
                    str(rate), byn_cents,
                ))
            except (ValueError, InvalidOperation) as error:
                errors.append(f"строка {line_number}: {error}")
        if errors:
            details = "; ".join(errors[:5])
            if len(errors) > 5:
                details += f"; и ещё {len(errors) - 5}"
            raise ValueError(f"Импорт отменён: {details}")
        if not prepared:
            raise ValueError("В CSV нет операций для импорта")
        with self.connect() as db:
            db.executemany(
                """INSERT INTO entries
                (operation_date, kind, amount_cents, sign, from_party, to_party, description,
                 owner, currency, fx_rate, amount_byn_cents)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
        return len(prepared)

    def export_csv(self, destination: str | Path) -> Path:
        target = Path(destination)
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["Дата", "Раздел", "Операция", "Баланс", "Знак", "Сумма", "Валюта",
                             "Курс НБРБ", "Сумма BYN", "От кого", "Кому", "Пояснение",
                             "Изменение взаимного долга", "Изменение депозита займа"])
            for item in reversed(self.list()):
                writer.writerow([
                    item.operation_date, item.section, item.kind_label, item.owner,
                    "+" if item.sign > 0 else "−", f"{Decimal(item.amount_cents) / 100:.2f}",
                    item.currency, item.fx_rate, f"{Decimal(item.byn_cents) / 100:.2f}",
                    item.from_party, item.to_party, item.description,
                    f"{Decimal(item.effect_cents) / 100:.2f}",
                    f"{Decimal(item.deposit_effect_cents) / 100:.2f}",
                ])
        return target
