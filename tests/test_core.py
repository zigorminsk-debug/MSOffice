from decimal import Decimal

from finance_balance.core import Ledger, format_money, money_to_cents


class FixedRates:
    def get_usd_rate(self, operation_date):
        assert operation_date == "2026-01-10"
        return Decimal("3.25")


def test_money_parsing():
    assert money_to_cents("1 234,56") == 123456
    assert format_money(123456).endswith("1 234,56 Br")


def test_two_owner_debt_scenario(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    ledger.add(operation_date="2026-01-01", kind="credit", amount="100000", sign=1, owner="Юра")
    ledger.add(operation_date="2026-02-01", kind="interest", amount="10000", sign=1, owner="Юра")
    ledger.add(operation_date="2026-03-01", kind="repayment_bank", amount="25000", sign=-1, owner="Юра")
    # Покупка Игоря увеличивает его долг Юре и уменьшает чистый долг Юры Игорю.
    ledger.add(operation_date="2026-03-02", kind="personal_purchase", amount="15000", sign=1, owner="Игорь")
    result = ledger.summary()
    assert result["debt"] == 7_000_000
    assert result["igor"] == 7_000_000
    assert result["yura"] == -7_000_000


def test_usd_conversion_and_loan_deposits(tmp_path):
    ledger = Ledger(tmp_path / "test.db", rate_provider=FixedRates())
    ledger.add(operation_date="2026-01-10", kind="purchase", amount="100", sign=1,
               owner="Юра", currency="USD")
    ledger.add(operation_date="2026-01-11", kind="loan_deposit", amount="1000", sign=1, owner="Игорь")
    ledger.add(operation_date="2026-01-12", kind="loan_use", amount="250", sign=-1, owner="Игорь")
    result = ledger.summary()
    assert result["debt"] == 32_500
    assert result["igor_deposit"] == 75_000
    assert result["yura_deposit"] == 0
    usd = next(entry for entry in ledger.list() if entry.currency == "USD")
    assert usd.fx_rate == "3.25"
    assert usd.byn_cents == 32_500


def test_csv_export_import_roundtrip(tmp_path):
    source = Ledger(tmp_path / "source.db")
    source.add(operation_date="2026-04-01", kind="purchase", amount="125,50", sign=1,
               owner="Юра", currency="BYN", from_party="Компания", to_party="Магазин",
               description="Тестовая покупка")
    source.add(operation_date="2026-04-02", kind="repayment_cash", amount="25", sign=-1,
               owner="Юра", currency="USD", fx_rate="3.2", from_party="Юра", to_party="Игорь")
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
    csv_data = "Дата;Операция;Баланс;Знак;Сумма;Валюта\n2026-01-01;Кредит;Юра;+;100;BYN\n2026-01-02;Неизвестно;Юра;+;50;BYN\n"
    try:
        ledger.import_csv_bytes(csv_data.encode("utf-8"))
        assert False, "Ожидалась ошибка импорта"
    except ValueError as error:
        assert "строка 3" in str(error)
    assert ledger.list() == []


def test_delete(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    entry_id = ledger.add(operation_date="2026-01-01", kind="fine", amount="500", sign=1, owner="Юра")
    assert ledger.delete(entry_id)
    assert ledger.list() == []
