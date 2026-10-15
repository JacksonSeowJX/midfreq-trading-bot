"""Data ingestion: fetch bars from any configured venue into the catalog (`python -m mft.data`)."""
from mft.data.service import FetchResult, fetch_bars, instrument_id_for

__all__ = ["FetchResult", "fetch_bars", "instrument_id_for"]
