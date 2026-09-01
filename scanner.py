import requests
import pandas as pd
import time

# OKX'ten mum verisi
def get_candles(inst_id, limit=250):
    url = "https://www.okx.com/api/v5/market/candles"
    params = {
        "instId": inst_id,
        "bar": "15m",
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
            "ts", "open", "high", "low", "close",
            "vol", "volCcy", "volCcyQuote", "confirm"
        ]
    )

    df = df.iloc[::-1].reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    return df


# RSI 14
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


# EMA 200
def calculate_ema(close, period=200):
    return close.ewm(
        span=period,
        adjust=False
    ).mean()


# Supertrend 10 / 3
def calculate_supertrend(df, period=10, multiplier=3.0):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
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

    return pd.Series(direction, index=df.index)


# UT Bot ATR 10 / Sensitivity 1
def calculate_utbot(df):

    close = df["close"]

    atr = (
        df["high"]
        .sub(df["low"])
        .ewm(alpha=1 / 10, adjust=False)
        .mean()
    )

    nloss = atr

    trailing_stop = [0.0] * len(df)

    trailing_stop[0] = close.iloc[0] - nloss.iloc[0]

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
            trailing_stop[i] = close.iloc[i] - nloss.iloc[i]

        else:
            trailing_stop[i] = close.iloc[i] + nloss.iloc[i]

    trailing_stop = pd.Series(
        trailing_stop,
        index=df.index
    )

    return close > trailing_stop, close < trailing_stop


# Tek coin kontrolü
def check_coin(inst_id):

    df = get_candles(inst_id)

    if df is None or len(df) < 210:
        return None

    df["rsi"] = calculate_rsi(df["close"], 14)
    df["ema200"] = calculate_ema(df["close"], 200)

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
        return "AL"

    if sell and not previous_sell:
        return "SAT"

    return None


# OKX USDT paritelerini al
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
            symbols.append(item["instId"])

    return symbols


# Ana tarama
if __name__ == "__main__":

    symbols = get_symbols()

    print(
        f"OKX'te {len(symbols)} USDT coin bulundu."
    )

    for symbol in symbols:

        try:

            signal = check_coin(symbol)

            if signal:

                print(
                    f"{signal} SİNYALİ: {symbol}"
                )

        except Exception as e:

            print(
                f"Hata {symbol}: {e}"
            )

        time.sleep(0.1)
