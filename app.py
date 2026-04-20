from fastapi import FastAPI
from typing import Optional, List, Dict, Any
import os
import requests

app = FastAPI(title="Trading Assistant API")

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io"


# -----------------------------
# Helpers
# -----------------------------
def polygon_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not POLYGON_API_KEY:
        return {}

    query = dict(params or {})
    query["apiKey"] = POLYGON_API_KEY

    try:
        r = requests.get(f"{BASE_URL}{path}", params=query, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def get_stock_price(symbol: str) -> Optional[float]:
    data = polygon_get(f"/v2/aggs/ticker/{symbol}/prev")
    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("c")


def get_options_chain(symbol: str, expiry: Optional[str] = None, option_type: Optional[str] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "underlying_ticker": symbol,
        "limit": 250,
        "sort": "expiration_date",
        "order": "asc",
    }
    if expiry:
        params["expiration_date"] = expiry

    data = polygon_get("/v3/snapshot/options", params)
    rows = data.get("results", []) or []

    cleaned: List[Dict[str, Any]] = []

    for row in rows:
        details = row.get("details", {}) or {}
        greeks = row.get("greeks", {}) or {}
        quote = row.get("last_quote", {}) or {}
        day = row.get("day", {}) or {}

        contract_type = str(details.get("contract_type", "")).lower()
        if option_type and contract_type != option_type.lower():
            continue

        bid = float(quote.get("bid", 0) or 0)
        ask = float(quote.get("ask", 0) or 0)
        strike = float(details.get("strike_price", 0) or 0)
        delta = greeks.get("delta", None)
        expiry_date = details.get("expiration_date", None)
        volume = int(day.get("volume", 0) or 0)
        open_interest = int(row.get("open_interest", 0) or 0)
        iv = float(row.get("implied_volatility", 0) or 0)

        if not expiry_date or strike <= 0:
            continue
        if bid <= 0 or ask <= 0:
            continue
        if delta is None:
            continue

        mid = (bid + ask) / 2
        spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999

        cleaned.append({
            "symbol": row.get("ticker"),
            "expiry": expiry_date,
            "option_type": contract_type,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "mid": round(mid, 4),
            "spread_pct": round(spread_pct, 2),
            "delta": float(delta),
            "volume": volume,
            "open_interest": open_interest,
            "iv": iv,
        })

    return cleaned


def score_contract(c: Dict[str, Any]) -> float:
    liquidity = min(c["open_interest"] / 1500, 1.0) * 0.45 + min(c["volume"] / 500, 1.0) * 0.35
    spread = max(0.0, 1 - c["spread_pct"] / 10) * 0.20
    return round(liquidity + spread, 4)


def filter_candidates(chain: List[Dict[str, Any]], option_type: str) -> List[Dict[str, Any]]:
    out = []
    for c in chain:
        if c["option_type"] != option_type:
            continue
        if c["open_interest"] < 100:
            continue
        if c["volume"] < 10:
            continue
        if c["spread_pct"] > 12:
            continue

        abs_delta = abs(c["delta"])
        if abs_delta < 0.25 or abs_delta > 0.70:
            continue

        out.append(c)
    return out


def choose_long_option(candidates: List[Dict[str, Any]], direction: str) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    target_delta = 0.45
    best = None
    best_score = -999.0

    for c in candidates:
        structure = score_contract(c)
        delta_fit = 1 - abs(abs(c["delta"]) - target_delta)
        total = structure + delta_fit
        if total > best_score:
            best_score = total
            best = c

    if not best:
        return None

    strategy = "long_call" if direction == "bullish" else "long_put"
    return {
        "ticker": best["symbol"],
        "bias": direction,
        "strategy": strategy,
        "expiry": best["expiry"],
        "option_type": best["option_type"],
        "legs": [
            {"action": "buy", "strike": best["strike"], "type": best["option_type"]}
        ],
        "estimated_entry": f"Limit near {best['mid']:.2f}",
        "position_size": 1,
        "max_loss": round(best["ask"] * 100, 2),
        "max_profit": None,
        "confidence_score": round(min(0.95, 0.55 + best_score / 3), 2),
        "why_it_works": f"Good liquidity, manageable spread, and usable delta ({best['delta']:.2f}).",
        "what_could_kill_it": "Momentum failure, widening spread, or a fast reversal.",
        "_internal_score": best_score,
    }


def choose_vertical_spread(candidates: List[Dict[str, Any]], direction: str) -> Optional[Dict[str, Any]]:
    if len(candidates) < 2:
        return None

    best_trade = None
    best_score = -999.0

    sorted_chain = sorted(candidates, key=lambda x: x["strike"])

    if direction == "bullish":
        longs = [c for c in sorted_chain if 0.30 <= c["delta"] <= 0.65]
        for long_leg in longs:
            shorts = [s for s in sorted_chain if s["strike"] > long_leg["strike"]]
            for short_leg in shorts[:4]:
                width = (short_leg["strike"] - long_leg["strike"]) * 100
                debit = (long_leg["ask"] - short_leg["bid"]) * 100
                if width <= 0 or debit <= 0:
                    continue
                max_profit = width - debit
                if max_profit <= 0:
                    continue

                rr = max_profit / debit
                score = (
                    (score_contract(long_leg) + score_contract(short_leg)) / 2
                    + rr * 0.15
                )
                if score > best_score:
                    best_score = score
                    best_trade = {
                        "ticker": long_leg["symbol"],
                        "bias": "bullish",
                        "strategy": "bull_call_spread",
                        "expiry": long_leg["expiry"],
                        "option_type": "call",
                        "legs": [
                            {"action": "buy", "strike": long_leg["strike"], "type": "call"},
                            {"action": "sell", "strike": short_leg["strike"], "type": "call"},
                        ],
                        "estimated_entry": f"Limit debit near {debit/100:.2f}",
                        "position_size": 1,
                        "max_loss": round(debit, 2),
                        "max_profit": round(max_profit, 2),
                        "confidence_score": round(min(0.95, 0.58 + score / 3), 2),
                        "why_it_works": "Defined risk, better capital efficiency than a naked call, and acceptable spread/liquidity.",
                        "what_could_kill_it": "Failure to move up enough before expiration or spread widening.",
                        "_internal_score": score,
                    }

    else:
        sorted_chain = sorted(candidates, key=lambda x: x["strike"], reverse=True)
        longs = [c for c in sorted_chain if -0.65 <= c["delta"] <= -0.30]
        for long_leg in longs:
            shorts = [s for s in sorted_chain if s["strike"] < long_leg["strike"]]
            for short_leg in shorts[:4]:
                width = (long_leg["strike"] - short_leg["strike"]) * 100
                debit = (long_leg["ask"] - short_leg["bid"]) * 100
                if width <= 0 or debit <= 0:
                    continue
                max_profit = width - debit
                if max_profit <= 0:
                    continue

                rr = max_profit / debit
                score = (
                    (score_contract(long_leg) + score_contract(short_leg)) / 2
                    + rr * 0.15
                )
                if score > best_score:
                    best_score = score
                    best_trade = {
                        "ticker": long_leg["symbol"],
                        "bias": "bearish",
                        "strategy": "bear_put_spread",
                        "expiry": long_leg["expiry"],
                        "option_type": "put",
                        "legs": [
                            {"action": "buy", "strike": long_leg["strike"], "type": "put"},
                            {"action": "sell", "strike": short_leg["strike"], "type": "put"},
                        ],
                        "estimated_entry": f"Limit debit near {debit/100:.2f}",
                        "position_size": 1,
                        "max_loss": round(debit, 2),
                        "max_profit": round(max_profit, 2),
                        "confidence_score": round(min(0.95, 0.58 + score / 3), 2),
                        "why_it_works": "Defined risk, better capital efficiency than a naked put, and acceptable spread/liquidity.",
                        "what_could_kill_it": "Failure to move down enough before expiration or spread widening.",
                        "_internal_score": score,
                    }

    return best_trade


def build_real_trade(symbol: str, expiry: Optional[str], option_type: Optional[str]) -> Dict[str, Any]:
    if not POLYGON_API_KEY:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": "POLYGON_API_KEY is missing from environment variables."
        }

    stock_price = get_stock_price(symbol)
    if stock_price is None:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": f"No stock price data found for {symbol.upper()}."
        }

    desired_type = (option_type or "call").lower()
    direction = "bullish" if desired_type == "call" else "bearish"

    chain = get_options_chain(symbol, expiry=expiry, option_type=desired_type)
    if not chain:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": f"No options chain data returned for {symbol.upper()}."
        }

    candidates = filter_candidates(chain, desired_type)
    if not candidates:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": f"No liquid {desired_type} candidates passed filters for {symbol.upper()}."
        }

    single = choose_long_option(candidates, direction)
    spread = choose_vertical_spread(candidates, direction)

    valid = [x for x in [single, spread] if x is not None]
    if not valid:
        return {
            "best_trade": None,
            "backup_trade": None,
            "no_trade_reason": "No valid strategy qualified after ranking."
        }

    ranked = sorted(valid, key=lambda x: x["_internal_score"], reverse=True)
    best_trade = ranked[0]
    backup_trade = ranked[1] if len(ranked) > 1 else None

    # clean internal key
    best_trade.pop("_internal_score", None)
    if backup_trade:
        backup_trade.pop("_internal_score", None)

    # enrich with stock context
    best_trade["why_it_works"] = f"{best_trade['why_it_works']} Underlying stock price is around {stock_price:.2f}."
    if backup_trade:
        backup_trade["why_it_works"] = f"{backup_trade['why_it_works']} Underlying stock price is around {stock_price:.2f}."

    return {
        "best_trade": best_trade,
        "backup_trade": backup_trade,
        "no_trade_reason": None,
    }


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/scan/symbol")
def scan_symbol(symbol: str, expiry: Optional[str] = None, option_type: Optional[str] = None):
    return build_real_trade(symbol, expiry, option_type)


@app.get("/scan/watchlist")
def scan_watchlist(option_type: Optional[str] = None, max_results: int = 3):
    watchlist = ["HOOD", "NVDA", "AMD", "AAPL", "QQQ", "SPY"]
    results: List[Dict[str, Any]] = []

    for ticker in watchlist:
        trade = build_real_trade(ticker, None, option_type)
        if trade.get("best_trade"):
            best = trade["best_trade"]
            score = best.get("confidence_score", 0)
            results.append({
                "ticker": ticker,
                "best_trade": best,
                "backup_trade": trade.get("backup_trade"),
                "score": score,
            })

    ranked = sorted(results, key=lambda x: x["score"], reverse=True)
    return {"results": ranked[:max_results]}