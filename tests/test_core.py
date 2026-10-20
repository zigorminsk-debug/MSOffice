from decimal import Decimal

from finance_balance.core import Ledger, format_money, money_to_cents
from finance_balance.grammar import to_dative


class FixedRates:
    def get_usd_rate(self, operation_date):
        assert operation_date == "2026-01-10"
        return Decimal("3.25")


def test_money_parsing():
    assert money_to_cents("1 234,56") == 123456
    assert format_money(123456).endswith("1 234,56 Br")


def test_two_owner_debt_scenario(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    ledger.add(operation_date="2026-01-01", kind="credit", amount="100000", sign=1, owner="Собственник 2")
    ledger.add(operation_date="2026-02-01", kind="interest", amount="10000", sign=1, owner="Собственник 2")
    ledger.add(operation_date="2026-03-01", kind="repayment_bank", amount="25000", sign=-1, owner="Собственник 2")
    # Личная покупка Собственника 1 увеличивает его долг Собственнику 2 с учётом налогов.
    ledger.add(operation_date="2026-03-02", kind="personal_purchase", amount="15000", sign=1, owner="Собственник 1")
    result = ledger.summary()
    assert result["debt"] == 7_500_000
    assert result["owner1"] == 7_500_000
    assert result["owner2"] == -7_500_000


def test_usd_conversion_and_loan_deposits(tmp_path):
    ledger = Ledger(tmp_path / "test.db", rate_provider=FixedRates())
    ledger.add(operation_date="2026-01-10", kind="purchase", amount="100", sign=1,
               owner="Собственник 2", currency="USD")
    ledger.add(operation_date="2026-01-11", kind="loan_deposit", amount="1000", sign=1, owner="Собственник 1")
    ledger.add(operation_date="2026-01-12", kind="loan_use", amount="250", sign=-1, owner="Собственник 1")
    result = ledger.summary()
    assert result["debt"] == 21_666
    assert result["owner1_deposit"] == 75_000
    assert result["owner2_deposit"] == 0
    usd = next(entry for entry in ledger.list() if entry.currency == "USD")
    assert usd.fx_rate == "3.25"
    assert usd.byn_cents == 32_500


def test_csv_export_import_roundtrip(tmp_path):
    source = Ledger(tmp_path / "source.db")
    source.add(operation_date="2026-04-01", kind="purchase", amount="125,50", sign=1,
               owner="Собственник 2", currency="BYN", from_party="Компания", to_party="Магазин",
               description="Тестовая покупка")
    source.add(operation_date="2026-04-02", kind="repayment_cash", amount="25", sign=-1,
               owner="Собственник 2", currency="USD", fx_rate="3.2", from_party="Собственник 2", to_party="Собственник 1")
    exported = source.export_csv(tmp_path / "fitnes.csv")

    destination = Ledger(tmp_path / "destination.db")
    assert destination.import_csv(exported) == 2
    entries = destination.list()
    assert len(entries) == 2
    assert entries[0].currency == "USD"
    assert entries[0].fx_rate == "3.2"
    assert entries[1].description == "Тестовая покупка"
    assert destination.summary() == source.summary()


def test_csv_import_is_atomic_on_error(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    csv_data = "Дата;Операция;Баланс;Знак;Сумма;Валюта\n2026-01-01;Кредит;Собственник 2;+;100;BYN\n2026-01-02;Неизвестно;Собственник 2;+;50;BYN\n"
    try:
        ledger.import_csv_bytes(csv_data.encode("utf-8"))
        assert False, "Ожидалась ошибка импорта"
    except ValueError as error:
        assert "строка 3" in str(error)
    assert ledger.list() == []


def test_exchange_difference_closes_deposit_without_debt(tmp_path):
    ledger = Ledger(tmp_path / "fx.db")
    ledger.add(operation_date="2026-01-01", kind="loan_deposit", amount="30000", sign=1,
               owner="Собственник 2", currency="BYN", description="Валютный депозит по курсу 3,2")
    use_id = ledger.add(operation_date="2026-06-01", kind="loan_use", amount="30000", sign=-1,
                        owner="Собственник 2", currency="BYN", reference_rate="3.2", fx_rate="2.7")
    entry = ledger.get(use_id)
    assert entry is not None
    assert entry.byn_cents == 2_531_250
    assert entry.fx_difference_cents == 468_750
    assert entry.deposit_effect_cents == -3_000_000
    summary = ledger.summary()
    assert summary["owner2_deposit"] == 0
    assert summary["fx_difference"] == 468_750
    assert summary["debt"] == 0


def test_multiple_deposit_sources_only_show_nonzero(tmp_path):
    ledger = Ledger(tmp_path / "deposits.db")
    ledger.add(operation_date="2026-01-01", kind="loan_deposit", amount="1000", sign=1,
               owner="Собственник 2", deposit_source="Собственник 2")
    ledger.add(operation_date="2026-01-02", kind="loan_deposit", amount="2500", sign=1,
               owner="Собственник 1", deposit_source="ООО Ромашка")
    ledger.add(operation_date="2026-01-03", kind="loan_use", amount="1000", sign=-1,
               owner="Собственник 2", deposit_source="Собственник 2")
    assert ledger.deposit_balances() == [{"source": "ООО Ромашка", "balance": 250_000}]
    entry = next(item for item in ledger.list() if item.kind == "loan_use")
    assert entry.deposit_name == "Собственник 2"
    assert ledger.summary()["debt"] == 0


def test_edit_entry_recalculates_balance(tmp_path):
    ledger = Ledger(tmp_path / "edit.db")
    entry_id = ledger.add(operation_date="2026-01-01", kind="fine", amount="500", sign=1, owner="Собственник 2")
    assert ledger.update(entry_id, operation_date="2026-01-02", kind="fine", amount="200", sign=1,
                         owner="Собственник 1", currency="BYN", description="Исправлено")
    entry = ledger.get(entry_id)
    assert entry is not None and entry.description == "Исправлено"
    assert entry.edit_count == 1
    assert entry.edited_at
    assert "Изменено:" in entry.edit_note
    first_mark = entry.edited_at
    ledger.update(entry_id, operation_date="2026-01-03", kind="fine", amount="200", sign=1,
                  owner="Собственник 1", currency="BYN", description="Исправлено ещё раз")
    entry = ledger.get(entry_id)
    assert entry is not None and entry.edit_count == 2
    with ledger.connect() as db:
        marks = [row[0] for row in db.execute("SELECT edited_at FROM entry_edits WHERE entry_id=? ORDER BY id", (entry_id,))]
    assert len(marks) == 2 and marks[0] == first_mark
    assert ledger.summary()["debt"] == -20_000


def test_calculator_uses_source_amount_converted_to_byn(tmp_path):
    ledger = Ledger(tmp_path / "calculator.db")
    byn_id = ledger.add(operation_date="2026-01-01", kind="purchase", amount="100", sign=1,
                        owner="Собственник 2", currency="BYN")
    usd_id = ledger.add(operation_date="2026-01-02", kind="purchase", amount="10", sign=1,
                        owner="Собственник 2", currency="USD", fx_rate="3")
    assert ledger.get(byn_id).calculator_byn_cents == 10_000
    assert ledger.get(usd_id).calculator_byn_cents == 3_000


def test_delete(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    entry_id = ledger.add(operation_date="2026-01-01", kind="fine", amount="500", sign=1, owner="Собственник 2")
    assert ledger.delete(entry_id)
    assert ledger.list() == []


def test_personal_purchase_excludes_vat_and_profit_tax(tmp_path):
    ledger = Ledger(tmp_path / "tax.db")
    entry_id = ledger.add(operation_date="2026-10-20", kind="personal_purchase", amount="100", sign=1,
                          owner="Собственник 2", currency="BYN")
    entry = ledger.get(entry_id)
    assert entry.vat_cents == 1667
    assert entry.profit_tax_cents == 1667
    assert entry.debt_amount_cents == 6666
    assert entry.effect_cents == 6666
    assert "100,00 Br" in entry.tax_note and "долг 66,66 Br" in entry.tax_note


def test_configurable_owners_firms_and_tax_rates(tmp_path):
    ledger = Ledger(tmp_path / "settings.db")
    ledger.set_settings("Первый", "Второй", ["ООО Альфа", "ООО Бета"], "20", "20")
    assert ledger.owners == ("Первый", "Второй")
    assert ledger.parties == ["Первый", "Второй", "ООО Альфа", "ООО Бета"]
    entry_id = ledger.add(operation_date="2026-10-20", kind="credit", amount="10", sign=1, owner="Второй")
    assert ledger.get(entry_id).effect_cents == 1000


def test_russian_dative_for_names_and_organizations():
    assert to_dative("Игорь") == "Игорю"
    assert to_dative("Юра") == "Юре"
    assert to_dative("Павел") == "Павлу"
    assert to_dative("ООО Ромашка") == "ООО Ромашке"
    assert to_dative("ООО «Новая Ромашка»") == "ООО «Новой Ромашке»"
    assert to_dative("ИП Иванов") == "ИП Иванову"


def test_existing_goods_purchase_gets_tax_backfill(tmp_path):
    path = tmp_path / "legacy-purchase.db"
    ledger = Ledger(path)
    with ledger.connect() as db:
        db.execute("""INSERT INTO entries
            (operation_date, kind, amount_cents, sign, owner, amount_byn_cents, owner_slot)
            VALUES ('2026-10-20', 'purchase', 10000, 1, 'Собственник 2', 10000, 2)""")
    reopened = Ledger(path)
    entry = reopened.list()[0]
    assert entry.debt_amount_cents == 6666
    assert reopened.summary()["debt"] == 6666
