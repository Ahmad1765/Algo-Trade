# file: tests/test_main_uses_session_manager.py
import inspect

from src.cli import main


def test_run_pipeline_signature_preserved():
    sig = inspect.signature(main._run_pipeline)
    assert "market_adapter" in sig.parameters
    assert "sim_clock" in sig.parameters


def test_main_module_imports_session_manager():
    src = inspect.getsource(main)
    assert "SessionManager" in src
    assert "build_pipeline" in src or "from src.runtime" in src
