import requests
import pandas as pd
import time
import os
import json
from datetime import datetime, timezone


PAPER_FILE = "paper_trades.json"

# =========================================================
# İŞLEM AYARLARI
# =========================================================

SL_PERCENT = 0.02
TP1_PERCENT = 0.03
TP2_PERCENT = 0.06

# Maksimum aynı anda açık sanal işlem
MAX_OPEN_TRADES = 10

# Aynı coin kapandıktan sonra tekrar giriş bekleme süresi
REENTRY_COOLDOWN_MINUTES = 60


def now_utc():
    return datetime.now(timezone.utc).isoformat()


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
            print(
                "Telegram hatası:",
                response.text
            )

    except Exception as e:
        print(
            "Telegram bağlantı hatası:",
            e
        )


# =========================================================
# SANAL İŞLEM KAYITLARI
# =========================================================

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

        print(
            "Paper trade dosyası okunamadı:",
            e
        )

        return []


def save_paper_trades(trades):

    try:

        with open(
            PAPER_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                trades,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "Paper trade dosyası kaydedilemedi:",
            e
        )


def get_open_trade(trades, symbol):

    for trade in trades:

        if (
            trade.get("symbol") == symbol
            and trade.get("status") == "OPEN"
        ):

            return trade

    return None


def count_open_trades(trades):

    return sum(
        1
        for trade in trades
        if trade.get("status") == "OPEN"
    )


def is_in_reentry_cooldown(trades, symbol):

    closed_trades = [
        trade
        for trade in trades
        if (
            trade.get("symbol") == symbol
            and trade.get("status") == "CLOSED"
            and trade.get("exit_time")
        )
    ]

    if not closed_trades:
        return False

    latest_trade = max(
        closed_trades,
        key=lambda x: x.get(
            "exit_time",
            ""
        )
    )

    try:

        exit_time = datetime.fromisoformat(
            latest_trade["exit_time"]
        )

        elapsed_minutes = (
            datetime.now(timezone.utc)
            - exit_time
        ).total_seconds() / 60

        return (
            elapsed_minutes
            < REENTRY_COOLDOWN_MINUTES
        )

    except Exception:

        return False


def calculate_trade_pnl(
    entry_price,
    exit_price,
    portion
):

    return (
        (
            (exit_price - entry_price)
            / entry_price
        )
        * 100
        * portion
    )


def open_paper_trade(
    trades,
    symbol,
    entry_price,
    score
):

    trade = {

        "symbol": symbol,

        "side": "LONG",

        "status": "OPEN",

        "entry_price": float(
            entry_price
        ),

        "sl": float(
            entry_price
            * (1 - SL_PERCENT)
        ),

        "tp1": float(
            entry_price
            * (1 + TP1_PERCENT)
        ),

        "tp2": float(
            entry_price
            * (1 + TP2_PERCENT)
        ),

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

    if trade.get("status") != "OPEN":
        return float(
            trade.get(
                "realized_pnl_pct",
                0
            )
        )

    entry_price = float(
        trade["entry_price"]
    )

    exit_price = float(
        exit_price
    )

    remaining_portion = (
        float(
            trade.get(
                "remaining_pct",
                0
            )
        ) / 100
    )

    pnl = calculate_trade_pnl(
        entry_price,
        exit_price,
        remaining_portion
    )

    trade["realized_pnl_pct"] = round(
        float(
            trade.get(
                "realized_pnl_pct",
                0
            )
        ) + pnl,
        4
    )

    trade["status"] = "CLOSED"

    trade["remaining_pct"] = 0

    trade["exit_price"] = exit_price

    trade["exit_time"] = now_utc()

    trade["exit_reason"] = reason

    return trade["realized_pnl_pct"]



