from datetime import datetime

import pandas as pd

from core.storage import DataStorage


def _hourly(start, periods):
    idx = pd.date_range(start, periods=periods, freq='h', tz='UTC', name='timestamp')
    return pd.DataFrame({'open': 1.0, 'high': 1.0, 'low': 1.0, 'close': 1.0,
                         'volume': 100.0}, index=idx)


def _storage(tmp_path):
    st = DataStorage(str(tmp_path / 'data'))
    st.save_data(_hourly('2026-08-20 10:00', 60), 'US.AAA', '1h')   # to 08-22 21:00
    st.save_data(_hourly('2026-08-20 10:00', 40), 'US.BBB', '1h')   # to 08-22 01:00
    return st


def test_latest_common_timestamp_is_the_minimum_across_symbols(tmp_path):
    st = _storage(tmp_path)
    assert st.latest_common_timestamp(['US.AAA', 'US.BBB'], '1h') == datetime(2026, 8, 22, 1)


def test_as_of_ignores_candles_after_that_day(tmp_path):
    st = _storage(tmp_path)
    # Both symbols have candles through the end of 21 Aug, so the common end
    # is the last candle of that day, as it was before the later ones existed.
    assert st.latest_common_timestamp(['US.AAA', 'US.BBB'], '1h',
                                      as_of='2026-08-21') == datetime(2026, 8, 21, 23)
    assert st.latest_common_timestamp(['US.AAA'], '1h',
                                      as_of=datetime(2026, 8, 20, 15, 30)) == datetime(2026, 8, 20, 23)


def test_as_of_before_a_symbols_first_candle_skips_it(tmp_path):
    st = _storage(tmp_path)
    st.save_data(_hourly('2026-08-25 10:00', 5), 'US.CCC', '1h')
    assert st.latest_common_timestamp(['US.AAA', 'US.CCC'], '1h',
                                      as_of='2026-08-21') == datetime(2026, 8, 21, 23)
