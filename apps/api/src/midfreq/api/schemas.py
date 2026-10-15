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


class AccountSnapshotOut(BaseModel):
    currency: str
    total_assets: float
    cash: float
    market_value: float


class PositionOut(BaseModel):
    symbol: str
    quantity: float
    average_price: float
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
