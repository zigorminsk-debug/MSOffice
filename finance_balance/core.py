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

DEFAULT_OWNERS = ("Собственник 1", "Собственник 2")
OWNERS = DEFAULT_OWNERS  # совместимость для внешних импортов
DEFAULT_VAT_RATE = Decimal("20")
DEFAULT_PROFIT_TAX_RATE = Decimal("20")
CURRENCIES = ("BYN", "USD")
# Код: название, раздел, рекомендуемый знак, режим расчёта.
KINDS = {
    "credit": ("Кредит", "Приход", 1, "debt"),
    "income": ("Иное поступление", "Приход", 1, "debt"),
    "purchase": ("Покупка товаров собственником — с вычетом налогов", "Расход", 1, "debt"),
    "personal_purchase": ("Личная покупка собственника — с вычетом налогов", "Личная покупка", 1, "debt"),
    "cash_withdrawal": ("Снятие наличных", "Расход", 1, "debt"),
    "interest": ("Проценты банка", "Расход", 1, "debt"),
    "fine": ("Штраф", "Расход", 1, "debt"),
    "leasing": ("Лизинг", "Расход", 1, "debt"),
    "investment": ("Инвестиционные расходы", "Расход", 1, "debt"),
    "repayment_bank": ("Возврат долга — перечисление", "Возврат", -1, "debt"),
    "repayment_cash": ("Возврат долга — наличные", "Возврат", -1, "debt"),
    "loan_deposit": ("Внесение на депозит", "Депозиты", 1, "deposit_add"),
    "loan_use": ("Расход из депозита / снятие", "Депозиты", -1, "deposit_use"),
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
    owner: str = "Собственник 2"
    currency: str = "BYN"
    fx_rate: str = "1"
    amount_byn_cents: int = 0
    reference_rate: str = ""
    fx_difference_cents: int = 0
    deposit_source: str = ""
    edited_at: str = ""
    edit_count: int = 0
    vat_rate: str = ""
    profit_tax_rate: str = ""
    vat_cents: int = 0
    profit_tax_cents: int = 0
    debt_amount_cents: int = 0
    owner_slot: int = 2
    repayment_method: str = ""
    purchase_offset_cents: int = 0
    balance_after_cents: int = 0

    @property
    def edit_note(self) -> str:
        if not self.edited_at:
            return ""
        try:
            moment = datetime.fromisoformat(self.edited_at)
            suffix = f" · правок: {self.edit_count}" if self.edit_count > 1 else ""
            return f"Изменено: {moment.strftime('%d.%m.%Y %H:%M:%S')}{suffix}"
        except ValueError:
            return f"Изменено: {self.edited_at}"

    @property
    def mode(self) -> str:
        return KINDS.get(self.kind, ("", "", 0, "debt"))[3]

    @property
    def deposit_name(self) -> str:
        return self.deposit_source.strip() or self.from_party.strip() or self.owner

    @property
    def effect_cents(self) -> int:
        if self.mode != "debt":
            return 0
        owner_direction = 1 if self.owner_slot == 2 else -1
        return self.balance_amount_cents * self.sign * owner_direction

    @property
    def settlement_cents(self) -> int:
        return self.debt_amount_cents or self.byn_cents

    @property
    def effective_repayment_method(self) -> str:
        if self.repayment_method in {"account", "cash"}:
            return self.repayment_method
        return "cash" if self.kind == "repayment_cash" else "account"

    @property
    def balance_amount_cents(self) -> int:
        amount = self.settlement_cents
        if self.kind in {"repayment_bank", "repayment_cash"} and self.effective_repayment_method == "cash":
            return int((Decimal(amount) / 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return amount

    @property
    def repayment_note(self) -> str:
        if self.kind not in {"repayment_bank", "repayment_cash"}:
            return ""
        if self.effective_repayment_method == "cash":
            return f"Погашение наличными: {format_money(self.byn_cents)} ÷ 2 = уменьшение долга {format_money(self.balance_amount_cents)}"
        return f"Погашение со счёта: уменьшение долга на полную сумму {format_money(self.balance_amount_cents)}"

    @property
    def calculation_note(self) -> str:
        return self.tax_note or self.repayment_note

    @property
    def tax_note(self) -> str:
        if self.kind not in {"purchase", "personal_purchase"} or not self.debt_amount_cents:
            return ""
        if self.purchase_offset_cents:
            excess = self.byn_cents - self.purchase_offset_cents
            return (
                f"Расчёт покупки: {format_money(self.byn_cents)}; встречный долг погашен на "
                f"{format_money(self.purchase_offset_cents)}; остаток покупки {format_money(excess)} − "
                f"НДС {self.vat_rate}% ({format_money(self.vat_cents)}) − налог на прибыль "
                f"{self.profit_tax_rate}% ({format_money(self.profit_tax_cents)}); зачтено в баланс "
                f"{format_money(self.debt_amount_cents)}; итоговое взаимное сальдо "
                f"{format_money(abs(self.balance_after_cents))}"
            )
        return (
            f"Расчёт покупки собственника: {format_money(self.byn_cents)} − НДС {self.vat_rate}% "
            f"({format_money(self.vat_cents)}) − налог на прибыль {self.profit_tax_rate}% "
            f"({format_money(self.profit_tax_cents)}) = долг {format_money(self.debt_amount_cents)}"
        )

    @property
    def deposit_effect_cents(self) -> int:
        if self.mode == "deposit_add":
            return self.byn_cents
        if self.mode == "deposit_use":
            return -(self.byn_cents + self.fx_difference_cents)
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

    @property
    def calculator_byn_cents(self) -> int:
        """Исходная сумма ячейки, приведённая к BYN для калькулятора."""
        if self.currency == "BYN":
            return self.amount_cents
        return self.byn_cents


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
        connection.execute("PRAGMA foreign_keys = ON")
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
                    owner TEXT NOT NULL DEFAULT 'Собственник 2',
                    currency TEXT NOT NULL DEFAULT 'BYN',
                    fx_rate TEXT NOT NULL DEFAULT '1',
                    amount_byn_cents INTEGER NOT NULL DEFAULT 0,
                    reference_rate TEXT NOT NULL DEFAULT '',
                    fx_difference_cents INTEGER NOT NULL DEFAULT 0,
                    deposit_source TEXT NOT NULL DEFAULT '',
                    vat_rate TEXT NOT NULL DEFAULT '',
                    profit_tax_rate TEXT NOT NULL DEFAULT '',
                    vat_cents INTEGER NOT NULL DEFAULT 0,
                    profit_tax_cents INTEGER NOT NULL DEFAULT 0,
                    debt_amount_cents INTEGER NOT NULL DEFAULT 0,
                    owner_slot INTEGER NOT NULL DEFAULT 2,
                    repayment_method TEXT NOT NULL DEFAULT '',
                    purchase_offset_cents INTEGER NOT NULL DEFAULT 0,
                    balance_after_cents INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            existing = {row[1] for row in db.execute("PRAGMA table_info(entries)")}
            migrations = {
                "owner": "ALTER TABLE entries ADD COLUMN owner TEXT NOT NULL DEFAULT 'Собственник 2'",
                "currency": "ALTER TABLE entries ADD COLUMN currency TEXT NOT NULL DEFAULT 'BYN'",
                "fx_rate": "ALTER TABLE entries ADD COLUMN fx_rate TEXT NOT NULL DEFAULT '1'",
                "amount_byn_cents": "ALTER TABLE entries ADD COLUMN amount_byn_cents INTEGER NOT NULL DEFAULT 0",
                "reference_rate": "ALTER TABLE entries ADD COLUMN reference_rate TEXT NOT NULL DEFAULT ''",
                "fx_difference_cents": "ALTER TABLE entries ADD COLUMN fx_difference_cents INTEGER NOT NULL DEFAULT 0",
                "deposit_source": "ALTER TABLE entries ADD COLUMN deposit_source TEXT NOT NULL DEFAULT ''",
                "vat_rate": "ALTER TABLE entries ADD COLUMN vat_rate TEXT NOT NULL DEFAULT ''",
                "profit_tax_rate": "ALTER TABLE entries ADD COLUMN profit_tax_rate TEXT NOT NULL DEFAULT ''",
                "vat_cents": "ALTER TABLE entries ADD COLUMN vat_cents INTEGER NOT NULL DEFAULT 0",
                "profit_tax_cents": "ALTER TABLE entries ADD COLUMN profit_tax_cents INTEGER NOT NULL DEFAULT 0",
                "debt_amount_cents": "ALTER TABLE entries ADD COLUMN debt_amount_cents INTEGER NOT NULL DEFAULT 0",
                "owner_slot": "ALTER TABLE entries ADD COLUMN owner_slot INTEGER NOT NULL DEFAULT 2",
                "repayment_method": "ALTER TABLE entries ADD COLUMN repayment_method TEXT NOT NULL DEFAULT ''",
                "purchase_offset_cents": "ALTER TABLE entries ADD COLUMN purchase_offset_cents INTEGER NOT NULL DEFAULT 0",
                "balance_after_cents": "ALTER TABLE entries ADD COLUMN balance_after_cents INTEGER NOT NULL DEFAULT 0",
            }
            for column, sql in migrations.items():
                if column not in existing:
                    db.execute(sql)
            db.execute("UPDATE entries SET amount_byn_cents = amount_cents WHERE amount_byn_cents = 0")
            db.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(operation_date, id)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS entry_edits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL,
                    edited_at TEXT NOT NULL,
                    FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_entry_edits_entry ON entry_edits(entry_id, id)")
            db.execute("CREATE TABLE IF NOT EXISTS fitnes_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            defaults = {
                "owner1": DEFAULT_OWNERS[0], "owner2": DEFAULT_OWNERS[1], "firms": "[]",
                "vat_rate": str(DEFAULT_VAT_RATE), "profit_tax_rate": str(DEFAULT_PROFIT_TAX_RATE),
            }
            for key, value in defaults.items():
                db.execute("INSERT OR IGNORE INTO fitnes_settings(key, value) VALUES (?, ?)", (key, value))
            # Однократная обезличенная миграция прежних имён.
            db.execute("UPDATE entries SET owner_slot=1 WHERE owner IN ('Игорь', 'Собственник 1')")
            db.execute("UPDATE entries SET owner_slot=2 WHERE owner IN ('Юра', 'Собственник 2')")
            db.execute("UPDATE entries SET owner=? WHERE owner='Игорь'", (DEFAULT_OWNERS[0],))
            db.execute("UPDATE entries SET owner=? WHERE owner='Юра'", (DEFAULT_OWNERS[1],))
            self._backfill_tax_calculations(db)
            self._recalculate_purchase_offsets(db)

    @staticmethod
    def _tax_calculation(amount_cents: int, vat: Decimal, profit: Decimal) -> tuple[int, int, int]:
        vat_cents = int((Decimal(amount_cents) * vat / (Decimal("100") + vat)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        taxable_cents = amount_cents - vat_cents
        profit_cents = int((Decimal(taxable_cents) * profit / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return vat_cents, profit_cents, amount_cents - vat_cents - profit_cents

    def _backfill_tax_calculations(self, db: sqlite3.Connection) -> None:
        values = {row[0]: row[1] for row in db.execute("SELECT key, value FROM fitnes_settings")}
        vat, profit = Decimal(values.get("vat_rate", "20")), Decimal(values.get("profit_tax_rate", "20"))
        rows = db.execute(
            "SELECT id, CASE WHEN amount_byn_cents=0 THEN amount_cents ELSE amount_byn_cents END "
            "FROM entries WHERE kind IN ('purchase', 'personal_purchase') AND debt_amount_cents=0"
        ).fetchall()
        for entry_id, amount_cents in rows:
            vat_cents, profit_cents, debt_cents = self._tax_calculation(amount_cents, vat, profit)
            db.execute(
                "UPDATE entries SET vat_rate=?, profit_tax_rate=?, vat_cents=?, profit_tax_cents=?, debt_amount_cents=? WHERE id=?",
                (str(vat), str(profit), vat_cents, profit_cents, debt_cents, entry_id),
            )

    def _recalculate_purchase_offsets(self, db: sqlite3.Connection) -> None:
        """Пересчитывает покупки по хронологии: встречный долг гасится до налогов."""
        current_balance = 0
        rows = db.execute("SELECT * FROM entries ORDER BY operation_date, id").fetchall()
        for row in rows:
            kind = row["kind"]
            mode = KINDS.get(kind, ("", "", 0, "debt"))[3]
            if mode != "debt":
                db.execute("UPDATE entries SET balance_after_cents=? WHERE id=?", (current_balance, row["id"]))
                continue
            raw_cents = row["amount_byn_cents"] or row["amount_cents"]
            owner_direction = 1 if row["owner_slot"] == 2 else -1
            direction = owner_direction * row["sign"]
            impact_cents = row["debt_amount_cents"] or raw_cents
            offset_cents = 0
            if kind in {"purchase", "personal_purchase"}:
                if current_balance and current_balance * direction < 0:
                    offset_cents = min(raw_cents, abs(current_balance))
                excess_cents = raw_cents - offset_cents
                vat = Decimal(row["vat_rate"] or DEFAULT_VAT_RATE)
                profit = Decimal(row["profit_tax_rate"] or DEFAULT_PROFIT_TAX_RATE)
                vat_cents, profit_cents, net_excess = self._tax_calculation(excess_cents, vat, profit) if excess_cents else (0, 0, 0)
                impact_cents = offset_cents + net_excess
                db.execute(
                    """UPDATE entries SET vat_rate=?, profit_tax_rate=?, vat_cents=?, profit_tax_cents=?,
                    debt_amount_cents=?, purchase_offset_cents=? WHERE id=?""",
                    (str(vat), str(profit), vat_cents, profit_cents, impact_cents, offset_cents, row["id"]),
                )
            elif kind in {"repayment_bank", "repayment_cash"}:
                method = row["repayment_method"] or ("cash" if kind == "repayment_cash" else "account")
                impact_cents = int((Decimal(raw_cents) / 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if method == "cash" else raw_cents
            current_balance += impact_cents * direction
            db.execute("UPDATE entries SET balance_after_cents=? WHERE id=?", (current_balance, row["id"]))

    def settings(self) -> dict[str, object]:
        with self.connect() as db:
            values = {row[0]: row[1] for row in db.execute("SELECT key, value FROM fitnes_settings")}
        try:
            firms = json.loads(values.get("firms", "[]"))
        except json.JSONDecodeError:
            firms = []
        return {"owner1": values.get("owner1", DEFAULT_OWNERS[0]), "owner2": values.get("owner2", DEFAULT_OWNERS[1]),
                "firms": firms, "vat_rate": values.get("vat_rate", "20"),
                "profit_tax_rate": values.get("profit_tax_rate", "20")}

    @property
    def owners(self) -> tuple[str, str]:
        values = self.settings()
        return str(values["owner1"]), str(values["owner2"])

    @property
    def parties(self) -> list[str]:
        values = self.settings()
        return list(dict.fromkeys([*self.owners, *(str(x) for x in values["firms"] if str(x).strip())]))

    def set_settings(self, owner1: str, owner2: str, firms: list[str], vat_rate: str, profit_tax_rate: str) -> None:
        owner1, owner2 = owner1.strip(), owner2.strip()
        if not owner1 or not owner2 or owner1.casefold() == owner2.casefold():
            raise ValueError("Укажите два разных имени собственников")
        vat, profit = decimal_value(vat_rate), decimal_value(profit_tax_rate)
        if vat > 100 or profit > 100:
            raise ValueError("Ставка налога должна быть от 0 до 100 процентов")
        clean_firms = list(dict.fromkeys(x.strip() for x in firms if x.strip() and x.strip() not in {owner1, owner2}))
        with self.connect() as db:
            db.execute("UPDATE entries SET owner=? WHERE owner_slot=1", (owner1,))
            db.execute("UPDATE entries SET owner=? WHERE owner_slot=2", (owner2,))
            for key, value in {"owner1": owner1, "owner2": owner2, "firms": json.dumps(clean_firms, ensure_ascii=False),
                               "vat_rate": str(vat), "profit_tax_rate": str(profit)}.items():
                db.execute("INSERT OR REPLACE INTO fitnes_settings(key, value) VALUES (?, ?)", (key, value))

    def _prepare_entry(
        self, *, operation_date: str, kind: str, amount: str | int | Decimal,
        sign: int | str, owner: str = "Собственник 2", currency: str = "BYN",
        fx_rate: str | Decimal | None = None,
        reference_rate: str | Decimal | None = None, deposit_source: str = "",
        repayment_method: str = "", from_party: str = "", to_party: str = "", description: str = "",
    ) -> tuple:
        if kind not in KINDS:
            raise ValueError("Неизвестный вид операции")
        try:
            date.fromisoformat(operation_date)
        except ValueError as exc:
            raise ValueError("Некорректная дата") from exc
        if owner not in self.owners:
            raise ValueError("Выберите, на чей баланс относится сумма")
        if currency not in CURRENCIES:
            raise ValueError("Поддерживаются только BYN и USD")
        sign_int = int(sign)
        if sign_int not in (-1, 1):
            raise ValueError("Знак должен быть + или −")
        repayment = repayment_method.strip().lower()
        if repayment not in {"", "account", "cash"}:
            raise ValueError("Выберите путь возврата: со счёта или наличными")
        if kind in {"repayment_bank", "repayment_cash"} and not repayment:
            repayment = "cash" if kind == "repayment_cash" else "account"
        cents = money_to_cents(amount) if not isinstance(amount, int) else amount
        if cents <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        reference = decimal_value(reference_rate) if reference_rate and str(reference_rate).strip() else None
        supplied_rate = decimal_value(fx_rate) if fx_rate and str(fx_rate).strip() else None
        fx_difference = 0
        # Для расходования валютного депозита сумма может быть исходным взносом
        # в BYN. По двум курсам рассчитываются фактическая выплата и разница.
        if kind == "loan_use" and reference is not None and supplied_rate is not None:
            if currency == "BYN":
                received = Decimal(cents) * supplied_rate / reference
                byn_cents = int(received.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                fx_difference = cents - byn_cents
            else:
                byn_cents = int((Decimal(cents) * supplied_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                original_byn = int((Decimal(cents) * reference).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                fx_difference = original_byn - byn_cents
            rate = supplied_rate
        else:
            if currency == "USD":
                rate = supplied_rate or self.rate_provider.get_usd_rate(operation_date)
            else:
                rate = Decimal("1")
            byn_cents = int((Decimal(cents) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        vat_rate = profit_rate = ""
        vat_cents = profit_tax_cents = debt_amount_cents = 0
        if kind in {"purchase", "personal_purchase"}:
            settings = self.settings()
            vat = Decimal(settings["vat_rate"])
            profit = Decimal(settings["profit_tax_rate"])
            vat_cents, profit_tax_cents, debt_amount_cents = self._tax_calculation(byn_cents, vat, profit)
            vat_rate, profit_rate = str(vat), str(profit)
        source = deposit_source.strip()
        if KINDS[kind][3] in {"deposit_add", "deposit_use"} and not source:
            source = from_party.strip() or owner
        return (
            operation_date, kind, cents, sign_int, from_party.strip(), to_party.strip(),
            description.strip(), owner, currency, str(rate), byn_cents,
            str(reference) if reference is not None else "", fx_difference, source,
            vat_rate, profit_rate, vat_cents, profit_tax_cents, debt_amount_cents, self.owners.index(owner) + 1, repayment,
        )

    def add(self, **values) -> int:
        prepared = self._prepare_entry(**values)
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO entries
                (operation_date, kind, amount_cents, sign, from_party, to_party, description,
                 owner, currency, fx_rate, amount_byn_cents, reference_rate, fx_difference_cents, deposit_source, vat_rate, profit_tax_rate, vat_cents, profit_tax_cents, debt_amount_cents, owner_slot, repayment_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
            self._recalculate_purchase_offsets(db)
            return int(cursor.lastrowid)

    def update(self, entry_id: int, **values) -> bool:
        prepared = self._prepare_entry(**values)
        with self.connect() as db:
            result = db.execute(
                """UPDATE entries SET operation_date=?, kind=?, amount_cents=?, sign=?,
                from_party=?, to_party=?, description=?, owner=?, currency=?, fx_rate=?,
                amount_byn_cents=?, reference_rate=?, fx_difference_cents=?, deposit_source=?, vat_rate=?, profit_tax_rate=?, vat_cents=?, profit_tax_cents=?, debt_amount_cents=?, owner_slot=?, repayment_method=? WHERE id=?""",
                (*prepared, entry_id),
            )
            if result.rowcount:
                self._recalculate_purchase_offsets(db)
                edited_at = datetime.now().astimezone().isoformat(timespec="seconds")
                db.execute("INSERT INTO entry_edits(entry_id, edited_at) VALUES (?, ?)", (entry_id, edited_at))
            return result.rowcount > 0

    @staticmethod
    def _entry_select() -> str:
        return """SELECT e.*,
            COALESCE((SELECT edited_at FROM entry_edits h WHERE h.entry_id=e.id ORDER BY h.id DESC LIMIT 1), '') AS edited_at,
            (SELECT COUNT(*) FROM entry_edits h WHERE h.entry_id=e.id) AS edit_count
            FROM entries e"""

    def get(self, entry_id: int) -> Entry | None:
        with self.connect() as db:
            row = db.execute(self._entry_select() + " WHERE e.id = ?", (entry_id,)).fetchone()
        return Entry(**dict(row)) if row else None

    def delete(self, entry_id: int) -> bool:
        with self.connect() as db:
            result = db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            if result.rowcount:
                self._recalculate_purchase_offsets(db)
            return result.rowcount > 0

    def list(self) -> list[Entry]:
        with self.connect() as db:
            rows = db.execute(self._entry_select() + " ORDER BY e.operation_date DESC, e.id DESC").fetchall()
        return [Entry(**dict(row)) for row in rows]

    def deposit_balances(self) -> list[dict[str, int | str]]:
        balances: dict[str, int] = {}
        for item in self.list():
            if item.mode in {"deposit_add", "deposit_use"}:
                balances[item.deposit_name] = balances.get(item.deposit_name, 0) + item.deposit_effect_cents
        return [
            {"source": source, "balance": balance}
            for source, balance in sorted(balances.items(), key=lambda pair: pair[0].casefold())
            if balance != 0
        ]

    def summary(self) -> dict[str, int]:
        entries = self.list()
        debt = sum(item.effect_cents for item in entries)
        return {
            "debt": debt,
            "owner1": debt,
            "owner2": -debt,
            "incoming": sum(item.byn_cents for item in entries if item.section == "Приход"),
            "expenses": sum(item.byn_cents for item in entries if item.section == "Расход"),
            "repayments": sum(item.balance_amount_cents for item in entries if item.mode == "debt" and item.sign < 0),
            "increases": sum(item.balance_amount_cents for item in entries if item.mode == "debt" and item.sign > 0),
            "owner1_deposit": sum(item.deposit_effect_cents for item in entries if item.owner == self.owners[0]),
            "owner2_deposit": sum(item.deposit_effect_cents for item in entries if item.owner == self.owners[1]),
            "fx_difference": sum(item.fx_difference_cents for item in entries),
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
                owner = value(row, "Баланс", "На чей баланс", "Владелец", "Owner", required=True)
                if owner not in self.owners:
                    raise ValueError(f"баланс должен быть «{self.owners[0]}» или «{self.owners[1]}»")
                raw_sign = value(row, "Знак", "Действие", "Sign", required=True).replace("−", "-")
                if raw_sign.strip().lower() in {"+", "+1", "1", "плюс"}:
                    sign = 1
                elif raw_sign.strip().lower() in {"-", "-1", "минус"}:
                    sign = -1
                else:
                    raise ValueError("знак должен быть + или −")
                prepared.append(self._prepare_entry(
                    operation_date=operation_date, kind=kind,
                    amount=value(row, "Сумма", "Amount", required=True), sign=sign,
                    owner=owner, currency=(value(row, "Валюта", "Currency") or "BYN").upper(),
                    fx_rate=value(row, "Курс получения", "Курс НБРБ", "Курс", "Rate"),
                    reference_rate=value(row, "Курс внесения", "Исходный курс", "Reference rate"),
                    deposit_source=value(row, "Источник депозита", "Депозит от", "Deposit source"),
                    repayment_method={"со счёта": "account", "счет": "account", "счёт": "account", "account": "account", "наличными": "cash", "нал": "cash", "cash": "cash"}.get(value(row, "Путь возврата", "Способ возврата", "Repayment method").lower(), ""),
                    from_party=value(row, "От кого", "From"), to_party=value(row, "Кому", "To"),
                    description=value(row, "Пояснение", "Описание", "Description"),
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
                 owner, currency, fx_rate, amount_byn_cents, reference_rate, fx_difference_cents, deposit_source, vat_rate, profit_tax_rate, vat_cents, profit_tax_cents, debt_amount_cents, owner_slot, repayment_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
        return len(prepared)

    def export_csv(self, destination: str | Path) -> Path:
        target = Path(destination)
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["Дата", "Раздел", "Операция", "Баланс", "Знак", "Сумма", "Валюта",
                             "Курс внесения", "Курс получения", "Получено BYN", "Курсовая разница",
                             "Источник депозита", "Путь возврата", "От кого", "Кому", "Пояснение", "Примечание редактирования",
                             "НДС", "Налог на прибыль", "Расчётный долг", "Изменение взаимного долга", "Изменение депозита займа"])
            for item in reversed(self.list()):
                writer.writerow([
                    item.operation_date, item.section, item.kind_label, item.owner,
                    "+" if item.sign > 0 else "−", f"{Decimal(item.amount_cents) / 100:.2f}",
                    item.currency, item.reference_rate, item.fx_rate,
                    f"{Decimal(item.byn_cents) / 100:.2f}",
                    f"{Decimal(item.fx_difference_cents) / 100:.2f}",
                    item.deposit_source, ("Наличными" if item.effective_repayment_method == "cash" else "Со счёта") if item.kind in {"repayment_bank", "repayment_cash"} else "", item.from_party, item.to_party, item.description, item.edit_note,
                    f"{Decimal(item.vat_cents) / 100:.2f}", f"{Decimal(item.profit_tax_cents) / 100:.2f}",
                    f"{Decimal(item.balance_amount_cents) / 100:.2f}",
                    f"{Decimal(item.effect_cents) / 100:.2f}",
                    f"{Decimal(item.deposit_effect_cents) / 100:.2f}",
                ])
        return target
