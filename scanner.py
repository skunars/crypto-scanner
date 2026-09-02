import requests
import pandas as pd
import time
import os


# ==============================
# TELEGRAM BİLDİRİM
# ==============================

def send_telegram(message):

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram bilgileri bulunamadı.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    data = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(
            url,
            data=data,
            timeout=10
        )

        if response.status_code == 200:
            print("Telegram bildirimi gönderildi.")
        else:
            print("Telegram hatası:", response.text)

    except Exception as e:
        print("Telegram bağlantı hatası:", e)


# ==============================
# OKX'TEN MUM VERİSİ
# ==============================

def get_candles(inst_id, limit=250):

    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": inst_id,
        "bar": "15m",
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

    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    return df


# ==============================
# RSI 14
# ==============================

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


# ==============================
# EMA 200
# ==============================

def calculate_ema(close, period=200):

    return close.ewm(
        span=period,
        adjust=False
    ).mean()


# ==============================
# SUPERTREND 10 / 3
# ==============================

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


# ==============================
# UT BOT ATR 10 / SENSITIVITY 1
# ==============================

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


# ==============================
# SİNYAL GÜÇ SKORU
# ==============================

def calculate_signal_score(row, signal):

    score = 0

    rsi = row["rsi"]
    close = row["close"]
    ema200 = row["ema200"]

    ema_distance = (
        (close - ema200) / ema200
    ) * 100

    # RSI gücü
    if signal == "AL":

        if rsi >= 60:
            score += 30
        elif rsi >= 55:
            score += 20
        elif rsi > 50:
            score += 10

        # EMA mesafesi
        if ema_distance >= 3:
            score += 30
        elif ema_distance >= 1:
            score += 20
        elif ema_distance > 0:
            score += 10

        # Supertrend
        if row["supertrend"] == -1:
            score += 20

        # UT Bot
        if row["ut_bull"]:
            score += 20

    elif signal == "SAT":

        if rsi <= 40:
            score += 30
        elif rsi <= 45:
            score += 20
        elif rsi < 50:
            score += 10

        # EMA mesafesi
        if ema_distance <= -3:
            score += 30
        elif ema_distance <= -1:
            score += 20
        elif ema_distance < 0:
            score += 10

        # Supertrend
        if row["supertrend"] == 1:
            score += 20

        # UT Bot
        if row["ut_bear"]:
            score += 20

    return score


def get_score_text(score):

    if score >= 80:
        return "ÇOK GÜÇLÜ"

    elif score >= 60:
        return "GÜÇLÜ"

    elif score >= 40:
        return "ORTA"

    else:
        return "ZAYIF"


# ==============================
# TEK COIN KONTROLÜ
# ==============================

def check_coin(inst_id):

    df = get_candles(inst_id)

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

    if buy and not previous_buy:

        score = calculate_signal_score(
            last,
            "AL"
        )

        return "AL", score, last

    if sell and not previous_sell:

        score = calculate_signal_score(
            last,
            "SAT"
        )

        return "SAT", score, last

    return None


# ==============================
# OKX USDT PARİTELERİ
# ==============================

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


# ==============================
# ANA TARAMA
# ==============================

if __name__ == "__main__":

    symbols = get_symbols()

    print(
        f"OKX'te {len(symbols)} USDT coin bulundu."
    )

    for symbol in symbols:

        try:

            result = check_coin(symbol)

            if result:

                signal, score, row = result

                score_text = get_score_text(score)

                if signal == "AL":
                    emoji = "🟢"
                else:
                    emoji = "🔴"

                ema_distance = (
                    (row["close"] - row["ema200"])
                    / row["ema200"]
                ) * 100

                message = (
                    f"🚨 KRİPTO SİNYALİ 🚨\n\n"
                    f"{emoji} {signal} SİNYALİ: {symbol}\n\n"
                    f"🎯 Sinyal Gücü: {score}/100\n"
                    f"💪 {score_text}\n\n"
                    f"📊 RSI: {row['rsi']:.2f}\n"
                    f"📈 EMA 200 mesafesi: {ema_distance:.2f}%\n"
                    f"📉 Supertrend: "
                    f"{'🟢 Yükseliş' if signal == 'AL' else '🔴 Düşüş'}\n"
                    f"🤖 UT Bot: "
                    f"{'🟢 AL' if signal == 'AL' else '🔴 SAT'}\n\n"
                    f"⏱️ Zaman dilimi: 15 dakika"
                )

                print(message)

                send_telegram(message)

        except Exception as e:

            print(
                f"Hata {symbol}: {e}"
            )

        time.sleep(0.1)
