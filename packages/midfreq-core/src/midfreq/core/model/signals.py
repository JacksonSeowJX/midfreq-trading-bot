"""
Signals, after NautilusTrader's custom `Signal` data.

A signal separates "what the model thinks" from "what the portfolio does about it". The
cross-sectional reversal port publishes its ranking of the universe as a Signal and a separate
portfolio step turns the ranking into orders, so either half can be swapped or tested alone. The
original strategies fused the two inside `on_data`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from midfreq.core.model.identifiers import InstrumentId


@dataclass(frozen=True, slots=True)
class Signal:
    name: str                                   # e.g. 'xs_reversal_rank'
    value: Any                                  # a number, a string, or an immutable ranking
    ts_event: int                               # the data time the signal refers to (UTC ns)
    ts_init: int = 0
    instrument_id: Optional[InstrumentId] = None
    source: Optional[str] = None                # who published it (strategy / actor id)

    @property
    def topic(self) -> str:
        return f"data.signals.{self.name}"
