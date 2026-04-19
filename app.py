from fastapi import FastAPI
from typing import Optional

app = FastAPI(title="Trading Assistant API")


def mock_trade(symbol: str, expiry: Optional[str] = None, option_type: Optional[str] = None):
    return {
        "best_trade": {
            "ticker": symbol,
            "bias": "bullish",
            "strategy": "bull_call_spread",
            "expiry": expiry or "2026-04-24",
            "option_type": option_type or "call",
            "legs": [
                {"action": "buy", "strike": 91, "type": "call"},
                {"action": "sell", "strike": 94, "type": "call"}
            ],
            "estimated_entry": "Limit debit near mid-price",
            "position_size": 1,
            "max_loss": 220,
            "max_profit": 80,
            "confidence_score": 0.87,
            "why_it_works": "Bullish trend, supportive catalysts, and defined risk.",
            "what_could_kill_it": "Trend failure, weak volume follow-through, or bad market reaction."
        },
        "backup_trade": None,
        "no_trade_reason": None
    }


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/scan/symbol")
def scan_symbol(symbol: str, expiry: Optional[str] = None, option_type: Optional[str] = None):
    return mock_trade(symbol, expiry, option_type)


@app.get("/scan/watchlist")
def scan_watchlist(option_type: Optional[str] = None, max_results: int = 3):
    sample = []
    tickers = ["HOOD", "NVDA", "AMD"][:max_results]
    for t in tickers:
        sample.append(mock_trade(t, None, option_type)["best_trade"])
    return {"results": sample}