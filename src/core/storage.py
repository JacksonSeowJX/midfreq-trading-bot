import os
import pandas as pd
from pathlib import Path

class DataStorage:
    def __init__(self, base_path: str = "data"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(exist_ok=True)

    def _get_file_path(self, symbol: str, timeframe: str) -> Path:
        symbol_dir = self.base_path / symbol.upper()
        symbol_dir.mkdir(parents=True, exist_ok=True)
        return symbol_dir / f"{timeframe}.parquet"

    def save_data(self, df: pd.DataFrame, symbol: str, timeframe: str):
        """
        Save DataFrame to a Parquet file.
        """
        file_path = self._get_file_path(symbol, timeframe)
        # Use pyarrow for parquet
        df.to_parquet(file_path, engine='pyarrow', index=True)
        print(f"Data saved to {file_path}")

    def load_data(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """
        Load DataFrame from a Parquet file.
        """
        file_path = self._get_file_path(symbol, timeframe)
        if not file_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(file_path, engine='pyarrow')

    def append_data(self, df: pd.DataFrame, symbol: str, timeframe: str):
        """
        Append new data to existing Parquet file, ensuring no duplicates.
        """
        existing_df = self.load_data(symbol, timeframe)
        if existing_df.empty:
            self.save_data(df, symbol, timeframe)
        else:
            # Combine and remove duplicates based on index (expected to be timestamp)
            combined_df = pd.concat([existing_df, df])
            # Drop duplicates by index (assuming index is sorted timestamp)
            combined_df = combined_df[~combined_df.index.duplicated(keep='last')].sort_index()
            self.save_data(combined_df, symbol, timeframe)
