# file: src/runtime/context.py
"""
RuntimeContext — mutable holder of the currently-active pipeline's components.

API endpoints read their dependencies from a single RuntimeContext instance so
that the SessionManager can swap the whole set (live <-> sim) atomically and
every handler observes the change immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RuntimeContext:
    mode: str = "live"  # "live" | "sim"
    risk_manager: Any = None
    position_store: Any = None
    market_adapter: Any = None
    strategy_engine: Any = None
    broker_adapter: Any = None
    sim_clock: Any = None
    signal_store: List[Dict] = field(default_factory=list)
    action_store: List[Dict] = field(default_factory=list)
