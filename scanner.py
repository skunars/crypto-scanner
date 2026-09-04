import os
import json
import time
from datetime import datetime, timezone

import requests
import pandas as pd


# ============================================================
# AYARLAR
# ============================================================

OKX_BASE_URL = "https://www.okx.com"

BAR = "15m"
CANDLE_LIMIT = 220

RSI_LENGTH = 14
EMA_LENGTH = 200

SUPERTREND_ATR_LENGTH = 10
SUPERTREND_FACTOR = 3.0

UT_ATR_LENGTH = 10
UT_SENSITIVITY = 1.0

MAX_SYMBOLS_TO_SCAN = 80

SL_PERCENT = 0.02
TP1_PERCENT = 0.03
TP2_PERCENT = 0.06

PAPER_FILE = "paper_trades.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# GENEL YARDIMCI FONKSİYONLAR
# ============================================================

def now_utc():
    return datetime.now(timezone.utc).isoformat()


def log(message):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] {message}")


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID bulunamadı.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

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
            log("Telegram mesajı gönderildi.")
            return True

        log(f"Telegram hatası: {response.status_code} {response.text}")
        return False

    except Exception as e:
        log(f"Telegram bağlantı hatası: {e}")
        return False


# ============================================================
# OKX API
# ============================================================

def okx_get(path, params=None):
    url = OKX_BASE_URL + path

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if data.get("code") != "0":
        raise RuntimeError(
            f"OKX API hatası: {data.get('msg', 'Bilinmeyen hata')}"
        )

    return data.get("data", [])


def get_symbols():
    data = okx_get(
        "/api/v5/public/instruments",
        {
            "instType": "SPOT"
        }
    )

    symbols = []

    for item in data:
        inst_id = item.get("instId", "")

        if not inst_id.endswith("-USDT"):
            continue

        if item.get("state") != "live":
            continue

        symbols.append(inst_id)

    return symbols


def get_tickers():
    data = okx_get(
        "/api/v5/market/tickers",
        {
            "instType": "SPOT"
        }
    )

    result = {}

    for item in data:
        symbol = item.get("instId")

        if symbol and symbol.endswith("-USDT"):
            result[symbol] = {
                "last": safe_float(item.get("last")),
                "volCcy24h": safe_float(item.get("volCcy24h"))
            }

    return result


def get_candles(symbol):
    data = okx_get(
        "/api/v5/market/candles",
        {
            "instId": symbol,
            "bar": BAR,
            "limit": CANDLE_LIMIT
        }
    )

    if not data:
        return pd.DataFrame()

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

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.sort_values("ts").reset_index(drop=True)

    return df


# ============================================================
# RSI
# ============================================================

def calculate_rsi(close, length=14):
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

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

    rs = avg_gain / avg_loss.replace(0, float("nan"))

    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, length=10):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length
    ).mean()

    return atr


# ============================================================
# SUPERTREND
# ============================================================

def calculate_supertrend(
    df,
    atr_length=10,
    factor=3.0
):
    atr = calculate_atr(df, atr_length)

    hl2 = (df["high"] + df["low"]) / 2

    upper_band = hl2 + factor * atr
    lower_band = hl2 - factor * atr

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    direction = pd.Series(
        index=df.index,
        dtype="int64"
    )

    direction.iloc[0] = 1

    for i in range(1, len(df)):

        if (
            upper_band.iloc[i] < final_upper.iloc[i - 1]
            or df["close"].iloc[i - 1] > final_upper.iloc[i - 1]
        ):
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        if (
            lower_band.iloc[i] > final_lower.iloc[i - 1]
            or df["close"].iloc[i - 1] < final_lower.iloc[i - 1]
        ):
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        if direction.iloc[i - 1] == -1:
            if df["close"].iloc[i] > final_upper.iloc[i]:
                direction.iloc[i] = 1
            else:
                direction.iloc[i] = -1
        else:
            if df["close"].iloc[i] < final_lower.iloc[i]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = 1

    return direction


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(
    df,
    atr_length=10,
    sensitivity=1.0
):
    close = df["close"]

    atr = calculate_atr(
        df,
        atr_length
    )

    loss = sensitivity * atr

    trailing_stop = pd.Series(
        index=df.index,
        dtype="float64"
    )

    trailing_stop.iloc[0] = close.iloc[0] - loss.iloc[0]

    for i in range(1, len(df)):

        previous_stop = trailing_stop.iloc[i - 1]
        previous_close = close.iloc[i - 1]
        current_close = close.iloc[i]
        current_loss = loss.iloc[i]

        if pd.isna(current_loss):
            trailing_stop.iloc[i] = previous_stop
            continue

        if (
            current_close > previous_stop
            and previous_close > previous_stop
        ):
            trailing_stop.iloc[i] = max(
                previous_stop,
                current_close - current_loss
            )

        elif (
            current_close < previous_stop
            and previous_close < previous_stop
        ):
            trailing_stop.iloc[i] = min(
                previous_stop,
                current_close + current_loss
            )

        elif current_close > previous_stop:
            trailing_stop.iloc[i] = (
                current_close - current_loss
            )

        else:
            trailing_stop.iloc[i] = (
                current_close + current_loss
            )

    bullish = close > trailing_stop

    return bullish


# ============================================================
# SİNYAL HESAPLAMA
# ============================================================

def calculate_signal(df):

    if len(df) < EMA_LENGTH + 10:
        return None

    df = df.copy()

    df["ema200"] = (
        df["close"]
        .ewm(
            span=EMA_LENGTH,
            adjust=False
        )
        .mean()
    )

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_LENGTH
    )

    df["supertrend"] = calculate_supertrend(
        df,
        SUPERTREND_ATR_LENGTH,
        SUPERTREND_FACTOR
    )

    df["ut_bull"] = calculate_ut_bot(
        df,
        UT_ATR_LENGTH,
        UT_SENSITIVITY
    )

    # Sadece kapanmış mumları kullan.
    closed = df[
        df["confirm"] == "1"
    ].copy()

    if len(closed) < 3:
        return None

    current = closed.iloc[-1]
    previous = closed.iloc[-2]

    current_conditions = [
        current["rsi"] > 50,
        current["close"] > current["ema200"],
        current["supertrend"] == 1,
        bool(current["ut_bull"])
    ]

    previous_conditions = [
        previous["rsi"] > 50,
        previous["close"] > previous["ema200"],
        previous["supertrend"] == 1,
        bool(previous["ut_bull"])
    ]

    current_score = sum(current_conditions)
    previous_score = sum(previous_conditions)

    signal = None

    # Dört şartın tamamı ilk kez oluştu.
    if current_score == 4 and previous_score < 4:
        signal = "BUY"

    # Daha önce dört şart vardı, artık bozuldu.
    elif current_score < 4 and previous_score == 4:
        signal = "SELL"

    return {
        "signal": signal,
        "score": current_score,
        "previous_score": previous_score,
        "price": float(current["close"]),
        "rsi": float(current["rsi"]),
        "ema200": float(current["ema200"]),
        "supertrend": int(current["supertrend"]),
        "ut_bull": bool(current["ut_bull"])
    }


# ============================================================
# PAPER TRADE DOSYASI
# ============================================================

def load_paper_trades():

    if not os.path.exists(PAPER_FILE):
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

        return []

    except Exception as e:
        log(f"Paper trade dosyası okunamadı: {e}")
        return []


def save_paper_trades(trades):

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
# PAPER TRADE AÇ
# ============================================================

def open_paper_trade(
    trades,
    symbol,
    entry_price
):

    open_trades = [
        t for t in trades
        if t.get("status") == "OPEN"
    ]

    if len(open_trades) >= 10:
        log("Maksimum 10 açık paper trade sınırına ulaşıldı.")
        return False

    for trade in open_trades:
        if trade.get("symbol") == symbol:
            return False

    sl = entry_price * (1 - SL_PERCENT)
    tp1 = entry_price * (1 + TP1_PERCENT)
    tp2 = entry_price * (1 + TP2_PERCENT)

    trade = {
        "symbol": symbol,
        "side": "LONG",
        "status": "OPEN",

        "entry_time": now_utc(),

        "entry_price": entry_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,

        "tp1_hit": False,

        "remaining_pct": 100,

        "realized_pnl_pct": 0,

        "exit_time": None,
        "exit_price": None,
        "exit_reason": None
    }

    trades.append(trade)

    log(
        f"PAPER BUY: {symbol} "
        f"entry={entry_price}"
    )

    return True


# ============================================================
# PAPER TRADE KAPAT
# ============================================================

def close_paper_trade(
    trade,
    exit_price,
    reason
):

    entry_price = safe_float(
        trade.get("entry_price")
    )

    if entry_price <= 0:
        return 0

    pnl_pct = (
        (exit_price - entry_price)
        / entry_price
    ) * 100

    remaining_pct = safe_float(
        trade.get("remaining_pct"),
        100
    )

    trade["realized_pnl_pct"] = (
        safe_float(trade.get("realized_pnl_pct"))
        + pnl_pct * (remaining_pct / 100)
    )

    trade["status"] = "CLOSED"
    trade["remaining_pct"] = 0

    trade["exit_time"] = now_utc()
    trade["exit_price"] = exit_price
    trade["exit_reason"] = reason

    return trade["realized_pnl_pct"]


# ============================================================
# PAPER TRADELERİ GÜNCELLE
# ============================================================

def update_paper_trades(
    trades,
    prices
):

    changed = False

    for trade in trades:

        if trade.get("status") != "OPEN":
            continue

        symbol = trade.get("symbol")

        if symbol not in prices:
            continue

        current_price = prices[symbol]

        entry = safe_float(
            trade.get("entry_price")
        )

        sl = safe_float(
            trade.get("sl")
        )

        tp1 = safe_float(
            trade.get("tp1")
        )

        tp2 = safe_float(
            trade.get("tp2")
        )

        # ====================================================
        # STOP LOSS
        # ====================================================

        if current_price <= sl:

            pnl = close_paper_trade(
                trade,
                current_price,
                "STOP LOSS"
            )

            log(
                f"STOP LOSS: {symbol} "
                f"PnL={pnl:.2f}%"
            )

            send_telegram(
                f"🛑 PAPER TRADE STOP LOSS\n\n"
                f"🪙 {symbol}\n"
                f"💰 Çıkış: {current_price:.10g}\n"
                f"📉 PnL: {pnl:.2f}%"
            )

            changed = True
            continue

        # ====================================================
        # TP1
        # ====================================================

        if (
            not trade.get("tp1_hit", False)
            and current_price >= tp1
        ):

            half_pnl = (
                (current_price - entry)
                / entry
            ) * 100 * 0.5

            trade["realized_pnl_pct"] = (
                safe_float(
                    trade.get("realized_pnl_pct")
                )
                + half_pnl
            )

            trade["tp1_hit"] = True
            trade["remaining_pct"] = 50

            # TP1 sonrası SL giriş fiyatına taşınır.
            trade["sl"] = entry

            log(
                f"TP1: {symbol} "
                f"partial PnL={half_pnl:.2f}%"
            )

            send_telegram(
                f"🎯 PAPER TRADE TP1\n\n"
                f"🪙 {symbol}\n"
                f"💰 Fiyat: {current_price:.10g}\n"
                f"📈 TP1 gerçekleşti\n"
                f"🛡️ SL giriş fiyatına taşındı"
            )

            changed = True

        # ====================================================
        # TP2
        # ====================================================

        if (
            trade.get("status") == "OPEN"
            and current_price >= tp2
        ):

            pnl = close_paper_trade(
                trade,
                current_price,
                "TAKE PROFIT 2"
            )

            log(
                f"TP2: {symbol} "
                f"PnL={pnl:.2f}%"
            )

            send_telegram(
                f"🎯🎯 PAPER TRADE TP2\n\n"
                f"🪙 {symbol}\n"
                f"💰 Çıkış: {current_price:.10g}\n"
                f"📈 Toplam PnL: {pnl:.2f}%"
            )

            changed = True

    return changed


# ============================================================
# SAT SİNYALİ İLE PAPER TRADE KAPAT
# ============================================================

def close_on_sell_signal(
    trades,
    symbol,
    price
):

    changed = False

    for trade in trades:

        if trade.get("status") != "OPEN":
            continue

        if trade.get("symbol") != symbol:
            continue

        pnl = close_paper_trade(
            trade,
            price,
            "SELL SIGNAL"
        )

        log(
            f"SELL SIGNAL: {symbol} "
            f"PnL={pnl:.2f}%"
        )

        send_telegram(
            f"📉 PAPER TRADE SAT\n\n"
            f"🪙 {symbol}\n"
            f"💰 Çıkış: {price:.10g}\n"
            f"📊 PnL: {pnl:.2f}%\n"
            f"ℹ️ Sebep: SELL SIGNAL"
        )

        changed = True

    return changed


# ============================================================
# BUY BİLDİRİMİ
# ============================================================

def send_buy_signal(
    symbol,
    result
):

    price = result["price"]

    sl = price * (1 - SL_PERCENT)
    tp1 = price * (1 + TP1_PERCENT)
    tp2 = price * (1 + TP2_PERCENT)

    message = (
        "🚨 KRİPTO SİNYALİ 🚨\n\n"
        f"🪙 {symbol}\n"
        "📈 Yön: LONG\n\n"
        f"💰 Giriş: {price:.10g}\n"
        f"🛑 SL: {sl:.10g}\n"
        f"🎯 TP1: {tp1:.10g}\n"
        f"🎯 TP2: {tp2:.10g}\n\n"
        f"📊 RSI: {result['rsi']:.2f}\n"
        f"📈 EMA200: {result['ema200']:.10g}\n"
        "✅ 4/4 indikatör onaylandı\n\n"
        "⏱️ Timeframe: 15 dakika"
    )

    send_telegram(message)


def send_sell_signal(
    symbol,
    result
):

    message = (
        "🔻 KRİPTO SAT SİNYALİ 🔻\n\n"
        f"🪙 {symbol}\n"
        "📉 Yön: SAT\n\n"
        f"💰 Fiyat: {result['price']:.10g}\n"
        f"📊 RSI: {result['rsi']:.2f}\n"
        f"📈 EMA200: {result['ema200']:.10g}\n"
        f"📊 Skor: {result['score']}/4\n\n"
        "⚠️ 4/4 indikatör uyumu bozuldu\n"
        "⏱️ Timeframe: 15 dakika"
    )

    send_telegram(message)


# ============================================================
# ANA TARAMA
# ============================================================

def main():

    log("=" * 60)
    log("OKX CRYPTO SCANNER BAŞLADI")
    log("=" * 60)

    # --------------------------------------------------------
    # Telegram testi
    # --------------------------------------------------------

    send_telegram(
        "🟢 OKX Crypto Scanner çalıştı.\n"
        "Telegram bağlantısı aktif."
    )

    # --------------------------------------------------------
    # Paper trades
    # --------------------------------------------------------

    trades = load_paper_trades()

    log(
        f"Paper trade kayıtları: {len(trades)}"
    )

    # --------------------------------------------------------
    # Ticker verileri
    # --------------------------------------------------------

    try:
        tickers = get_tickers()

    except Exception as e:

        log(
            f"Ticker verileri alınamadı: {e}"
        )

        return

    prices = {
        symbol: data["last"]
        for symbol, data in tickers.items()
        if data["last"] > 0
    }

    # --------------------------------------------------------
    # Açık işlemleri güncelle
    # --------------------------------------------------------

    if update_paper_trades(
        trades,
        prices
    ):

        save_paper_trades(
            trades
        )

    # --------------------------------------------------------
    # Sembolleri al
    # --------------------------------------------------------

    try:
        symbols = get_symbols()

    except Exception as e:

        log(
            f"OKX sembolleri alınamadı: {e}"
        )

        return

    # --------------------------------------------------------
    # Hacme göre sırala
    # --------------------------------------------------------

    symbols = [
        symbol
        for symbol in symbols
        if symbol in tickers
    ]

    symbols.sort(
        key=lambda symbol:
        tickers[symbol]["volCcy24h"],
        reverse=True
    )

    symbols = symbols[
        :MAX_SYMBOLS_TO_SCAN
    ]

    log(
        f"Taranacak coin sayısı: {len(symbols)}"
    )

    buy_count = 0
    sell_count = 0
    error_count = 0

    # --------------------------------------------------------
    # COIN TARAMA
    # --------------------------------------------------------

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            log(
                f"[{index}/{len(symbols)}] "
                f"Taranıyor: {symbol}"
            )

            df = get_candles(
                symbol
            )

            if df.empty:
                continue

            result = calculate_signal(
                df
            )

            if result is None:
                continue

            signal = result["signal"]

            # ------------------------------------------------
            # BUY
            # ------------------------------------------------

            if signal == "BUY":

                buy_count += 1

                log(
                    f"🚨 BUY: {symbol} "
                    f"price={result['price']:.10g} "
                    f"RSI={result['rsi']:.2f}"
                )

                send_buy_signal(
                    symbol,
                    result
                )

                opened = open_paper_trade(
                    trades,
                    symbol,
                    result["price"]
                )

                if opened:
                    save_paper_trades(
                        trades
                    )

            # ------------------------------------------------
            # SELL
            # ------------------------------------------------

            elif signal == "SELL":

                sell_count += 1

                log(
                    f"🔻 SELL: {symbol} "
                    f"price={result['price']:.10g}"
                )

                send_sell_signal(
                    symbol,
                    result
                )

                if close_on_sell_signal(
                    trades,
                    symbol,
                    result["price"]
                ):

                    save_paper_trades(
                        trades
                    )

            # ------------------------------------------------
            # Küçük bekleme
            # ------------------------------------------------

            time.sleep(0.15)

        except Exception as e:

            error_count += 1

            log(
                f"❌ {symbol} hata: {e}"
            )

    # --------------------------------------------------------
    # SON KAYIT
    # --------------------------------------------------------

    save_paper_trades(
        trades
    )

    open_count = sum(
        1
        for trade in trades
        if trade.get("status") == "OPEN"
    )

    closed_count = sum(
        1
        for trade in trades
        if trade.get("status") == "CLOSED"
    )

    total_pnl = sum(
        safe_float(
            trade.get("realized_pnl_pct")
        )
        for trade in trades
        if trade.get("status") == "CLOSED"
    )

    # --------------------------------------------------------
    # ÖZET
    # --------------------------------------------------------

    log("=" * 60)
    log("TARAMA TAMAMLANDI")
    log(f"BUY sinyali: {buy_count}")
    log(f"SELL sinyali: {sell_count}")
    log(f"Hata: {error_count}")
    log(f"Açık paper trade: {open_count}")
    log(f"Kapanmış paper trade: {closed_count}")
    log(f"Toplam gerçekleşmiş PnL: {total_pnl:.2f}%")
    log("=" * 60)


# ============================================================
# PROGRAMI BAŞLAT
# ============================================================

if __name__ == "__main__":
    main()
