import requests
import pandas as pd
import time
import os
import json
from datetime import datetime, timezone


PAPER_FILE = "paper_trades.json"

SL_PERCENT = 0.02
TP1_PERCENT = 0.03
TP2_PERCENT = 0.06


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def send_telegram(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram bilgileri bulunamadı.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}

    try:
        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            print("Telegram bildirimi gönderildi.")
        else:
            print("Telegram hatası:", response.text)

    except Exception as e:
        print("Telegram bağlantı hatası:", e)


# =========================================================
# SANAL İŞLEM KAYITLARI
# =========================================================

def load_paper_trades():
    if not os.path.exists(PAPER_FILE):
        return []

    try:
        with open(PAPER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        return []

    except Exception:
        return []


def save_paper_trades(trades):
    with open(PAPER_FILE, "w", encoding="utf-8") as f:
        json.dump(
            trades,
            f,
            ensure_ascii=False,
            indent=2
        )


def get_open_trade(trades, symbol):
    for trade in trades:
        if (
            trade.get("symbol") == symbol
            and trade.get("status") == "OPEN"
        ):
            return trade

    return None


def calculate_trade_pnl(entry_price, exit_price, portion):
    return (
        ((exit_price - entry_price) / entry_price)
        * 100
        * portion
    )


def open_paper_trade(trades, symbol, entry_price, score):
    trade = {
        "symbol": symbol,
        "side": "LONG",
        "status": "OPEN",

        "entry_price": float(entry_price),

        "sl": float(entry_price * (1 - SL_PERCENT)),
        "tp1": float(entry_price * (1 + TP1_PERCENT)),
        "tp2": float(entry_price * (1 + TP2_PERCENT)),

        "tp1_hit": False,
        "remaining_pct": 100,

        "realized_pnl_pct": 0.0,

        "score": int(score),

        "entry_time": now_utc(),
        "exit_time": None,
        "exit_price": None,
        "exit_reason": None
    }

    trades.append(trade)

    return trade


def close_paper_trade(
    trade,
    exit_price,
    reason
):
    entry_price = float(trade["entry_price"])
    exit_price = float(exit_price)

    remaining_portion = (
        float(trade["remaining_pct"]) / 100
    )

    pnl = calculate_trade_pnl(
        entry_price,
        exit_price,
        remaining_portion
    )

    trade["realized_pnl_pct"] = round(
        float(trade.get("realized_pnl_pct", 0))
        + pnl,
        4
    )

    trade["status"] = "CLOSED"
    trade["remaining_pct"] = 0
    trade["exit_price"] = exit_price
    trade["exit_time"] = now_utc()
    trade["exit_reason"] = reason

    return trade["realized_pnl_pct"]


def update_paper_trade(trade, row):
    messages = []

    if trade.get("status") != "OPEN":
        return messages

    entry_price = float(trade["entry_price"])
    high = float(row["high"])
    low = float(row["low"])

    sl = float(trade["sl"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])

    tp1_hit = bool(trade.get("tp1_hit", False))

    # -----------------------------------------------------
    # ÖNCE STOP LOSS
    # Aynı mumda hem SL hem TP görülürse konservatif
    # olarak SL'nin önce çalıştığını kabul ediyoruz.
    # -----------------------------------------------------

    if low <= sl:

        pnl = close_paper_trade(
            trade,
            sl,
            "STOP LOSS"
        )

        messages.append(
            f"🛑 SANAL STOP LOSS\n\n"
            f"Coin: {trade['symbol']}\n"
            f"Çıkış: {sl:.8f}\n"
            f"Toplam K/Z: {pnl:+.2f}%"
        )

        return messages

    # -----------------------------------------------------
    # TP1
    # -----------------------------------------------------

    if not tp1_hit and high >= tp1:

        # Pozisyonun %50'si TP1'de kapanıyor.
        tp1_pnl = TP1_PERCENT * 100 * 0.50

        trade["realized_pnl_pct"] = round(
            float(trade.get("realized_pnl_pct", 0))
            + tp1_pnl,
            4
        )

        trade["tp1_hit"] = True
        trade["remaining_pct"] = 50

        # TP1 sonrası stop giriş fiyatına çekiliyor.
        trade["sl"] = entry_price

        messages.append(
            f"🎯 SANAL TP1\n\n"
            f"Coin: {trade['symbol']}\n"
            f"TP1: {tp1:.8f}\n"
            f"Pozisyonun %50'si kapatıldı.\n"
            f"TP1 katkısı: +{tp1_pnl:.2f}%\n"
            f"🛡 Stop giriş fiyatına çekildi."
        )

        tp1_hit = True

    # -----------------------------------------------------
    # TP2
    # -----------------------------------------------------

    if tp1_hit and high >= tp2:

        # Kalan %50, +%6 seviyesinde kapanıyor.
        tp2_pnl = TP2_PERCENT * 100 * 0.50

        trade["realized_pnl_pct"] = round(
            float(trade.get("realized_pnl_pct", 0))
            + tp2_pnl,
            4
        )

        trade["status"] = "CLOSED"
        trade["remaining_pct"] = 0
        trade["exit_price"] = tp2
        trade["exit_time"] = now_utc()
        trade["exit_reason"] = "TP2"

        total_pnl = trade["realized_pnl_pct"]

        messages.append(
            f"🎯🎯 SANAL TP2\n\n"
            f"Coin: {trade['symbol']}\n"
            f"TP2: {tp2:.8f}\n"
            f"Pozisyon tamamen kapatıldı.\n"
            f"💰 TOPLAM K/Z: {total_pnl:+.2f}%"
        )

    return messages


def get_paper_summary(trades):
    closed = [
        t for t in trades
        if t.get("status") == "CLOSED"
    ]

    wins = [
        t for t in closed
        if float(t.get("realized_pnl_pct", 0)) > 0
    ]

    losses = [
        t for t in closed
        if float(t.get("realized_pnl_pct", 0)) < 0
    ]

    total_pnl = sum(
        float(t.get("realized_pnl_pct", 0))
        for t in closed
    )

    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "pnl": total_pnl
    }


# =========================================================
# OKX VERİLERİ
# =========================================================

def get_candles(inst_id, bar="15m", limit=250):

    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": inst_id,
        "bar": bar,
        "limit": str(limit)
    }

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    data = response.json()

    if data.get("code") != "0":
        return None

    candles = data["data"]

    df = pd.DataFrame(
        candles,
        columns=[
            "ts",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "volCcy",
            "volCcyQuote",
            "confirm"
        ]
    )

    df = df.iloc[::-1].reset_index(drop=True)

    for col in [
        "open",
        "high",
        "low",
        "close",
        "vol"
    ]:
        df[col] = df[col].astype(float)

    return df


# =========================================================
# İNDİKATÖRLER
# =========================================================

def calculate_rsi(close, period=14):

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_ema(close, period=200):

    return close.ewm(
        span=period,
        adjust=False
    ).mean()


def calculate_supertrend(
    df,
    period=10,
    multiplier=3.0
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    hl2 = (high + low) / 2

    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    direction = [1] * len(df)

    for i in range(1, len(df)):

        if close.iloc[i] > upper.iloc[i - 1]:
            direction[i] = -1

        elif close.iloc[i] < lower.iloc[i - 1]:
            direction[i] = 1

        else:
            direction[i] = direction[i - 1]

    return pd.Series(
        direction,
        index=df.index
    )


def calculate_utbot(df):

    close = df["close"]

    atr = (
        df["high"]
        .sub(df["low"])
        .ewm(
            alpha=1 / 10,
            adjust=False
        )
        .mean()
    )

    nloss = atr

    trailing_stop = [0.0] * len(df)

    trailing_stop[0] = (
        close.iloc[0] - nloss.iloc[0]
    )

    for i in range(1, len(df)):

        previous_stop = trailing_stop[i - 1]

        if (
            close.iloc[i] > previous_stop
            and close.iloc[i - 1] > previous_stop
        ):

            trailing_stop[i] = max(
                previous_stop,
                close.iloc[i] - nloss.iloc[i]
            )

        elif (
            close.iloc[i] < previous_stop
            and close.iloc[i - 1] < previous_stop
        ):

            trailing_stop[i] = min(
                previous_stop,
                close.iloc[i] + nloss.iloc[i]
            )

        elif close.iloc[i] > previous_stop:

            trailing_stop[i] = (
                close.iloc[i] - nloss.iloc[i]
            )

        else:

            trailing_stop[i] = (
                close.iloc[i] + nloss.iloc[i]
            )

    trailing_stop = pd.Series(
        trailing_stop,
        index=df.index
    )

    return (
        close > trailing_stop,
        close < trailing_stop
    )


# =========================================================
# SİNYAL SKORU
# =========================================================

def calculate_signal_score(row, signal):

    score = 0

    rsi = row["rsi"]
    close = row["close"]
    ema200 = row["ema200"]

    ema_distance = (
        (close - ema200)
        / ema200
    ) * 100

    volume_ratio = row["volume_ratio"]

    if signal == "AL":

        if rsi >= 60:
            score += 30
        elif rsi >= 55:
            score += 20
        elif rsi > 50:
            score += 10

        if ema_distance >= 3:
            score += 30
        elif ema_distance >= 1:
            score += 20
        elif ema_distance > 0:
            score += 10

        if row["supertrend"] == -1:
            score += 20

        if row["ut_bull"]:
            score += 20

        if volume_ratio >= 1.5:
            score += 10
        elif volume_ratio >= 1.2:
            score += 5

    elif signal == "SAT":

        if rsi <= 40:
            score += 30
        elif rsi <= 45:
            score += 20
        elif rsi < 50:
            score += 10

        if ema_distance <= -3:
            score += 30
        elif ema_distance <= -1:
            score += 20
        elif ema_distance < 0:
            score += 10

        if row["supertrend"] == 1:
            score += 20

        if row["ut_bear"]:
            score += 20

        if volume_ratio >= 1.5:
            score += 10
        elif volume_ratio >= 1.2:
            score += 5

    return min(score, 100)


def get_score_text(score):

    if score >= 80:
        return "ÇOK GÜÇLÜ"

    elif score >= 60:
        return "GÜÇLÜ"

    elif score >= 40:
        return "ORTA"

    else:
        return "ZAYIF"


# =========================================================
# COIN KONTROLÜ
# =========================================================

def check_coin(inst_id):

    df = get_candles(
        inst_id,
        bar="15m",
        limit=250
    )

    if df is None or len(df) < 210:
        return None

    df["rsi"] = calculate_rsi(
        df["close"],
        14
    )

    df["ema200"] = calculate_ema(
        df["close"],
        200
    )

    df["supertrend"] = calculate_supertrend(
        df,
        10,
        3.0
    )

    df["ut_bull"], df["ut_bear"] = calculate_utbot(df)

    df["volume_avg"] = (
        df["vol"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["vol"]
        / df["volume_avg"]
    )

    last = df.iloc[-2]
    previous = df.iloc[-3]

    buy = (
        last["close"] > last["ema200"]
        and last["rsi"] > 50
        and last["supertrend"] == -1
        and last["ut_bull"]
    )

    sell = (
        last["close"] < last["ema200"]
        and last["rsi"] < 50
        and last["supertrend"] == 1
        and last["ut_bear"]
    )

    previous_buy = (
        previous["close"] > previous["ema200"]
        and previous["rsi"] > 50
        and previous["supertrend"] == -1
        and df["ut_bull"].iloc[-3]
    )

    previous_sell = (
        previous["close"] < previous["ema200"]
        and previous["rsi"] < 50
        and previous["supertrend"] == 1
        and df["ut_bear"].iloc[-3]
    )

    df_1h = get_candles(
        inst_id,
        bar="1H",
        limit=250
    )

    if df_1h is None or len(df_1h) < 210:
        return None

    df_1h["ema200"] = calculate_ema(
        df_1h["close"],
        200
    )

    df_1h["supertrend"] = calculate_supertrend(
        df_1h,
        10,
        3.0
    )

    last_1h = df_1h.iloc[-2]

    hourly_bull = (
        last_1h["close"] > last_1h["ema200"]
        and last_1h["supertrend"] == -1
    )

    hourly_bear = (
        last_1h["close"] < last_1h["ema200"]
        and last_1h["supertrend"] == 1
    )

    signal = None
    score = 0
    confirmed = False

    if buy and not previous_buy and hourly_bull:

        signal = "AL"

        score = calculate_signal_score(
            last,
            "AL"
        )

        confirmed = True

    elif sell and not previous_sell and hourly_bear:

        signal = "SAT"

        score = calculate_signal_score(
            last,
            "SAT"
        )

        confirmed = True

    return {
        "signal": signal,
        "score": score,
        "row": last,
        "row_1h": last_1h,
        "confirmed": confirmed
    }


# =========================================================
# COIN LİSTESİ
# =========================================================

def get_symbols():

    url = "https://www.okx.com/api/v5/public/instruments"

    params = {
        "instType": "SPOT"
    }

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    data = response.json()

    symbols = []

    for item in data.get("data", []):

        if (
            item.get("quoteCcy") == "USDT"
            and item.get("state") == "live"
        ):

            symbols.append(
                item["instId"]
            )

    return symbols


# =========================================================
# ANA PROGRAM
# =========================================================

if __name__ == "__main__":

    trades = load_paper_trades()

    symbols = get_symbols()

    print(
        f"OKX'te {len(symbols)} USDT coin bulundu."
    )

    for symbol in symbols:

        try:

            result = check_coin(symbol)

            if result is None:
                continue

            signal = result["signal"]
            score = result["score"]
            row = result["row"]
            row_1h = result["row_1h"]

            # -------------------------------------------------
            # ÖNCE AÇIK SANAL İŞLEMİ KONTROL ET
            # -------------------------------------------------

            open_trade = get_open_trade(
                trades,
                symbol
            )

            if open_trade:

                trade_messages = update_paper_trade(
                    open_trade,
                    row
                )

                for trade_message in trade_messages:

                    print(trade_message)
                    send_telegram(trade_message)

                if trade_messages:
                    save_paper_trades(trades)

                # Güncellemeden sonra işlem kapanmış olabilir.
                open_trade = get_open_trade(
                    trades,
                    symbol
                )

            # -------------------------------------------------
            # SAT SİNYALİ GELİRSE AÇIK LONG'U KAPAT
            # -------------------------------------------------

            if signal == "SAT" and open_trade:

                exit_price = float(row["close"])

                total_pnl = close_paper_trade(
                    open_trade,
                    exit_price,
                    "SAT SİNYALİ"
                )

                summary = get_paper_summary(
                    trades
                )

                message = (
                    f"🔴 SANAL SATIŞ\n\n"
                    f"Coin: {symbol}\n"
                    f"Çıkış: {exit_price:.8f}\n\n"
                    f"💰 İşlem K/Z: {total_pnl:+.2f}%\n"
                    f"📊 Toplam işlem: {summary['total']}\n"
                    f"🟢 Kazanan: {summary['wins']}\n"
                    f"🔴 Kaybeden: {summary['losses']}\n"
                    f"📈 Toplam K/Z: {summary['pnl']:+.2f}%"
                )

                print(message)
                send_telegram(message)

                save_paper_trades(trades)

                open_trade = None

            # -------------------------------------------------
            # AL SİNYALİ GELİRSE YENİ SANAL İŞLEM AÇ
            # -------------------------------------------------

            if signal == "AL":

                if not open_trade:

                    entry_price = float(row["close"])

                    trade = open_paper_trade(
                        trades,
                        symbol,
                        entry_price,
                        score
                    )

                    message = (
                        f"🟢 SANAL ALIŞ\n\n"
                        f"Coin: {symbol}\n"
                        f"🎯 Sinyal Gücü: {score}/100\n\n"
                        f"💵 Giriş: {entry_price:.8f}\n"
                        f"🛑 Stop Loss: {trade['sl']:.8f}\n"
                        f"🎯 TP1: {trade['tp1']:.8f} (+3%)\n"
                        f"🎯 TP2: {trade['tp2']:.8f} (+6%)\n\n"
                        f"📌 TP1'de %50 kapatılacak.\n"
                        f"🛡 TP1 sonrası stop giriş fiyatına çekilecek."
                    )

                    print(message)
                    send_telegram(message)

                    save_paper_trades(trades)

            # -------------------------------------------------
            # NORMAL SİNYAL BİLDİRİMİ
            # -------------------------------------------------

            if signal:

                score_text = get_score_text(
                    score
                )

                if signal == "AL":
                    emoji = "🟢"
                else:
                    emoji = "🔴"

                ema_distance = (
                    (row["close"] - row["ema200"])
                    / row["ema200"]
                ) * 100

                volume_ratio = row["volume_ratio"]

                if volume_ratio >= 1.5:
                    volume_text = "🔥 ÇOK YÜKSEK"

                elif volume_ratio >= 1.2:
                    volume_text = "📈 YÜKSEK"

                else:
                    volume_text = "📊 NORMAL"

                hourly_ema_distance = (
                    (row_1h["close"] - row_1h["ema200"])
                    / row_1h["ema200"]
                ) * 100

                message = (
                    f"🚨 KRİPTO SİNYALİ 🚨\n\n"
                    f"{emoji} {signal} SİNYALİ: {symbol}\n\n"
                    f"🎯 Sinyal Gücü: {score}/100\n"
                    f"💪 {score_text}\n\n"
                    f"📊 RSI 15dk: {row['rsi']:.2f}\n"
                    f"📈 EMA 200 mesafesi 15dk: {ema_distance:.2f}%\n"
                    f"🔥 Hacim: {volume_ratio:.2f}x ortalama\n"
                    f"{volume_text}\n"
                    f"📉 Supertrend 15dk: "
                    f"{'🟢 Yükseliş' if signal == 'AL' else '🔴 Düşüş'}\n"
                    f"🤖 UT Bot 15dk: "
                    f"{'🟢 AL' if signal == 'AL' else '🔴 SAT'}\n\n"
                    f"⏰ 1 Saatlik Trend: "
                    f"{'🟢 YÜKSELİŞ ONAYI' if signal == 'AL' else '🔴 DÜŞÜŞ ONAYI'}\n"
                    f"📈 EMA 200 mesafesi 1s: {hourly_ema_distance:.2f}%\n"
                    f"📉 Supertrend 1s: "
                    f"{'🟢 Yükseliş' if signal == 'AL' else '🔴 Düşüş'}\n\n"
                    f"✅ 1 SAATLİK ONAYLI SİNYAL\n"
                    f"⏱ Ana zaman dilimi: 15 dakika"
                )

                print(message)
                send_telegram(message)

        except Exception as e:

            print(
                f"Hata {symbol}: {e}"
            )

        time.sleep(0.1)

    # -----------------------------------------------------
    # SON KAYIT
    # -----------------------------------------------------

    save_paper_trades(trades)

    summary = get_paper_summary(trades)

    print(
        "\n===== SANAL İŞLEM ÖZETİ ====="
    )

    print(
        f"Toplam kapanan işlem: {summary['total']}"
    )

    print(
        f"Kazanan: {summary['wins']}"
    )

    print(
        f"Kaybeden: {summary['losses']}"
    )

    print(
        f"Toplam K/Z: {summary['pnl']:+.2f}%"
    )
