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
