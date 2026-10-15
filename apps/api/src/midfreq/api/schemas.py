"""
Response models of the dashboard API: the contract between apps/api and apps/web.

The web app's TypeScript types are generated from these (apps/api/openapi.json, exported by
`uv run python -m midfreq.api.export_openapi`), so a change here is a change of contract:
re-export, regenerate (`npm run gen:api` in apps/web) and update both sides.

Field names are snake_case on the wire. Times are ISO 8601 in UTC unless the name says
otherwise (`date` fields are the session's New York trading date).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── service ─────────────────────────────────────────────────────────────────

class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    sources: Dict[str, bool] = Field(description="which data sources this deployment can read: "
                                                 "results, live_sessions, data_cache")


# ── the bot's own record (live_sessions/*.jsonl, through a record store) ─────

class EquityPoint(BaseModel):
    timestamp: datetime
    pnl: float = Field(description="profit since the test began, account currency")
    value: float = Field(description="capital + pnl")


class ForwardTrade(BaseModel):
    date: date
    decided_on_candle: str
    side: Literal["BUY", "SELL"]
    stock: str
    shares: float
    price: float
    value: float
    realised_pnl: Optional[float] = None
    realised_pct: Optional[float] = None


class Holding(BaseModel):
    stock: str
    shares: float
    bought_at: float
    last_price: float
    value: float
    unrealised_pnl: float
    unrealised_pct: float


class ForwardTest(BaseModel):
    """The forward test as midfreq.research.forward_test.load computes it."""
    market: str
    capital: float
    pnl: float
    pnl_pct: float
    value: float
    sessions: int
    rebalances: int
    orders: int
    realised: float
    started: str = Field(description="first session date, YYYYMMDD")
    first_trade: Optional[str] = None
    as_of: Optional[datetime] = None
    equity: List[EquityPoint]
    trades: List[ForwardTrade]
    holdings: List[Holding]


class SessionSummary(BaseModel):
    id: str = Field(description="the session log's file name without .jsonl")
    market: str
    strategy: str
    symbols: int
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    candles: int
    trades: int
    crashed: bool = Field(description="its console log has a Traceback or CRITICAL line")
    timeframe: Optional[str] = None
    start_total_assets: Optional[float] = Field(
        default=None, description="the broker's account total when the session started, account currency")
    end_total_assets: Optional[float] = Field(
        default=None, description="the broker's account total when it ended; null if it has not ended")


class SessionTrade(BaseModel):
    timestamp: datetime
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    price: float


class SessionDetail(BaseModel):
    summary: SessionSummary
    equity: List[EquityPoint] = Field(description="the broker's account total at session start and end")
    trades: List[SessionTrade]
    event_counts: Dict[str, int]


# ── broker / exchange accounts (connectors' AccountClient) ──────────────────

class AccountInfo(BaseModel):
    id: str = Field(description="key in the accounts section of config/connections.yaml")
    label: str
    connector: str
    available: bool
    reason: str = ""
    kind: Literal["live", "snapshot"] = Field("live", description="live: read through a gateway now; "
                                              "snapshot: published by the VM every few minutes (see as_of)")


class AccountSnapshotOut(BaseModel):
    currency: str
    total_assets: float
    cash: float
    market_value: float


class PositionOut(BaseModel):
    symbol: str
    quantity: float
    average_price: Optional[float] = Field(
        default=None, description="cost basis per unit; null where the venue reports none (exchange spot balances)")
    market_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    instrument_id: Optional[str] = None


class OrderOut(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: float
    filled_quantity: float
    price: Optional[float] = None
    average_fill_price: Optional[float] = None
    status: str
    created: datetime
    updated: Optional[datetime] = None


class AccountView(BaseModel):
    id: str
    label: str
    connector: str
    as_of: datetime
    snapshot: AccountSnapshotOut
    positions: List[PositionOut]
    orders: List[OrderOut]


# ── research results (results/) ─────────────────────────────────────────────

class ResultRow(BaseModel):
    family: str
    scope: str
    strategy: str
    subject: str
    config_a_oos: float
    config_a_consistency: float
    config_b_oos: float
    config_b_consistency: float
    worst_config: float
    robust: bool
    study: str
    file: str


class ResultsSummary(BaseModel):
    total: int
    passed: int
    studies: int
    families: int
    pass_rate: float


class RealityCheckConfig(BaseModel):
    name: str
    p_value: float
    naive_p_value: Optional[float] = None
    best: str
    best_mean: float
    n_windows: Optional[int] = None
    n_candidates: Optional[int] = None
    luck_p95: Optional[float] = None
    means: Dict[str, float]


class SearchCorrection(BaseModel):
    file: str
    end: str
    n_boot: Optional[int] = None
    configs: List[RealityCheckConfig]
    pbo: Optional[Dict[str, Any]] = None


class Overview(BaseModel):
    summary: ResultsSummary
    rows: List[ResultRow]
    search_correction: Optional[SearchCorrection] = None


class ResearchFile(BaseModel):
    name: str
    study: str
    label: str = Field(description="human-readable name, as the Streamlit research view shows it")
    kind: Literal["csv", "json"]
    size: int
    modified: datetime


class ResearchFileContent(BaseModel):
    name: str
    kind: Literal["csv", "json"]
    columns: Optional[List[str]] = None
    rows: Optional[List[Dict[str, Any]]] = None
    data: Optional[Any] = Field(default=None, description="the parsed document, for JSON files")


# ── reference data ───────────────────────────────────────────────────────────

class StrategyParam(BaseModel):
    name: str
    label: str = Field(default="", description="the control's caption, as the registry gives it")
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


class StrategyInfo(BaseModel):
    name: str
    params: List[StrategyParam]


class Universe(BaseModel):
    name: str
    label: str
    market: Optional[str] = None
    symbols: int
    description: str = ""


class Candle(BaseModel):
    t: datetime = Field(description="the candle's close, as stored in the cache")
    o: float
    h: float
    l: float
    c: float
    v: float


# ── runs: backtest, compare, optimize (as background jobs) ──────────────────

class RiskSettings(BaseModel):
    """The Streamlit sidebar's risk and sizing controls. Percentages as fractions (0.03 = 3%)."""
    stop_loss_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_drawdown_pct: float = 0.10
    sizing: Literal["fixed_quantity", "percent_risk", "kelly", "equal_dollar"] = "fixed_quantity"
    qty: int = 100
    risk_pct: float = 0.02
    fraction: float = 0.5
    max_pct: float = 0.25


class RunSettings(BaseModel):
    """What every run needs. Single-stock strategies trade `symbol` (and `pair_symbol` for pairs);
    basket strategies (cross-sectional) trade the whole `universe`."""
    market: Literal["US", "HK"] = "US"
    symbol: str
    pair_symbol: Optional[str] = None
    universe: Optional[str] = None
    timeframe: Literal["1h", "1d"] = "1h"
    start: date
    end: date
    initial_capital: float = 100_000.0
    commission_rate: Optional[float] = Field(default=None, description="per side; None = the market's "
                                             "calibrated rate (HK 0.16%, US 0.005%)")
    slippage_bps: float = 0.0
    risk: RiskSettings = RiskSettings()


class BacktestRequest(RunSettings):
    strategy: str
    params: Dict[str, Any] = {}


class StrategyChoice(BaseModel):
    strategy: str
    params: Dict[str, Any] = {}


class CompareRequest(RunSettings):
    strategies: List[StrategyChoice] = Field(min_length=2, max_length=8)


class OptimizeRequest(RunSettings):
    strategy: str
    method: Literal["grid", "walk_forward"] = "walk_forward"
    objective: str = "sharpe_ratio"
    max_combinations: int = Field(200, ge=5, le=2000)
    n_splits: int = Field(3, ge=2, le=15)
    train_pct: float = Field(0.6, ge=0.5, le=0.9)


class SeriesPoint(BaseModel):
    t: datetime
    v: float


class TradeOut(BaseModel):
    timestamp: datetime
    symbol: str
    action: str
    qty: float
    price: float
    commission: float = 0.0
    pnl: Optional[float] = None


class Indicator(BaseModel):
    name: str
    pane: Literal["price", "lower"] = Field(description="drawn over the price, or in a pane below it")
    points: List[SeriesPoint]


class PriceChart(BaseModel):
    symbol: str
    candles: List[Candle]
    indicators: List[Indicator]


class BacktestResult(BaseModel):
    strategy: str
    symbols: List[str]
    params: Dict[str, Any]
    metrics: Dict[str, Any] = Field(description="the backtester's metrics: return_pct, sharpe_ratio, "
                                                "max_drawdown_pct, win_rate, total_trades, ...")
    equity: List[SeriesPoint]
    trades: List[TradeOut]
    chart: Optional[PriceChart] = None


class CompareRow(BaseModel):
    strategy: str
    params: Dict[str, Any]
    metrics: Dict[str, Any]


class CompareResult(BaseModel):
    rows: List[CompareRow]
    equity: Dict[str, List[SeriesPoint]] = Field(description="strategy name -> equity curve")


class GridRow(BaseModel):
    params: Dict[str, Any]
    metrics: Dict[str, Any]


class WalkForwardWindow(BaseModel):
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    params: Dict[str, Any]
    train_objective: Optional[float] = None
    test_metrics: Dict[str, Any]


class OptimizeResult(BaseModel):
    method: Literal["grid", "walk_forward"]
    objective: str
    rows: List[GridRow] = Field(default_factory=list, description="grid search, best first")
    windows: List[WalkForwardWindow] = Field(default_factory=list, description="walk-forward")
    summary: Dict[str, Any] = {}


class Job(BaseModel):
    id: str
    kind: Literal["backtest", "compare", "optimize"]
    status: Literal["queued", "running", "done", "failed"]
    progress: float = Field(0.0, description="0..1")
    message: str = ""
    created: datetime
    started: Optional[datetime] = None
    finished: Optional[datetime] = None
    error: Optional[str] = None
    request: Dict[str, Any]
    backtest: Optional[BacktestResult] = None
    compare: Optional[CompareResult] = None
    optimize: Optional[OptimizeResult] = None


class JobCapabilities(BaseModel):
    kinds: List[Literal["backtest", "compare", "optimize"]] = Field(description="the runs this deployment "
                                                                    "allows (the hosted site: backtest, compare)")
    max_concurrent: int
    objectives: Dict[str, str] = Field(description="objective key -> label, for optimize")
    markets: Dict[str, Dict[str, Any]] = Field(description="per market: default commission_rate, "
                                               "currency, symbols with cached data, universes")
