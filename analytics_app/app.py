"""Standalone Token-inform Analytics Mini App (read-only market data)."""
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import httpx

app = FastAPI(title="Token-inform Analytics", version="0.1.0")
BYBIT = "https://api.bybit.com"
INDEX = Path(__file__).parent / "index.html"

async def bybit_get(endpoint: str, params: dict):
    async with httpx.AsyncClient(timeout=12) as client:
        try:
            response = await client.get(f"{BYBIT}{endpoint}", params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Bybit temporarily unavailable") from exc
    if data.get("retCode") != 0:
        raise HTTPException(status_code=502, detail="Bybit returned an error")
    return data["result"]

def validate(category: str, symbol: str):
    if category not in ("spot", "linear"):
        raise HTTPException(400, "Unsupported market")
    symbol = symbol.upper()
    if not symbol.endswith("USDT") or not symbol.isalnum() or len(symbol) > 24:
        raise HTTPException(400, "Invalid USDT symbol")
    return symbol

@app.get("/")
async def index():
    return FileResponse(INDEX)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/ticker")
async def ticker(category: str = "linear", symbol: str = "BTCUSDT"):
    symbol = validate(category, symbol)
    result = await bybit_get("/v5/market/tickers", {"category": category, "symbol": symbol})
    rows = result.get("list", [])
    if not rows:
        raise HTTPException(404, "Pair not found on selected market")
    item = rows[0]
    return {"market": category, "symbol": symbol, "price": item.get("lastPrice"),
            "change24h": item.get("price24hPcnt"), "volume24h": item.get("volume24h"),
            "turnover24h": item.get("turnover24h")}

@app.get("/api/candles")
async def candles(category: str = "linear", symbol: str = "BTCUSDT", interval: str = "15"):
    symbol = validate(category, symbol)
    if interval not in ("5", "15", "60", "240", "D"):
        raise HTTPException(400, "Unsupported interval")
    result = await bybit_get("/v5/market/kline",
                             {"category": category, "symbol": symbol, "interval": interval, "limit": 120})
    return {"market": category, "symbol": symbol, "interval": interval,
            "candles": [{"time": int(x[0]) // 1000, "open": x[1], "high": x[2],
                         "low": x[3], "close": x[4], "volume": x[5]} for x in reversed(result.get("list", []))]}
