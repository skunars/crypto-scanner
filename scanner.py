import requests
import pandas as pd
import time
import os
import json
from datetime import datetime


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


def get_candles(inst_id, bar="15m", limit=250):
    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": inst_id,
        "bar": bar,
        "limit": str(limit)
    }

    response = requests.get(url, params=params, timeout=10)
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

    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)

    return df


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


def calculate_supertrend(df, period=10, multiplier=3.0):

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


def check_coin(inst_id):

    # =========================
    # 15 DAKİKALIK ANALİZ
    # =========================

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

    # Son kapanmış mum
    last = df.iloc[-2]

    # Bir önceki kapanmış mum
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

    # =========================
    # 1 SAATLİK TREND ONAYI
    # =========================

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

    # Son kapanmış 1 saatlik mum
    last_1h = df_1h.iloc[-2]

    hourly_bull = (
        last_1h["close"] > last_1h["ema200"]
        and last_1h["supertrend"] == -1
    )

    hourly_bear = (
        last_1h["close"] < last_1h["ema200"]
        and last_1h["supertrend"] == 1
    )

    # =========================
    # ONAYLI AL
    # =========================

    if buy and not previous_buy:

        if hourly_bull:

            score = calculate_signal_score(
                last,
                "AL"
            )

            return (
                "AL",
                score,
                last,
                last_1h,
                True
            )

        else:
            return None

    # =========================
    # ONAYLI SAT
    # =========================

    if sell and not previous_sell:

        if hourly_bear:

            score = calculate_signal_score(
                last,
                "SAT"
            )

            return (
                "SAT",
                score,
                last,
                last_1h,
                True
            )

        else:
            return None

    return None


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


if __name__ == "__main__":

    symbols = get_symbols()

    print(
        f"OKX'te {len(symbols)} USDT coin bulundu."
    )

    for symbol in symbols:

        try:

            result = check_coin(symbol)

            if result:

                (
                    signal,
                    score,
                    row,
                    row_1h,
                    confirmed
                ) = result

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

                    f"{emoji} {signal} SİNYALİ: "
                    f"{symbol}\n\n"

                    f"🎯 Sinyal Gücü: "
                    f"{score}/100\n"

                    f"💪 {score_text}\n\n"

                    f"📊 RSI 15dk: "
                    f"{row['rsi']:.2f}\n"

                    f"📈 EMA 200 mesafesi 15dk: "
                    f"{ema_distance:.2f}%\n"

                    f"🔥 Hacim: "
                    f"{volume_ratio:.2f}x ortalama\n"

                    f"{volume_text}\n"

                    f"📉 Supertrend 15dk: "
                    f"{'🟢 Yükseliş' if signal == 'AL' else '🔴 Düşüş'}\n"

                    f"🤖 UT Bot 15dk: "
                    f"{'🟢 AL' if signal == 'AL' else '🔴 SAT'}\n\n"

                    f"⏰ 1 Saatlik Trend: "
                    f"{'🟢 YÜKSELİŞ ONAYI' if signal == 'AL' else '🔴 DÜŞÜŞ ONAYI'}\n"

                    f"📈 EMA 200 mesafesi 1s: "
                    f"{hourly_ema_distance:.2f}%\n"

                    f"📉 Supertrend 1s: "
                    f"{'🟢 Yükseliş' if signal == 'AL' else '🔴 Düşüş'}\n\n"

                    f"✅ 1 SAATLİK ONAYLI SİNYAL\n"

                    f"⏱️ Ana zaman dilimi: "
                    f"15 dakika"
                )

                print(message)

                send_telegram(message)

        except Exception as e:

            print(
                f"Hata {symbol}: {e}"
            )

        time.sleep(0.1)
