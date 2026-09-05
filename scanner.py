import os
import json
import time
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# TREND RIDER CRYPTO SCANNER
# ============================================================
# OKX SPOT
# 15m ana trend
# 1h üst zaman dilimi onayı
# 0-100 ağırlıklı skor
# ATR + market structure stop
# Dinamik trailing stop
# Sabit TP YOK
# Her paper trade = 100 TL
# ============================================================


# ============================================================
# AYARLAR
# ============================================================

OKX_BASE_URL = "https://www.okx.com"

MAIN_BAR = "15m"
HTF_BAR = "1H"

MAIN_CANDLE_LIMIT = 250
HTF_CANDLE_LIMIT = 250

MAX_SYMBOLS_TO_SCAN = 80
MAX_FINAL_CANDIDATES = 20
MAX_OPEN_TRADES = 10

STAKE_TL = 100.0

# ------------------------------------------------------------
# PAPER TRADE MALİYETLERİ
# ------------------------------------------------------------

# Değiştirilebilir.
# OKX hesap seviyesine göre gerçek oran farklı olabilir.
TAKER_FEE_RATE = float(
    os.getenv(
        "OKX_TAKER_FEE_RATE",
        "0.001"
    )
)

SLIPPAGE_RATE = float(
    os.getenv(
        "PAPER_SLIPPAGE_RATE",
        "0.0005"
    )


# ============================================================
# GİRİŞ SKORU
# ============================================================

ENTRY_SCORE_MIN = 75

# Çok zayıf trendlerde işlem açma
MIN_ADX_FOR_TREND = 18

# Güçlü trend için ekstra avantaj
STRONG_TREND_SCORE = 82


# ============================================================
# RİSK / STOP AYARLARI
# ============================================================

ATR_LENGTH = 14

INITIAL_ATR_MULTIPLIER = 2.2

MIN_INITIAL_STOP_PCT = 0.012
MAX_INITIAL_STOP_PCT = 0.055


# ============================================================
# TRAILING STOP
# ============================================================

# İşlem kâra geçtikçe koruma artar.

TRAIL_START_PROFIT = 0.025       # +2.5%

TRAIL_LEVELS = [
    # peak profit, izin verilen geri çekilme
    (0.025, 0.020),   # +2.5  -> yaklaşık %2 geri verme
    (0.050, 0.018),   # +5   -> %1.8
    (0.075, 0.017),   # +7.5 -> %1.7
    (0.100, 0.016),   # +10  -> %1.6
    (0.150, 0.015),   # +15  -> %1.5
    (0.200, 0.014),   # +20  -> %1.4
    (0.250, 0.013),   # +25  -> %1.3
    (0.300, 0.012),   # +30  -> %1.2
    (0.400, 0.011),   # +40  -> %1.1
    (0.500, 0.010),   # +50  -> %1
]


# ============================================================
# KÂR KORUMA
# ============================================================

# +5% görülünce minimum yaklaşık +2% korumaya çalış.
# +10% görülünce minimum yaklaşık +5% korumaya çalış.
# +20% görülünce minimum yaklaşık +10% korumaya çalış.

PROFIT_LOCK_LEVELS = [
    (0.05, 0.015),
    (0.10, 0.040),
    (0.15, 0.070),
    (0.20, 0.100),
    (0.25, 0.140),
    (0.30, 0.180),
    (0.40, 0.250),
    (0.50, 0.330),
]


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# DOSYA
# ============================================================

PAPER_FILE = "paper_trades.json"


# ============================================================
# GENEL
# ============================================================

def now_utc():
    return datetime.now(
        timezone.utc
    ).isoformat()


def log(message):
    print(
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
        f"{message}"
    )


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(
        low,
        min(high, value)
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(
            "Telegram bilgileri bulunamadı."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.ok:
            return True

        log(
            f"Telegram hatası: "
            f"{response.status_code}"
        )

    except Exception as e:

        log(
            f"Telegram bağlantı hatası: {e}"
        )

    return False


# ============================================================
# OKX
# ============================================================

def okx_get(
    path,
    params=None
):

    response = requests.get(
        OKX_BASE_URL + path,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if data.get("code") != "0":

        raise RuntimeError(
            data.get(
                "msg",
                "OKX API hatası"
            )
        )

    return data.get(
        "data",
        []
    )


# ============================================================
# SYMBOL FİLTRESİ
# ============================================================

def is_crypto_symbol(
    symbol
):

    if not symbol.endswith("-USDT"):
        return False

    base = symbol[:-5].upper()

    excluded = {
        "USDC",
        "USDT",
        "USDE",
        "USDS",
        "USDG",
        "DAI",
        "FDUSD",
        "TUSD",
        "PYUSD"
    }

    if base in excluded:
        return False

    if base.startswith("X"):
        return False

    return True


# ============================================================
# SYMBOLS
# ============================================================

def get_symbols():

    data = okx_get(
        "/api/v5/public/instruments",
        {
            "instType": "SPOT"
        }
    )

    symbols = []

    for item in data:

        symbol = item.get(
            "instId",
            ""
        )

        if item.get("state") != "live":
            continue

        if not is_crypto_symbol(symbol):
            continue

        symbols.append(symbol)

    return symbols


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = okx_get(
        "/api/v5/market/tickers",
        {
            "instType": "SPOT"
        }
    )

    result = {}

    for item in data:

        symbol = item.get(
            "instId"
        )

        if not symbol:
            continue

        if not is_crypto_symbol(symbol):
            continue

        result[symbol] = {
            "last": safe_float(
                item.get("last")
            ),
            "volCcy24h": safe_float(
                item.get("volCcy24h")
            )
        }

    return result


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    bar,
    limit
):

    data = okx_get(
        "/api/v5/market/candles",
        {
            "instId": symbol,
            "bar": bar,
            "limit": limit
        }
    )

    rows = []

    for candle in data:

        if len(candle) < 9:
            continue

        rows.append({
            "ts": int(candle[0]),
            "open": safe_float(candle[1]),
            "high": safe_float(candle[2]),
            "low": safe_float(candle[3]),
            "close": safe_float(candle[4]),
            "volume": safe_float(candle[5]),
            "confirm": str(candle[8])
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = (
        df
        .sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    length=14
):

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100 -
        100 /
        (1 + rs)
    )

    return rsi.fillna(50)


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    length=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length
    ).mean()


# ============================================================
# EMA
# ============================================================

def ema(
    series,
    length
):

    return series.ewm(
        span=length,
        adjust=False
    ).mean()


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    close
):

    ema12 = ema(
        close,
        12
    )

    ema26 = ema(
        close,
        26
    )

    macd = (
        ema12 -
        ema26
    )

    signal = ema(
        macd,
        9
    )

    histogram = (
        macd -
        signal
    )

    return (
        macd,
        signal,
        histogram
    )


# ============================================================
# SUPERTREND
# ============================================================

def calculate_supertrend(
    df,
    atr_length=10,
    factor=3.0
):

    atr = calculate_atr(
        df,
        atr_length
    )

    hl2 = (
        df["high"] +
        df["low"]
    ) / 2

    upper = (
        hl2 +
        factor * atr
    )

    lower = (
        hl2 -
        factor * atr
    )

    final_upper = upper.copy()
    final_lower = lower.copy()

    direction = pd.Series(
        1,
        index=df.index,
        dtype=int
    )

    for i in range(1, len(df)):

        if (
            upper.iloc[i]
            < final_upper.iloc[i - 1]
            or
            df["close"].iloc[i - 1]
            > final_upper.iloc[i - 1]
        ):

            final_upper.iloc[i] = (
                upper.iloc[i]
            )

        else:

            final_upper.iloc[i] = (
                final_upper.iloc[i - 1]
            )

        if (
            lower.iloc[i]
            > final_lower.iloc[i - 1]
            or
            df["close"].iloc[i - 1]
            < final_lower.iloc[i - 1]
        ):

            final_lower.iloc[i] = (
                lower.iloc[i]
            )

        else:

            final_lower.iloc[i] = (
                final_lower.iloc[i - 1]
            )

        if direction.iloc[i - 1] == -1:

            if (
                df["close"].iloc[i]
                > final_upper.iloc[i]
            ):

                direction.iloc[i] = 1

            else:

                direction.iloc[i] = -1

        else:

            if (
                df["close"].iloc[i]
                < final_lower.iloc[i]
            ):

                direction.iloc[i] = -1

            else:

                direction.iloc[i] = 1

    return direction


# ============================================================
# ADX
# ============================================================

def calculate_adx(
    df,
    length=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = (
        high.diff()
    )

    down_move = (
        -low.diff()
    )

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move)
            &
            (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move)
            &
            (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    atr = tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        /
        atr
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        /
        atr
    )

    denominator = (
        plus_di +
        minus_di
    ).replace(
        0,
        np.nan
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        denominator
    )

    adx = dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    return (
        adx.fillna(0),
        plus_di.fillna(0),
        minus_di.fillna(0)
    )


# ============================================================
# VWAP
# ============================================================

def calculate_vwap(
    df
):

    typical_price = (
        df["high"] +
        df["low"] +
        df["close"]
    ) / 3

    cumulative_volume = (
        df["volume"].cumsum()
    )

    cumulative_value = (
        (
            typical_price *
            df["volume"]
        ).cumsum()
    )

    return (
        cumulative_value /
        cumulative_volume.replace(
            0,
            np.nan
        )
    )


# ============================================================
# OBV
# ============================================================

def calculate_obv(
    df
):

    direction = np.sign(
        df["close"].diff()
    ).fillna(0)

    return (
        direction *
        df["volume"]
    ).cumsum()


# ============================================================
# BOLLINGER
# ============================================================

def calculate_bollinger(
    close,
    length=20
):

    middle = (
        close.rolling(length)
        .mean()
    )

    std = (
        close.rolling(length)
        .std()
    )

    upper = (
        middle +
        2 * std
    )

    lower = (
        middle -
        2 * std
    )

    return (
        middle,
        upper,
        lower
    )


# ============================================================
# INDICATOR HESAPLAMA
# ============================================================

def calculate_indicators(
    df
):

    df = df.copy()

    df["ema20"] = ema(
        df["close"],
        20
    )

    df["ema50"] = ema(
        df["close"],
        50
    )

    df["ema100"] = ema(
        df["close"],
        100
    )

    df["ema200"] = ema(
        df["close"],
        200
    )

    df["rsi"] = calculate_rsi(
        df["close"],
        14
    )

    (
        df["macd"],
        df["macd_signal"],
        df["macd_hist"]
    ) = calculate_macd(
        df["close"]
    )

    df["atr"] = calculate_atr(
        df,
        ATR_LENGTH
    )

    df["supertrend"] = calculate_supertrend(
        df
    )

    (
        df["adx"],
        df["plus_di"],
        df["minus_di"]
    ) = calculate_adx(
        df
    )

    df["vwap"] = calculate_vwap(
        df
    )

    df["obv"] = calculate_obv(
        df
    )

    (
        df["bb_mid"],
        df["bb_upper"],
        df["bb_lower"]
    ) = calculate_bollinger(
        df["close"]
    )

    df["volume_ma20"] = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["roc10"] = (
        df["close"]
        .pct_change(10)
        * 100
    )

    return df


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    df
):

    if len(df) < 210:
        return None

    current = df.iloc[-2]
    previous = df.iloc[-3]

    score = 0
    components = {}

    close = safe_float(
        current["close"]
    )

    # --------------------------------------------------------
    # 1. EMA TREND — 20 POINT
    # --------------------------------------------------------

    ema_score = 0

    if close > current["ema200"]:
        ema_score += 6

    if current["ema20"] > current["ema50"]:
        ema_score += 5

    if current["ema50"] > current["ema100"]:
        ema_score += 4

    if current["ema100"] > current["ema200"]:
        ema_score += 3

    if current["ema20"] > previous["ema20"]:
        ema_score += 2

    ema_score = min(
        ema_score,
        20
    )

    score += ema_score
    components["EMA"] = ema_score

    # --------------------------------------------------------
    # 2. SUPERTREND — 10
    # --------------------------------------------------------

    supertrend_score = 10 if (
        current["supertrend"] == 1
    ) else 0

    score += supertrend_score

    components["Supertrend"] = (
        supertrend_score
    )

    # --------------------------------------------------------
    # 3. RSI — 10
    # --------------------------------------------------------

    rsi = safe_float(
        current["rsi"]
    )

    if 55 <= rsi < 65:
        rsi_score = 10

    elif 65 <= rsi < 72:
        rsi_score = 9

    elif 50 <= rsi < 55:
        rsi_score = 6

    elif 72 <= rsi < 78:
        rsi_score = 6

    elif rsi >= 78:
        rsi_score = 2

    else:
        rsi_score = 0

    score += rsi_score
    components["RSI"] = rsi_score

    # --------------------------------------------------------
    # 4. MACD — 10
    # --------------------------------------------------------

    macd_score = 0

    if current["macd"] > current["macd_signal"]:
        macd_score += 5

    if current["macd_hist"] > 0:
        macd_score += 3

    if current["macd_hist"] > previous["macd_hist"]:
        macd_score += 2

    score += macd_score
    components["MACD"] = macd_score

    # --------------------------------------------------------
    # 5. ADX — 10
    # --------------------------------------------------------

    adx = safe_float(
        current["adx"]
    )

    adx_score = 0

    if adx >= 30:
        adx_score = 10

    elif adx >= 25:
        adx_score = 8

    elif adx >= 20:
        adx_score = 6

    elif adx >= 18:
        adx_score = 4

    score += adx_score
    components["ADX"] = adx_score

    # --------------------------------------------------------
    # 6. VOLUME — 10
    # --------------------------------------------------------

    volume_ma = safe_float(
        current["volume_ma20"]
    )

    volume_ratio = (
        current["volume"] /
        volume_ma
        if volume_ma > 0
        else 0
    )

    if volume_ratio >= 2.0:
        volume_score = 10

    elif volume_ratio >= 1.5:
        volume_score = 8

    elif volume_ratio >= 1.2:
        volume_score = 6

    elif volume_ratio >= 1.0:
        volume_score = 4

    else:
        volume_score = 1

    score += volume_score
    components["Volume"] = volume_score

    # --------------------------------------------------------
    # 7. VWAP — 10
    # --------------------------------------------------------

    vwap_score = 10 if (
        close > current["vwap"]
    ) else 0

    score += vwap_score
    components["VWAP"] = vwap_score

    # --------------------------------------------------------
    # 8. OBV — 5
    # --------------------------------------------------------

    obv_score = 0

    if current["obv"] > previous["obv"]:
        obv_score += 5

    score += obv_score
    components["OBV"] = obv_score

    # --------------------------------------------------------
    # 9. MOMENTUM — 5
    # --------------------------------------------------------

    roc = safe_float(
        current["roc10"]
    )

    if roc > 5:
        momentum_score = 5

    elif roc > 2:
        momentum_score = 4

    elif roc > 0:
        momentum_score = 2

    else:
        momentum_score = 0

    score += momentum_score
    components["Momentum"] = momentum_score

    # --------------------------------------------------------
    # 10. BOLLINGER / VOLATILITY — 5
    # --------------------------------------------------------

    bb_score = 0

    if (
        close > current["bb_mid"]
        and
        close < current["bb_upper"]
    ):
        bb_score = 5

    elif close > current["bb_upper"]:
        bb_score = 2

    score += bb_score
    components["Volatility"] = bb_score

    return {
        "score": int(
            clamp(score, 0, 100)
        ),
        "components": components,
        "price": close,
        "rsi": rsi,
        "adx": adx,
        "volume_ratio": volume_ratio,
        "atr": safe_float(
            current["atr"]
        ),
        "ema20": safe_float(
            current["ema20"]
        ),
        "ema50": safe_float(
            current["ema50"]
        ),
        "ema100": safe_float(
            current["ema100"]
        ),
        "ema200": safe_float(
            current["ema200"]
        ),
        "supertrend": int(
            current["supertrend"]
        ),
        "macd_hist": safe_float(
            current["macd_hist"]
        ),
        "roc10": roc,
        "candle_ts": int(
            current["ts"]
        )
    }


# ============================================================
# HTF SCORE
# ============================================================

def calculate_htf_confirmation(
    df
):

    if len(df) < 210:
        return {
            "confirmed": False,
            "score": 0
        }

    df = calculate_indicators(
        df
    )

    current = df.iloc[-2]

    close = safe_float(
        current["close"]
    )

    htf_score = 0

    if close > current["ema200"]:
        htf_score += 5

    if current["ema50"] > current["ema200"]:
        htf_score += 4

    if current["supertrend"] == 1:
        htf_score += 3

    if current["rsi"] > 50:
        htf_score += 2

    if current["adx"] >= 18:
        htf_score += 1

    return {
        "confirmed": htf_score >= 10,
        "score": htf_score,
        "price": close,
        "rsi": safe_float(
            current["rsi"]
        ),
        "adx": safe_float(
            current["adx"]
        )
    }


# ============================================================
# MARKET REGIME
# ============================================================

def market_regime(
    result
):

    adx = result["adx"]

    if adx >= 25:
        return "STRONG_TREND"

    if adx >= 18:
        return "TREND"

    return "RANGE"


# ============================================================
# INITIAL STOP
# ============================================================

def calculate_initial_stop(
    df,
    entry_price,
    atr
):

    recent_low = safe_float(
        df["low"]
        .iloc[-8:-1]
        .min()
    )

    atr_stop = (
        entry_price -
        atr *
        INITIAL_ATR_MULTIPLIER
    )

    structure_stop = (
        recent_low * 0.995
        if recent_low > 0
        else atr_stop
    )

    stop = max(
        atr_stop,
        structure_stop
    )

    stop_pct = (
        entry_price - stop
    ) / entry_price

    if stop_pct < MIN_INITIAL_STOP_PCT:

        stop = (
            entry_price *
            (1 - MIN_INITIAL_STOP_PCT)
        )

    elif stop_pct > MAX_INITIAL_STOP_PCT:

        stop = (
            entry_price *
            (1 - MAX_INITIAL_STOP_PCT)
        )

    return stop


# ============================================================
# PAPER TRADE DOSYASI
# ============================================================

def load_paper_trades():

    if not os.path.exists(
        PAPER_FILE
    ):
        return []

    try:

        with open(
            PAPER_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, list):
            return data

    except Exception as e:

        log(
            f"Paper dosyası okunamadı: {e}"
        )

    return []


def save_paper_trades(
    trades
):

    with open(
        PAPER_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            trades,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# PNL
# ============================================================

def gross_pnl_pct(
    entry,
    current
):

    if entry <= 0:
        return 0

    return (
        (
            current -
            entry
        )
        /
        entry
    )


def estimate_net_pnl_tl(
    entry,
    exit_price,
    stake_tl
):

    if entry <= 0:
        return 0

    price_change = (
        exit_price -
        entry
    ) / entry

    gross = (
        stake_tl *
        price_change
    )

    fees = (
        stake_tl *
        (
            TAKER_FEE_RATE * 2
        )
    )

    slippage = (
        stake_tl *
        (
            SLIPPAGE_RATE * 2
        )
    )

    return (
        gross -
        fees -
        slippage
    )


# ============================================================
# MİLSTONE
# ============================================================

def get_profit_milestone(
    pnl
):

    levels = [
        0.05,
        0.10,
        0.15,
        0.20,
        0.25,
        0.30,
        0.40,
        0.50
    ]

    milestone = 0

    for level in levels:

        if pnl >= level:
            milestone = level

    return milestone


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    trades,
    symbol,
    result,
    htf
):

    open_trades = [
        t
        for t in trades
        if t.get("status") == "OPEN"
    ]

    if len(open_trades) >= MAX_OPEN_TRADES:
        return False

    if any(
        t.get("symbol") == symbol
        for t in open_trades
    ):
        return False

    entry = result["price"]

    stop = calculate_initial_stop(
        CURRENT_DF,
        entry,
        result["atr"]
    )

    trade = {
        "strategy_version":
            "TREND_RIDER_100",

        "symbol": symbol,
        "side": "LONG",
        "status": "OPEN",

        "stake_tl": STAKE_TL,

        "entry_time": now_utc(),
        "entry_price": entry,

        "initial_sl": stop,
        "current_sl": stop,

        "atr_at_entry":
            result["atr"],

        "score": result["score"],
        "last_score": result["score"],

        "score_components":
            result["components"],

        "market_regime":
            market_regime(result),

        "htf_score":
            htf.get("score", 0),

        "htf_confirmed":
            htf.get("confirmed", False),

        "peak_price": entry,
        "peak_pnl_pct": 0.0,

        "current_pnl_pct": 0.0,

        "trailing_active": False,
        "trailing_stop_price": None,

        "last_milestone": 0,

        "gross_pnl_tl": 0.0,
        "fees_tl": 0.0,
        "slippage_tl": 0.0,
        "net_pnl_tl": 0.0,

        "exit_time": None,
        "exit_price": None,
        "exit_reason": None
    }

    trades.append(
        trade
    )

    send_telegram(
        "🚀 YENİ PAPER TRADE 🚀\n\n"
        f"🪙 {symbol}\n"
        f"📈 LONG\n"
        f"💰 Sermaye: {STAKE_TL:.2f} TL\n\n"
        f"💵 Giriş: {entry:.10g}\n"
        f"🛑 İlk SL: {stop:.10g}\n\n"
        f"🧠 Skor: {result['score']}/100\n"
        f"📊 HTF: "
        f"{htf.get('score', 0)}/15\n"
        f"🔥 ADX: {result['adx']:.1f}\n"
        f"📈 RSI: {result['rsi']:.1f}\n\n"
        f"🎯 SABİT TP YOK\n"
        f"📈 Trend devam ettiği sürece taşınacak\n"
        f"🛡️ Kâr dinamik trailing ile korunacak"
    )

    log(
        f"OPEN {symbol} | "
        f"score={result['score']} | "
        f"entry={entry}"
    )

    return True


# ============================================================
# TRAILING STOP HESAPLAMA
# ============================================================

def calculate_trailing_stop(
    trade,
    current_price
):

    entry = safe_float(
        trade.get("entry_price")
    )

    peak = safe_float(
        trade.get("peak_price"),
        entry
    )

    peak_pnl = gross_pnl_pct(
        entry,
        peak
    )

    if peak_pnl < TRAIL_START_PROFIT:
        return None

    drawdown = 0

    for level, allowed in TRAIL_LEVELS:

        if peak_pnl >= level:
            drawdown = allowed

    if drawdown <= 0:
        return None

    trail_price = (
        peak *
        (1 - drawdown)
    )

    # Kâr kilitleme
    lock_profit = 0

    for level, locked in PROFIT_LOCK_LEVELS:

        if peak_pnl >= level:
            lock_profit = locked

    if lock_profit > 0:

        lock_price = (
            entry *
            (1 + lock_profit)
        )

        trail_price = max(
            trail_price,
            lock_price
        )

    return trail_price


# ============================================================
# TRADE UPDATE
# ============================================================

def update_open_trades(
    trades,
    prices
):

    changed = False

    for trade in trades:

        if trade.get("status") != "OPEN":
            continue

        symbol = trade.get(
            "symbol"
        )

        if symbol not in prices:
            continue

        current = prices[symbol]

        entry = safe_float(
            trade.get("entry_price")
        )

        if entry <= 0:
            continue

        current_pnl = gross_pnl_pct(
            entry,
            current
        )

        trade["current_pnl_pct"] = (
            current_pnl
        )

        # ----------------------------------------------------
        # PEAK
        # ----------------------------------------------------

        peak = safe_float(
            trade.get(
                "peak_price"
            ),
            entry
        )

        if current > peak:

            trade["peak_price"] = (
                current
            )

            peak = current

            trade["peak_pnl_pct"] = (
                gross_pnl_pct(
                    entry,
                    peak
                )
            )

            changed = True

        # ----------------------------------------------------
        # DİNAMİK TRAILING
        # ----------------------------------------------------

        trailing = calculate_trailing_stop(
            trade,
            current
        )

        if trailing:

            old_trailing = safe_float(
                trade.get(
                    "trailing_stop_price"
                ),
                0
            )

            # Stop sadece yukarı hareket eder.
            if trailing > old_trailing:

                trade[
                    "trailing_stop_price"
                ] = trailing

                trade[
                    "current_sl"
                ] = max(
                    safe_float(
                        trade.get(
                            "current_sl"
                        )
                    ),
                    trailing
                )

                trade[
                    "trailing_active"
                ] = True

                changed = True

        # ----------------------------------------------------
        # MİLESTONE TELEGRAM
        # ----------------------------------------------------

        milestone = get_profit_milestone(
            current_pnl
        )

        last_milestone = safe_float(
            trade.get(
                "last_milestone"
            )
        )

        if (
            milestone > last_milestone
            and milestone > 0
        ):

            trade[
                "last_milestone"
            ] = milestone

            peak_pct = (
                safe_float(
                    trade.get(
                        "peak_pnl_pct"
                    )
                ) * 100
            )

            send_telegram(
                "🔥 KÂR MİLESTONE 🔥\n\n"
                f"🪙 {symbol}\n"
                f"💰 Güncel: "
                f"{current_pnl * 100:+.2f}%\n"
                f"🚀 Zirve: "
                f"{peak_pct:+.2f}%\n"
                f"💵 100 TL karşılığı: "
                f"{STAKE_TL * current_pnl:+.2f} TL\n\n"
                "🛡️ İşlem trend devam ettiği için "
                "açık tutuluyor."
            )

            changed = True

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        current_sl = safe_float(
            trade.get(
                "current_sl"
            )
        )

        if current <= current_sl:

            exit_reason = (
                "DYNAMIC TRAILING STOP"
                if trade.get(
                    "trailing_active"
                )
                else
                "INITIAL STOP LOSS"
            )

            close_trade(
                trade,
                current,
                exit_reason
            )

            send_exit_message(
                trade
            )

            changed = True

    return changed


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    reason
):

    entry = safe_float(
        trade.get("entry_price")
    )

    stake = safe_float(
        trade.get(
            "stake_tl"
        ),
        STAKE_TL
    )

    gross_pct = gross_pnl_pct(
        entry,
        exit_price
    )

    gross_tl = (
        stake *
        gross_pct
    )

    fees = (
        stake *
        TAKER_FEE_RATE *
        2
    )

    slippage = (
        stake *
        SLIPPAGE_RATE *
        2
    )

    net = (
        gross_tl -
        fees -
        slippage
    )

    trade["gross_pnl_tl"] = (
        gross_tl
    )

    trade["fees_tl"] = (
        fees
    )

    trade["slippage_tl"] = (
        slippage
    )

    trade["net_pnl_tl"] = (
        net
    )

    trade["exit_price"] = (
        exit_price
    )

    trade["exit_time"] = (
        now_utc()
    )

    trade["exit_reason"] = (
        reason
    )

    trade["status"] = (
        "CLOSED"
    )

    trade["current_pnl_pct"] = (
        gross_pct
    )


# ============================================================
# EXIT MESSAGE
# ============================================================

def send_exit_message(
    trade
):

    symbol = trade.get(
        "symbol"
    )

    net = safe_float(
        trade.get(
            "net_pnl_tl"
        )
    )

    gross = safe_float(
        trade.get(
            "gross_pnl_tl"
        )
    )

    peak = safe_float(
        trade.get(
            "peak_pnl_pct"
        )
    ) * 100

    current = safe_float(
        trade.get(
            "current_pnl_pct"
        )
    ) * 100

    if net >= 0:
        emoji = "🟢"
        result_text = "KÂR"
    else:
        emoji = "🔴"
        result_text = "ZARAR"

    send_telegram(
        f"{emoji} PAPER TRADE KAPANDI — "
        f"{result_text} {emoji}\n\n"
        f"🪙 {symbol}\n"
        f"💰 Giriş: "
        f"{trade.get('entry_price'):.10g}\n"
        f"💰 Çıkış: "
        f"{trade.get('exit_price'):.10g}\n\n"
        f"📈 Anlık PnL: "
        f"{current:+.2f}%\n"
        f"🚀 Zirve PnL: "
        f"{peak:+.2f}%\n\n"
        f"💵 Brüt: "
        f"{gross:+.2f} TL\n"
        f"💳 Komisyon: "
        f"-{trade.get('fees_tl', 0):.2f} TL\n"
        f"📉 Slippage: "
        f"-{trade.get('slippage_tl', 0):.2f} TL\n"
        f"💰 NET: "
        f"{net:+.2f} TL\n\n"
        f"🛑 Sebep: "
        f"{trade.get('exit_reason')}"
    )


# ============================================================
# SCORE UPDATE
# ============================================================

def update_trade_score(
    trades,
    symbol,
    result
):

    changed = False

    for trade in trades:

        if trade.get("status") != "OPEN":
            continue

        if trade.get("symbol") != symbol:
            continue

        trade["last_score"] = (
            result["score"]
        )

        trade["last_score_components"] = (
            result["components"]
        )

        trade["last_score_time"] = (
            now_utc()
        )

        changed = True

    return changed


# ============================================================
# CANDIDATE
# ============================================================

def rank_candidate(
    result,
    htf
):

    final_score = (
        result["score"]
    )

    if htf["confirmed"]:
        final_score += 5

    else:
        final_score -= 5

    # Güçlü trend bonusu
    if result["adx"] >= 25:
        final_score += 3

    # Aşırı RSI cezası
    if result["rsi"] >= 80:
        final_score -= 8

    final_score = int(
        clamp(
            final_score,
            0,
            100
        )
    )

    return final_score


# ============================================================
# MAIN
# ============================================================

CURRENT_DF = None


def main():

    global CURRENT_DF

    log("=" * 70)
    log("🚀 TREND RIDER CRYPTO SCANNER")
    log("=" * 70)

    trades = load_paper_trades()

    log(
        f"Paper kayıtları: "
        f"{len(trades)}"
    )

    # --------------------------------------------------------
    # TICKERS
    # --------------------------------------------------------

    try:

        tickers = get_tickers()

    except Exception as e:

        log(
            f"Ticker hatası: {e}"
        )

        return

    prices = {
        symbol: data["last"]
        for symbol, data
        in tickers.items()
        if data["last"] > 0
    }

    # --------------------------------------------------------
    # AÇIK İŞLEMLERİ ÖNCE YÖNET
    # --------------------------------------------------------

    if update_open_trades(
        trades,
        prices
    ):

        save_paper_trades(
            trades
        )

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    try:

        symbols = get_symbols()

    except Exception as e:

        log(
            f"Symbol hatası: {e}"
        )

        return

    symbols = [
        s
        for s in symbols
        if s in tickers
    ]

    symbols.sort(
        key=lambda s:
        tickers[s]["volCcy24h"],
        reverse=True
    )

    symbols = symbols[
        :MAX_SYMBOLS_TO_SCAN
    ]

    log(
        f"Taranıyor: "
        f"{len(symbols)} coin"
    )

    # --------------------------------------------------------
    # 15M TARAMA
    # --------------------------------------------------------

    candidates = []

    errors = 0

    for index, symbol in enumerate(
        symbols,
        1
    ):

        try:

            log(
                f"[{index}/{len(symbols)}] "
                f"{symbol}"
            )

            df = get_candles(
                symbol,
                MAIN_BAR,
                MAIN_CANDLE_LIMIT
            )

            if df.empty:
                continue

            closed = df[
                df["confirm"] == "1"
            ].copy()

            if len(closed) < 210:
                continue

            df = calculate_indicators(
                closed
            )

            result = calculate_score(
                df
            )

            if result is None:
                continue

            update_trade_score(
                trades,
                symbol,
                result
            )

            # Ön filtre
            if result["score"] < 65:
                continue

            if result["adx"] < MIN_ADX_FOR_TREND:
                continue

            CURRENT_DF = df

            candidates.append({
                "symbol": symbol,
                "result": result,
                "df": df
            })

            time.sleep(
                0.10
            )

        except Exception as e:

            errors += 1

            log(
                f"{symbol} hata: {e}"
            )

    # --------------------------------------------------------
    # EN İYİLERİ SEÇ
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x:
        x["result"]["score"],
        reverse=True
    )

    candidates = candidates[
        :MAX_FINAL_CANDIDATES
    ]

    log(
        f"HTF kontrolü yapılacak aday: "
        f"{len(candidates)}"
    )

    # --------------------------------------------------------
    # 1H ONAY
    # --------------------------------------------------------

    final_candidates = []

    for candidate in candidates:

        symbol = candidate[
            "symbol"
        ]

        result = candidate[
            "result"
        ]

        try:

            htf_df = get_candles(
                symbol,
                HTF_BAR,
                HTF_CANDLE_LIMIT
            )

            if htf_df.empty:
                continue

            htf_closed = htf_df[
                htf_df["confirm"] == "1"
            ].copy()

            htf = calculate_htf_confirmation(
                htf_closed
            )

            final_score = rank_candidate(
                result,
                htf
            )

            candidate[
                "htf"
            ] = htf

            candidate[
                "final_score"
            ] = final_score

            final_candidates.append(
                candidate
            )

            log(
                f"🎯 {symbol} "
                f"15m={result['score']}/100 "
                f"HTF={htf['score']}/15 "
                f"FINAL={final_score}/100"
            )

            time.sleep(
                0.15
            )

        except Exception as e:

            log(
                f"HTF {symbol} hata: {e}"
            )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    final_candidates.sort(
        key=lambda x:
        x["final_score"],
        reverse=True
    )

    buy_count = 0

    for candidate in final_candidates:

        symbol = candidate[
            "symbol"
        ]

        result = candidate[
            "result"
        ]

        htf = candidate[
            "htf"
        ]

        final_score = candidate[
            "final_score"
        ]

        if final_score < ENTRY_SCORE_MIN:
            continue

        if result["rsi"] >= 80:
            continue

        already_open = any(
            t.get("status") == "OPEN"
            and
            t.get("symbol") == symbol
            for t in trades
        )

        if already_open:
            continue

        open_count = sum(
            1
            for t in trades
            if t.get("status") == "OPEN"
        )

        if open_count >= MAX_OPEN_TRADES:
            break

        CURRENT_DF = candidate[
            "df"
        ]

        result["score"] = (
            final_score
        )

        if open_trade(
            trades,
            symbol,
            result,
            htf
        ):

            buy_count += 1

            save_paper_trades(
                trades
            )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_paper_trades(
        trades
    )

    # --------------------------------------------------------
    # İSTATİSTİK
    # --------------------------------------------------------

    open_count = sum(
        1
        for t in trades
        if t.get("status") == "OPEN"
    )

    closed = [
        t
        for t in trades
        if t.get("status") == "CLOSED"
    ]

    total_net = sum(
        safe_float(
            t.get("net_pnl_tl")
        )
        for t in closed
    )

    wins = [
        t
        for t in closed
        if safe_float(
            t.get("net_pnl_tl")
        ) > 0
    ]

    losses = [
        t
        for t in closed
        if safe_float(
            t.get("net_pnl_tl")
        ) < 0
    ]

    win_rate = (
        len(wins) /
        len(closed) *
        100
        if closed
        else 0
    )

    log("=" * 70)
    log("✅ TARAMA TAMAMLANDI")
    log("=" * 70)
    log(
        f"Yeni işlem: {buy_count}"
    )
    log(
        f"Açık işlem: {open_count}"
    )
    log(
        f"Kapanmış işlem: "
        f"{len(closed)}"
    )
    log(
        f"Kazanılan: {len(wins)}"
    )
    log(
        f"Kaybedilen: {len(losses)}"
    )
    log(
        f"Win rate: "
        f"{win_rate:.2f}%"
    )
    log(
        f"NET Paper PnL: "
        f"{total_net:+.2f} TL"
    )
    log(
        f"Hatalar: {errors}"
    )
    log("=" * 70)


if __name__ == "__main__":
    main()
