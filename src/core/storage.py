import os
import pandas as pd
from pathlib import Path

class DataStorage:
    def __init__(self, base_path: str = "data"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(exist_ok=True)

    def _get_file_path(self, symbol: str, timeframe: str) -> Path:
        # Convert symbol format: "HK.00700" -> "HK_00700" to match folder names
        folder_name = symbol.upper().replace(".", "_")
        symbol_dir = self.base_path / folder_name
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

    def latest_common_timestamp(self, symbols: list, timeframe: str):
        """
        Latest candle timestamp available across ALL of `symbols`, as a naive
        datetime, or None if nothing is cached.

        Studies previously anchored their window to `datetime.now()`, which
        made every result depend on the day it was run: the same S&P 100
        study reported +1.28% on 24 Aug and -2.69% on 8 Sep purely because
        the trailing 3-year window had slid forward and the tail of it ran
        past the end of the cached data. Anchoring to the data instead makes
        a study reproducible for as long as the underlying cache is unchanged.

        The MINIMUM across symbols is used deliberately: a universe-ranking
        strategy should not be evaluated over a period where only some of its
        constituents have quotes.
        """
        latest = None
        for symbol in symbols:
            df = self.load_data(symbol.replace('.', '_'), timeframe)
            if df.empty:
                continue
            end = df.index.max()
            end = end.to_pydatetime().replace(tzinfo=None)
            latest = end if latest is None else min(latest, end)
        return latest
