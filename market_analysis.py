"""Read-only Bybit market analysis for Token-inform. No trading API keys required."""
import asyncio
import html
import logging
import math
import time
import aiohttp

LOG = logging.getLogger(__name__)
BYBIT_URL = 'https://api.bybit.com/v5/market/kline'
SYMBOLS = {'cap': 'CAPUSDT', 'ethereum': 'ETHUSDT', 'ripple': 'XRPUSDT', 'solana': 'SOLUSDT'}


def ema(values, period):
    if len(values) < period:
        return None
    result = sum(values[:period]) / period
    weight = 2 / (period + 1)
    for value in values[period:]:
        result += weight * (value - result)
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None
    diffs = [b - a for a, b in zip(values, values[1:])]
    gains = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    gain = sum(gains[:period]) / period
    loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        gain = (gain * (period - 1) + g) / period
        loss = (loss * (period - 1) + l) / period
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def atr(candles, period=14):
    if len(candles) <= period:
        return None
    ranges = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        ranges.append(max(c['high'] - c['low'], abs(c['high'] - prev['close']), abs(c['low'] - prev['close'])))
    result = sum(ranges[:period]) / period
    for tr in ranges[period:]:
        result = (result * (period - 1) + tr) / period
    return result


async def fetch_candles(session, symbol, interval, limit=150):
    params = {'category': 'linear', 'symbol': symbol, 'interval': str(interval), 'limit': limit}
    async with session.get(BYBIT_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
        response.raise_for_status()
        data = await response.json()
    if data.get('retCode') != 0:
        raise ValueError(f"Bybit: {data.get('retMsg', 'невідома помилка')}")
    rows = data.get('result', {}).get('list') or []
    if len(rows) < 40:
        raise ValueError(f'Недостатньо свічок {symbol} ({interval}m): {len(rows)}')
    candles = [{'start': int(x[0]), 'open': float(x[1]), 'high': float(x[2]),
                'low': float(x[3]), 'close': float(x[4]), 'volume': float(x[5])} for x in rows]
    candles.sort(key=lambda x: x['start'])
    # Exclude currently forming candle; indicators and volume use completed bars only.
    interval_ms = int(interval) * 60000
    if candles and candles[-1]['start'] + interval_ms > int(time.time() * 1000):
        candles.pop()
    if len(candles) < 35:
        raise ValueError('Недостатньо закритих свічок')
    return candles


def summarize(candles, interval):
    closes = [x['close'] for x in candles]
    last = closes[-1]
    avgs = {p: ema(closes, p) for p in (7, 14, 28)}
    strength = rsi(closes)
    volatility = atr(candles)
    lookback = candles[-48:]
    support = min(c['low'] for c in lookback[:-1])
    resistance = max(c['high'] for c in lookback[:-1])
    volume_avg = sum(c['volume'] for c in candles[-21:-1]) / 20
    volume_ratio = candles[-1]['volume'] / volume_avg if volume_avg else None
    if last < avgs[7] < avgs[14] < avgs[28]:
        trend = 'спадний (EMA впорядковані)'
    elif last > avgs[7] > avgs[14] > avgs[28]:
        trend = 'висхідний (EMA впорядковані)'
    else:
        trend = 'змішаний / без чіткого підтвердження'
    return {'interval': interval, 'last': last, 'ema': avgs, 'rsi': strength,
            'atr': volatility, 'support': support, 'resistance': resistance,
            'volume_ratio': volume_ratio, 'trend': trend}


def price(x):
    return f'{x:.5f}' if x < 1 else f'{x:.3f}'


def format_report(symbol, frames, automatic=False):
    m = frames[15]
    low, high = m['support'], m['resistance']
    span = high - low
    fib = [(0.382, high - span * .382), (0.5, high - span * .5), (0.618, high - span * .618)]
    lines = [f"📊 <b>{html.escape(symbol)} · {'Автоаналіз імпульсу' if automatic else 'Аналіз ринку'}</b>",
             'Джерело: Bybit USDT Perpetual · лише закриті свічки',
             f"Останнє закриття 15m: <b>{price(m['last'])}</b>", '']
    for interval in (5, 15, 60):
        s = frames[interval]
        lines.append(f"⏱ {interval}m: {s['trend']}; RSI14 {s['rsi']:.1f}")
    lines += ['', '<b>15m — індикатори</b>',
              f"EMA 7/14/28: {price(m['ema'][7])} / {price(m['ema'][14])} / {price(m['ema'][28])}",
              f"ATR14: {price(m['atr'])} ({m['atr']/m['last']*100:.2f}% ціни)",
              f"Обсяг останньої свічки / середній 20: {m['volume_ratio']:.2f}×",
              '', '<b>Діапазон 15m (останні 48 свічок)</b>',
              f"Нижня межа: {price(low)} · верхня: {price(high)}",
              'Рівні відкату від максимуму діапазону (умовна сітка):']
    lines.extend(f"Fib {pct*100:.1f}%: {price(level)}" for pct, level in fib)
    lines += ['', '<b>Умовні сценарії</b>',
              f"⬇️ Закриття 15m нижче {price(low)} з подальшим утриманням нижче підтримує сценарій продовження падіння; повернення вище рівня його послаблює.",
              f"⬆️ Закриття 15m вище {price(high)} з утриманням вище підтримує сценарій зростання; повернення нижче рівня його послаблює.",
              '⚠️ Це технічні орієнтири, не прогноз і не сигнал для угоди. Рівні Fib залежать від вибору екстремумів.']
    return '\n'.join(lines)


async def analyze(currency_id, automatic=False):
    symbol = SYMBOLS.get(currency_id)
    if not symbol:
        raise ValueError('Валюта не підтримується для аналізу Bybit')
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        series = await asyncio.gather(*(fetch_candles(session, symbol, i) for i in (5, 15, 60)))
    frames = {i: summarize(c, i) for i, c in zip((5, 15, 60), series)}
    return format_report(symbol, frames, automatic=automatic)
