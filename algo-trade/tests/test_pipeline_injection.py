# file: tests/test_pipeline_injection.py
import inspect

from src.cli import main


def test_run_pipeline_accepts_injection_kwargs():
    sig = inspect.signature(main._run_pipeline)
    assert "market_adapter" in sig.parameters
    assert "sim_clock" in sig.parameters
    # both must be keyword-only with default None
    assert sig.parameters["market_adapter"].default is None
    assert sig.parameters["sim_clock"].default is None
