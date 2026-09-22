import json
import os
from pathlib import Path
from typing import Dict, List, Any

class ConfigLoader:
    """
    Centralized configuration loader for tracking symbols, markets, and providers.
    """
    def __init__(self, config_path: str = None):
        if config_path is None:
            # Default to tracking symbols.json at project root / config directory
            base_dir = Path(__file__).parent.parent.parent
            self.config_path = base_dir / "config" / "symbols.json"
        else:
            self.config_path = Path(config_path)
            
        self.config_data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            print(f"Warning: Configuration file not found at {self.config_path}")
            return {"markets": {}}
            
        with open(self.config_path, "r") as f:
            return json.load(f)

    def get_live_symbols(self, market: str = None) -> List[str]:
        """
        Get all symbols that currently have 'live' status.
        If market is specified, filter by that market.
        """
        symbols = []
        markets = self.config_data.get("markets", {})
        
        for mkt, data in markets.items():
            if market and mkt.upper() != market.upper():
                continue
            
            if data.get("status") == "live":
                symbols.extend(data.get("symbols", []))
                
        return symbols

    def get_all_symbols(self, market: str = None) -> List[str]:
        """
        Get all symbols regardless of their status ('live' or 'planned').
        """
        symbols = []
        markets = self.config_data.get("markets", {})
        
        for mkt, data in markets.items():
            if market and mkt.upper() != market.upper():
                continue
            
            symbols.extend(data.get("symbols", []))
            
        return symbols

    # ─── Universes ────────────────────────────────────────────────
    # Every study's stock universe is declared in config/symbols.json
    # rather than hardcoded in the script that happens to use it. The
    # four universes previously lived in four places (symbols.json for
    # two of them, plus the SP100 and HSI backfill scripts), which made
    # "what was tested against what" hard to answer.

    def list_universes(self):
        """Names of every declared universe."""
        return list(self.config_data.get("universes", {}).keys())

    def get_universe(self, name: str, with_data_only: bool = False):
        """
        Symbols for a named universe (hk_live, us_15, sp100, hsi).

        Universes either carry their own symbol list or point at a
        market's list via `source`. Pass with_data_only=True to drop
        symbols that have no cached candles, which is what study
        scripts actually want.
        """
        universes = self.config_data.get("universes", {})
        if name not in universes:
            raise KeyError(f"unknown universe {name!r}; have {sorted(universes)}")
        u = universes[name]

        symbols = u.get("symbols")
        if symbols is None:
            market = u.get("market")
            symbols = self.config_data.get("markets", {}).get(market, {}).get("symbols", [])

        if with_data_only:
            from pathlib import Path
            root = Path(__file__).resolve().parent.parent.parent / "data"
            symbols = [s for s in symbols
                       if (root / s.replace('.', '_') / '1h.parquet').exists()]
        return list(symbols)

    def describe_universe(self, name: str) -> dict:
        """Metadata for a universe: description, provenance, which studies use it."""
        universes = self.config_data.get("universes", {})
        if name not in universes:
            raise KeyError(f"unknown universe {name!r}; have {sorted(universes)}")
        return dict(universes[name])
