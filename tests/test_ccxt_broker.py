"""CCXTBroker.get_balance() must read the *quote* currency of the traded
symbol, not a hardcoded one - otherwise trading any pair other than the
hardcoded currency reads the wrong (or a missing, zeroed) balance live.
"""
from __future__ import annotations

from ict_bot.execution.ccxt_broker import CCXTBroker


class _FakeExchange:
    def __init__(self, balances: dict):
        self._balances = balances

    def fetch_balance(self) -> dict:
        return self._balances


def test_get_balance_reads_the_symbol_quote_currency():
    exchange = _FakeExchange({"EUR": {"free": 1234.5, "used": 0.0, "total": 1234.5}})
    broker = CCXTBroker(exchange, symbol="BTC/EUR")
    assert broker.get_balance() == 1234.5


def test_get_balance_not_confused_by_other_currencies_in_the_wallet():
    exchange = _FakeExchange(
        {
            "USDT": {"free": 999.0, "used": 0.0, "total": 999.0},
            "EUR": {"free": 42.0, "used": 0.0, "total": 42.0},
        }
    )
    broker = CCXTBroker(exchange, symbol="BTC/EUR")
    assert broker.get_balance() == 42.0


def test_get_balance_zero_when_quote_currency_missing_from_wallet():
    exchange = _FakeExchange({"USDT": {"free": 999.0, "used": 0.0, "total": 999.0}})
    broker = CCXTBroker(exchange, symbol="BTC/EUR")
    assert broker.get_balance() == 0.0
