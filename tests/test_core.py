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


def test_delete(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    entry_id = ledger.add(operation_date="2026-01-01", kind="fine", amount="500", sign=1, owner="Юра")
    assert ledger.delete(entry_id)
    assert ledger.list() == []
