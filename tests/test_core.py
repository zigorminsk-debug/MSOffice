from finance_balance.core import Ledger, format_money, money_to_cents


def test_money_parsing():
    assert money_to_cents("1 234,56") == 123456
    assert format_money(123456).endswith("1 234,56 ₽")


def test_debt_scenario(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    ledger.add(operation_date="2026-01-01", kind="credit", amount="100000", sign=1, from_party="Банк", to_party="Юра")
    ledger.add(operation_date="2026-02-01", kind="interest", amount="10000", sign=1, from_party="Банк", to_party="Компания")
    ledger.add(operation_date="2026-03-01", kind="repayment_bank", amount="25000", sign=-1, from_party="Юра", to_party="Игорь")
    ledger.add(operation_date="2026-03-02", kind="igor_personal", amount="15000", sign=-1, from_party="Компания", to_party="Продавец")
    result = ledger.summary()
    assert result["debt"] == 7_000_000
    assert result["igor"] == 7_000_000
    assert result["yura"] == -7_000_000
    assert result["incoming"] == 10_000_000
    assert result["expenses"] == 1_000_000
    assert result["repayments"] == 4_000_000


def test_delete(tmp_path):
    ledger = Ledger(tmp_path / "test.db")
    entry_id = ledger.add(operation_date="2026-01-01", kind="fine", amount="500", sign=1)
    assert ledger.delete(entry_id)
    assert ledger.list() == []
