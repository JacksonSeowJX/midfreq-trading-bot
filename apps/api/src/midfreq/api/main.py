"""
The dashboard API: read-only JSON over what the Streamlit dashboard reads directly today.

    uv run uvicorn midfreq.api.main:app --reload          # http://127.0.0.1:8000/docs

Two independent sources for live trading, never merged: the bot's own record (session logs,
through a record store) and each broker or exchange account (through a connector's
AccountClient, configured in the `accounts` section of config/connections.yaml). The API never
talks to the trading bot process.

Contract: schemas.py. The routes below are the full v1 surface; implementations live in the
routers they call.
"""
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query

from midfreq.api import schemas as s

app = FastAPI(title="midfreq dashboard API", version="0.1.0",
              description="Read-only views of research results, the trading bot's record and "
                          "broker accounts.")


def _todo():
    raise HTTPException(status_code=501, detail="not implemented yet")


@app.get("/api/health", response_model=s.Health, tags=["service"])
def health():
    _todo()


@app.get("/api/forward-test", response_model=s.ForwardTest, tags=["bot record"],
         responses={404: {"description": "no forward-test session for this market"}})
def forward_test(market: str = Query("US", pattern="^(US|HK)$")):
    _todo()


@app.get("/api/sessions", response_model=List[s.SessionSummary], tags=["bot record"])
def sessions(market: Optional[str] = Query(None, pattern="^(US|HK)$"), limit: int = Query(50, ge=1, le=500)):
    """Recorded sessions, newest first."""
    _todo()


@app.get("/api/sessions/{session_id}", response_model=s.SessionDetail, tags=["bot record"],
         responses={404: {"description": "no such session"}})
def session(session_id: str):
    _todo()


@app.get("/api/accounts", response_model=List[s.AccountInfo], tags=["accounts"])
def accounts():
    """Configured accounts and whether each can be read now (a quick probe, never hangs)."""
    _todo()


@app.get("/api/accounts/{account_id}", response_model=s.AccountView, tags=["accounts"],
         responses={404: {"description": "no such account"},
                    503: {"description": "the account cannot be read now (gateway down, no key)"}})
def account(account_id: str, order_days: int = Query(14, ge=1, le=90)):
    _todo()


@app.get("/api/results/overview", response_model=s.Overview, tags=["research"])
def overview(current_only: bool = True):
    _todo()


@app.get("/api/research/files", response_model=List[s.ResearchFile], tags=["research"])
def research_files():
    _todo()


@app.get("/api/research/files/{name}", response_model=s.ResearchFileContent, tags=["research"],
         responses={404: {"description": "no such file"}})
def research_file(name: str, limit: int = Query(5000, ge=1, le=50000)):
    _todo()


@app.get("/api/strategies", response_model=List[s.StrategyInfo], tags=["reference"])
def strategies():
    _todo()


@app.get("/api/universes", response_model=List[s.Universe], tags=["reference"])
def universes():
    _todo()


@app.get("/api/candles", response_model=List[s.Candle], tags=["reference"],
         responses={404: {"description": "no cached candles for this symbol"}})
def candles(symbol: str, timeframe: str = Query("1h", pattern="^(1m|5m|1h|4h|1d)$"),
            start: Optional[str] = None, end: Optional[str] = None,
            limit: int = Query(5000, ge=1, le=100000)):
    _todo()
