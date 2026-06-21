# file: tests/test_simulate_script.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import simulate  # noqa: E402


def test_validate_accepts_trading_day():
    d = simulate.validate_sim_date("2026-06-17")  # Wednesday, not a holiday
    assert (d.year, d.month, d.day) == (2026, 6, 17)


def test_validate_rejects_weekend():
    with pytest.raises(SystemExit):
        simulate.validate_sim_date("2026-06-20")  # Saturday


def test_validate_rejects_holiday():
    with pytest.raises(SystemExit):
        simulate.validate_sim_date("2026-06-19")  # Juneteenth (NYSE holiday)
