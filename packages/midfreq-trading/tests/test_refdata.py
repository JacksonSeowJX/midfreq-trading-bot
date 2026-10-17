"""ReferenceDataService: cached instruments with the venue's lot rule as a fallback, calendars,
and corporate actions from the JSON file and venue cash-flow sources."""
import json
import logging
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from midfreq.core.model.identifiers import InstrumentId, Venue
from midfreq.core.model.instruments import CurrencyPair, Equity
from midfreq.core.paths import config_dir
from midfreq.core.venues.spec import default_venues
from midfreq.data.adapters.base import ConnectorError, InstrumentProvider
from midfreq.trading.calendar import VenueCalendar
from midfreq.trading.corporate_actions import (
    CashFlow, JsonCorporateActionSource, VenueCashFlowSource, merge_actions, parse_action,
)
from midfreq.trading.interfaces import CorporateAction
from midfreq.trading.refdata import ReferenceDataService

IID = InstrumentId.from_str


class FakeProvider(InstrumentProvider):
    """Answers lot sizes from a table; counts how often it is asked."""

    def __init__(self, lots, fail=False):
        super().__init__(SimpleNamespace(name="fake", venue_spec=default_venues().get))
        self.lots = lots
        self.fail = fail
        self.batches = []

    def _load(self, instrument_ids):
        self.batches.append([str(i) for i in instrument_ids])
        if self.fail:
            raise ConnectorError("OpenD is not running")
        out = []
        for iid in instrument_ids:
            if str(iid) in self.lots:
                spec = default_venues().get(iid.venue)
                out.append(Equity(id=iid, raw_symbol=str(iid.symbol), currency=spec.currency,
                                  price_increment=spec.price_increment, lot_size=self.lots[str(iid)]))
        return out


def test_instruments_are_loaded_once_and_batched_per_venue():
    hk = FakeProvider({"00700.XHKG": 100, "00005.XHKG": 400})
    us = FakeProvider({"AAPL.XNAS": 1})
    ref = ReferenceDataService({"XHKG": hk, "XNAS": us})
    insts = ref.instruments(["00700.XHKG", "AAPL.XNAS", "00005.XHKG", "00700.XHKG"])
    assert [str(i.id) for i in insts] == ["00700.XHKG", "AAPL.XNAS", "00005.XHKG", "00700.XHKG"]
    assert hk.batches == [["00700.XHKG", "00005.XHKG"]] and us.batches == [["AAPL.XNAS"]]
    assert ref.instrument("00005.XHKG").lot_size == 400 and ref.lot_size("00700.XHKG") == 100
    assert ref.lot_sizes(["00700.XHKG", "00005.XHKG"]) == {IID("00700.XHKG"): 100, IID("00005.XHKG"): 400}
    assert ref.provider_calls == 2 and len(hk.batches) == 1
    ref.refresh(["00700.XHKG"])
    ref.instrument("00700.XHKG")
    assert ref.provider_calls == 3 and len(hk.batches) == 1     # asked again; its own cache answered
    assert ref.find("AAPL.XNAS") is not None and ref.find("MSFT.XNAS") is None


def test_a_failed_provider_falls_back_to_the_venues_lot_rule(caplog):
    hk = FakeProvider({}, fail=True)
    ref = ReferenceDataService({"XHKG": hk})
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.refdata"):
        inst = ref.instrument("09988.XHKG")
    assert inst.lot_size == 100 and inst.currency == "HKD"
    assert "provider failed" in inst.info["lot_size_source"]
    assert any("odd lots" in r.getMessage() for r in caplog.records)
    ref.instrument("09988.XHKG")
    assert len(hk.batches) == 1                         # the fallback is cached too
    hk.fail, hk.lots = False, {"09988.XHKG": 50}         # the provider is back
    ref.refresh()
    assert ref.instrument("09988.XHKG").lot_size == 50 and len(hk.batches) == 2


def test_venues_without_a_provider_and_instruments_it_does_not_know():
    ref = ReferenceDataService({"XHKG": FakeProvider({"00700.XHKG": 100})})
    assert ref.instrument("AAPL.XNYS").lot_size == 1     # fixed rule: 1 share
    unknown = ref.instrument("00700.XHKG"), ref.instrument("99999.XHKG")
    assert unknown[0].lot_size == 100 and unknown[1].lot_size == 100
    assert "no instrument definition" in unknown[1].info["lot_size_source"]
    btc = ref.instrument("BTC-USDT.OKX")
    assert isinstance(btc, CurrencyPair) and btc.quantity_step > 0


def test_one_unknown_code_does_not_cost_the_batch_its_real_lots():
    hk = FakeProvider({"00005.XHKG": 400, "00700.XHKG": 100})
    ref = ReferenceDataService({"XHKG": hk})
    insts = ref.instruments(["00005.XHKG", "99999.XHKG", "00700.XHKG"])
    assert [i.lot_size for i in insts] == [400, 100, 100]
    assert "lot_size_source" not in insts[0].info and "venue lot rule" in insts[1].info["lot_size_source"]
    assert len(hk.batches) == 1


def test_a_provider_instrument_without_a_lot_gets_the_venue_rule():
    hk = FakeProvider({"00700.XHKG": 0})
    inst = ReferenceDataService({"XHKG": hk}).instrument("00700.XHKG")
    assert inst.lot_size == 100 and inst.info["lot_size_source"] == "venue lot rule"


def test_from_connectors_prefers_the_venues_default_connector():
    venues = default_venues()
    moomoo_provider, other_provider = object(), object()

    def connector(name, provider, served):
        return SimpleNamespace(name=name, venues=venues, instrument_provider=provider,
                               served_venues=lambda: [Venue(v) for v in served])
    ref = ReferenceDataService.from_connectors([connector("yfinance", other_provider, ["XHKG", "XNAS"]),
                                                connector("moomoo", moomoo_provider, ["XHKG"])])
    assert ref._providers[Venue("XHKG")] is moomoo_provider
    assert ref._providers[Venue("XNAS")] is other_provider


def test_calendars_come_from_venues_json_and_are_kept():
    ref = ReferenceDataService({})
    cal = ref.calendar("XHKG")
    assert isinstance(cal, VenueCalendar) and ref.calendar(Venue("XHKG")) is cal
    assert not cal.is_trading_day(date(2026, 10, 19))
    custom = object()
    assert ReferenceDataService({}, calendars={"XNYS": custom}).calendar("XNYS") is custom


# ── corporate actions ──────────────────────────────────────────────────────────────────────────

def write_actions(path, entries):
    path.write_text(json.dumps(entries))
    return JsonCorporateActionSource(path)


def test_the_repositorys_corporate_actions_file_is_an_empty_list():
    assert json.loads((config_dir() / "corporate_actions.json").read_text()) == []
    assert JsonCorporateActionSource().all() == []


def test_corporate_actions_from_the_file(tmp_path):
    src = write_actions(tmp_path / "ca.json", [
        {"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "2026-08-14", "amount": 0.1,
         "currency": "USD", "source": "HSBC interim", "pay_date": "2026-09-25"},
        {"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "2026-03-10", "amount": 0.36, "currency": "USD"},
        {"instrument_id": "NVDA.XNAS", "kind": "split", "ex_date": "2026-06-10", "amount": 10},
    ])
    ref = ReferenceDataService({}, corporate_actions=src)
    hsbc = ref.corporate_actions("00005.XHKG", date(2026, 1, 1), date(2026, 12, 31))
    assert [(a.ex_date, a.amount) for a in hsbc] == [(date(2026, 3, 10), 0.36), (date(2026, 8, 14), 0.1)]
    assert hsbc[1].source == "HSBC interim" and hsbc[0].source == "file:ca.json"
    assert ref.corporate_actions("00005.XHKG", date(2026, 8, 14), date(2026, 8, 14)) == [hsbc[1]]
    assert ref.corporate_actions(IID("NVDA.XNAS"), date(2026, 6, 1), date(2026, 6, 30))[0].kind == "split"
    assert ref.corporate_actions("AAPL.XNAS", date(2026, 1, 1), date(2026, 12, 31)) == []


@pytest.mark.parametrize("entry, problem", [
    ({"instrument_id": "00005.XHKG", "kind": "bonus", "ex_date": "2026-08-14", "amount": 1}, "kind"),
    ({"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "14/08/2026", "amount": 1}, "ISO date"),
    ({"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "2026-08-14", "amount": 0}, "positive"),
    ({"instrument_id": "00005.XHKG", "kind": "dividend", "amount": 1}, "lacks"),
])
def test_a_malformed_entry_is_an_error(tmp_path, entry, problem):
    src = write_actions(tmp_path / "ca.json", [entry])
    with pytest.raises(ValueError, match=problem):
        src.all()


class FakeCashFlows(VenueCashFlowSource):
    def __init__(self, venue, flows):
        super().__init__(venue)
        self.flows = flows
        self.asked = []

    def cash_flows(self, start, end):
        self.asked.append((start, end))
        return [f for f in self.flows if start <= f.ts.date() <= end]


def test_venue_cash_flows_add_dividends_the_file_does_not_have(tmp_path):
    src = write_actions(tmp_path / "ca.json", [
        {"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "2026-08-14", "amount": 0.1, "currency": "USD"}])
    hsbc, tencent = IID("00005.XHKG"), IID("00700.XHKG")
    flows = FakeCashFlows("XHKG", [
        CashFlow(Venue("XHKG"), "dividend", 312.0, "USD", pd.Timestamp("2026-09-25 02:00", tz="UTC"),
                 instrument_id=hsbc, per_share=0.1, ex_date=date(2026, 8, 14), reference="cf-1"),
        CashFlow(Venue("XHKG"), "dividend", 450.0, "HKD", pd.Timestamp("2026-06-05 02:00", tz="UTC"),
                 instrument_id=tencent, per_share=4.5, ex_date=date(2026, 5, 15), reference="cf-2"),
        CashFlow(Venue("XHKG"), "dividend", 80.0, "HKD", pd.Timestamp("2026-07-01 02:00", tz="UTC"),
                 instrument_id=tencent, description="no per-share amount"),
        CashFlow(Venue("XHKG"), "interest", 1.5, "HKD", pd.Timestamp("2026-07-02 02:00", tz="UTC")),
    ])
    ref = ReferenceDataService({}, corporate_actions=src, cash_flow_sources=[flows])
    assert len(ref.corporate_actions(hsbc, date(2026, 1, 1), date(2026, 12, 31))) == 1   # same action once
    assert ref.corporate_actions(hsbc, date(2026, 1, 1), date(2026, 12, 31))[0].source == "file:ca.json"
    ten = ref.corporate_actions(tencent, date(2026, 5, 1), date(2026, 5, 31))
    assert ten == [CorporateAction(tencent, "dividend", date(2026, 5, 15), 4.5, "HKD", "venue:XHKG cf-2")]
    assert flows.asked[-1] == (date(2026, 5, 1), date(2026, 8, 29))                       # 90 days past the end
    cash = ref.cash_flows("XHKG", date(2026, 1, 1), date(2026, 12, 31))
    assert [f.reference or f.kind for f in cash] == ["cf-2", "dividend", "interest", "cf-1"]
    assert ref.cash_flows("XNAS", date(2026, 1, 1), date(2026, 12, 31)) == []


def test_a_failing_cash_flow_source_leaves_the_files_answer(tmp_path, caplog):
    class Broken(VenueCashFlowSource):
        def cash_flows(self, start, end):
            raise ConnectorError("OpenD is not running")
    src = write_actions(tmp_path / "ca.json", [
        {"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "2026-08-14", "amount": 0.1}])
    ref = ReferenceDataService({}, corporate_actions=src, cash_flow_sources=[Broken("XHKG")])
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.refdata"):
        assert len(ref.corporate_actions("00005.XHKG", date(2026, 1, 1), date(2026, 12, 31))) == 1
    assert any("cash-flow source failed" in r.getMessage() for r in caplog.records)


def test_merge_keeps_the_first_of_identical_actions():
    a = parse_action({"instrument_id": "X.XNAS", "kind": "dividend", "ex_date": "2026-01-02", "amount": 1}, "first")
    b = parse_action({"instrument_id": "X.XNAS", "kind": "dividend", "ex_date": "2026-01-02", "amount": 1}, "second")
    c = parse_action({"instrument_id": "X.XNAS", "kind": "dividend", "ex_date": "2026-01-01", "amount": 1}, "third")
    assert [x.source for x in merge_actions([[a], [b, c]])] == ["third", "first"]
