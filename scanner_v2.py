import os
import json
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np

BASE = "https://www.okx.com"
PAPER_FILE = "paper_trades.json"
STAKE = 100.0
MAX_OPEN = 10
MAX_SCAN = 80
VERSION = "TREND_RIDER_V2"
FEE = float(os.getenv("OKX_TAKER_FEE_RATE", "0.001"))
SLIP = float(os.getenv("PAPER_SLIPPAGE_RATE", "0.0005"))


def now():
    return datetime.now(timezone.utc).isoformat()


def get(path, params):
    r = requests.get(BASE + path, params=params, timeout=20)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != "0":
        raise RuntimeError(d.get("msg", "OKX API error"))
    return d.get("data", [])


def f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def atr(df, n=14):
    pc = df.close.shift(1)
    tr = pd.concat([(df.high-df.low), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def adx(df, n=14):
    up = df.high.diff()
    dn = -df.low.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(df, n).replace(0, np.nan)
    pdi = 100 * plus.ewm(alpha=1/n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1/n, adjust=False).mean() / a
    dx = 100 * (pdi-mdi).abs() / (pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().fillna(0)


def candles(symbol, bar, limit=250):
    raw = get("/api/v5/market/candles", {"instId": symbol, "bar": bar, "limit": limit})
    rows = []
    for x in raw:
        if len(x) < 9:
            continue
        rows.append({"ts": int(x[0]), "open": f(x[1]), "high": f(x[2]), "low": f(x[3]), "close": f(x[4]), "volume": f(x[5]), "confirm": str(x[8])})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def symbols():
    data = get("/api/v5/public/instruments", {"instType": "SPOT"})
    out = []
    for x in data:
        s = x.get("instId", "")
        if x.get("state") == "live" and s.endswith("-USDT") and s[:-5].upper() not in {"USDT","USDC","USDE","USDS","USDG","DAI","FDUSD","TUSD","PYUSD"} and not s[:-5].startswith("X"):
            out.append(s)
    return out


def tickers():
    out = {}
    for x in get("/api/v5/market/tickers", {"instType": "SPOT"}):
        s = x.get("instId")
        if s and s.endswith("-USDT"):
            out[s] = {"last": f(x.get("last")), "vol": f(x.get("volCcy24h"))}
    return out


def score(main, htf):
    m = main.iloc[-1]
    h = htf.iloc[-1]
    points = 0
    ema20, ema50, ema200 = m.ema20, m.ema50, m.ema200
    he50, he200 = h.ema50, h.ema200
    if ema20 > ema50 > ema200: points += 25
    if h.close > he200 and he50 > he200: points += 20
    if m.adx >= 25: points += 15
    if 55 <= m.rsi <= 68: points += 15
    if m.macd_hist > 0: points += 10
    if m.volume_ratio >= 1.15: points += 10
    if m.close > m.breakout: points += 5
    return points


def enrich(df):
    df = df[df.confirm == "1"].copy()
    if len(df) < 210:
        return df
    df["ema20"] = ema(df.close, 20)
    df["ema50"] = ema(df.close, 50)
    df["ema200"] = ema(df.close, 200)
    df["atr"] = atr(df, 14)
    df["rsi"] = rsi(df.close, 14)
    df["adx"] = adx(df, 14)
    macd = ema(df.close, 12) - ema(df.close, 26)
    df["macd_hist"] = macd - ema(macd, 9)
    df["volume_ratio"] = df.volume / df.volume.rolling(20).mean().replace(0, np.nan)
    df["breakout"] = df.high.shift(1).rolling(20).max()
    return df.dropna()


def load():
    try:
        with open(PAPER_FILE, "r", encoding="utf-8") as h:
            x = json.load(h)
            return x if isinstance(x, list) else []
    except Exception:
        return []


def save(trades):
    with open(PAPER_FILE, "w", encoding="utf-8") as h:
        json.dump(trades, h, ensure_ascii=False, indent=2)


def close_trade(t, price, reason):
    entry = f(t.get("entry_price"))
    side = t.get("side", "LONG")
    move = (price-entry)/entry if side == "LONG" else (entry-price)/entry
    gross = STAKE * move
    t["exit_price"] = price
    t["exit_time"] = now()
    t["current_pnl_pct"] = move
    t["gross_pnl_tl"] = gross
    t["fees_tl"] = STAKE * FEE * 2
    t["slippage_tl"] = STAKE * SLIP
    t["net_pnl_tl"] = gross - t["fees_tl"] - t["slippage_tl"]
    t["status"] = "CLOSED"
    t["exit_reason"] = reason


def manage(trades, prices):
    for t in trades:
        if t.get("status") != "OPEN":
            continue
        p = prices.get(t.get("symbol"))
        if not p:
            continue
        entry = f(t.get("entry_price"))
        move = (p-entry)/entry
        peak = max(f(t.get("peak_pnl_pct")), move)
        t["peak_pnl_pct"] = peak
        t["peak_price"] = p if peak >= f(t.get("peak_pnl_pct")) else t.get("peak_price", entry)
        stop = f(t.get("initial_sl"))
        if p <= stop:
            close_trade(t, p, "INITIAL STOP LOSS")
            continue
        trail = None
        for peak_level, giveback in [(0.03,0.015),(0.05,0.013),(0.08,0.012),(0.12,0.010),(0.20,0.008),(0.30,0.007),(0.50,0.006)]:
            if peak >= peak_level:
                trail = peak - giveback
        if trail is not None:
            t["trailing_active"] = True
            t["trailing_stop_pct"] = trail
            if move <= trail:
                close_trade(t, p, "V2 DYNAMIC TRAILING STOP")
        t["current_pnl_pct"] = move


def can_enter(trades, symbol):
    if any(t.get("status") == "OPEN" and t.get("symbol") == symbol for t in trades):
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    for t in reversed(trades):
        if t.get("symbol") != symbol or t.get("status") != "CLOSED":
            continue
        try:
            if datetime.fromisoformat(t.get("exit_time", "").replace("Z", "+00:00")) > cutoff:
                return False
        except Exception:
            pass
        break
    return True


def main():
    trades = load()
    tk = tickers()
    ranked = sorted([s for s in symbols() if s in tk], key=lambda s: tk[s]["vol"], reverse=True)[:MAX_SCAN]
    prices = {s: tk[s]["last"] for s in ranked if tk[s]["last"] > 0}
    manage(trades, prices)
    open_count = sum(t.get("status") == "OPEN" for t in trades)
    candidates = []
    for s in ranked:
        if open_count >= MAX_OPEN or not can_enter(trades, s):
            continue
        try:
            m = enrich(candles(s, "15m", 250))
            h = enrich(candles(s, "1H", 250))
            if len(m) < 210 or len(h) < 210:
                continue
            x = m.iloc[-1]
            hh = h.iloc[-1]
            sc = score(m, h)
            if not (x.ema20 > x.ema50 > x.ema200 and hh.close > hh.ema200 and hh.ema50 > hh.ema200):
                continue
            if x.adx < 25 or not (55 <= x.rsi <= 68) or x.macd_hist <= 0 or x.volume_ratio < 1.15 or x.close <= x.breakout:
                continue
            if x.close > x.ema20 + 2.0*x.atr:
                continue
            if sc < 85:
                continue
            stop_pct = min(0.035, max(0.015, 1.8*x.atr/x.close))
            candidates.append((sc, s, x.close, stop_pct, x))
        except Exception as e:
            print(f"{s}: {e}")
    candidates.sort(reverse=True, key=lambda z: z[0])
    for sc, s, entry, stop_pct, x in candidates[:max(0, MAX_OPEN-open_count)]:
        stop = entry*(1-stop_pct)
        trades.append({"strategy_version": VERSION, "symbol": s, "side": "LONG", "status": "OPEN", "stake_tl": STAKE, "entry_time": now(), "entry_price": entry, "initial_sl": stop, "current_sl": stop, "market_regime": "V2_TREND_BREAKOUT", "htf_score": 20 if x.adx >= 25 else 0, "htf_confirmed": True, "peak_price": entry, "peak_pnl_pct": 0.0, "current_pnl_pct": 0.0, "trailing_active": False, "trailing_stop_price": None, "last_milestone": 0.0, "gross_pnl_tl": 0.0, "fees_tl": 0.0, "slippage_tl": 0.0, "net_pnl_tl": 0.0, "last_score": sc})
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    wins = [t for t in closed if f(t.get("net_pnl_tl")) > 0]
    losses = [t for t in closed if f(t.get("net_pnl_tl")) <= 0]
    net = sum(f(t.get("net_pnl_tl")) for t in closed)
    print("="*70)
    print(f"TREND RIDER V2 | scan={len(ranked)} candidates={len(candidates)}")
    print(f"open={sum(t.get('status')=='OPEN' for t in trades)} closed={len(closed)} wins={len(wins)} losses={len(losses)}")
    print(f"win_rate={(len(wins)/len(closed)*100 if closed else 0):.2f}% NET={net:.2f} TL")
    print("="*70)
    save(trades)


if __name__ == "__main__":
    main()
