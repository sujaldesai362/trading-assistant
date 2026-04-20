from fastapi import FastAPI
from typing import Optional, List, Dict, Any
import os
import requests

app = FastAPI(title="Trading Assistant API")

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")


def get_stock_price(symbol: str):
    if not POLYGON_API_KEY:
        return None

    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev"
    r = requests.get(url, params={"apiKey": POLYGON_API_KEY}, timeout=15)
    data = r.json()

    if "results" not in data or not data["results"]:
        return None

    return data["results"][0]["c"]


def build_trade(symbol: str, price: float, expiry: Optional[str], option_type: Optional[str]) -> Dict[str, Any]:
    opt_type = (option_type or "call").lower()

    if opt_type == "put":
        bias = "bearish"
        strategy = "bear_put_spread"
        buy_strike = round(price)
        sell_strike = buy_strike - 3
        legs = [
            {"action": "buy", "strike": buy_strike, "type": "put"},
            {"action": "sell", "strike": sell_strike, "type": "put"},
        ]
        why_it_works = f"{symbol} is trading around {price:.2f}; defined-risk bearish structure."
        what_could_kill_it = "Bullish reversal, strong market strength, or weak downside follow-through."
    else:
        bias = "bullish"
        strategy = "bull_call_spread"
        buy_strike = round(price)
        sell_strike = buy_strike + 3
        legs = [
            {"action": "buy", "strike": buy_strike, "type": "call"},
            {"action": "sell", "strike": sell_strike, "type": "call"},
        ]
        why_it_works = f"{symbol} is trading around {price:.2f}; defined-risk bullish structure."
        what_could_kill_it = "Bearish reversal, weak momentum, or bad market reaction."

    return {
        "best_trade": {
            "ticker": symbol.upper(),
            "bias": bias,
            "strategy": strategy,
            "expiry": expiry or "2026-04-24",
            "option_type": opt_type,
            "legs": legs,
            "estimated_entry": "Limit debit near mid-price",
            "position_size": 1,
            "max_loss": 200,
            "max_profit": 100,
            "confidence_score": 0.65,
            "why_it_works": why_it_works,
            "what_could_kill_it": what_could_kill_it,
        },
        "backup_trade": None,
        "no_trade_reason": None,
    }


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/scan/symbol")
def scan_symbol(
    symbol: str,
    expiry: Optional[str] = None,
    option_type: Optional[str] = None
):
    if not POLYGON_API_KEY:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": "POLYGON_API_KEY is missing from environment variables."
        }

    price = get_stock_price(symbol)

    if price is None:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": f"No live price data found for {symbol.upper()}."
        }

    return build_trade(symbol, price, expiry, option_type)


@app.get("/scan/watchlist")
def scan_watchlist(
    option_type: Optional[str] = None,
    max_results: int = 3
):
    if not POLYGON_API_KEY:
        return {
            "results": [],
            "no_trade_reason": "POLYGON_API_KEY is missing from environment variables."
        }

    tickers = ["HOOD", "NVDA", "AMD", "AAPL", "QQQ", "SPY"]
    results: List[Dict[str, Any]] = []

    for ticker in tickers[:max_results]:
        price = get_stock_price(ticker)
        if price is None:
            continue

        trade = build_trade(ticker, price, None, option_type)["best_trade"]
        results.append(trade)

    if not results:
        return {
            "results": [],
            "no_trade_reason": "No live data returned for watchlist."
        }

    return {"results": results}