# file: tests/test_sp500_universe.py
from src.sim.sp500 import SP500_SYMBOLS


def test_universe_is_sizable():
    assert len(SP500_SYMBOLS) >= 400


def test_no_duplicates():
    assert len(SP500_SYMBOLS) == len(set(SP500_SYMBOLS))


def test_all_uppercase_nonempty():
    for s in SP500_SYMBOLS:
        assert s and s == s.upper()


def test_contains_known_megacaps():
    for sym in ("AAPL", "MSFT", "NVDA", "AMZN", "TSLA"):
        assert sym in SP500_SYMBOLS
