import os
import json
import requests

PAPER_FILE = "paper_trades.json"

OKX_URL = "https://www.okx.com/api/v5/market/ticker"
TELEGRAM_URL = "https://api.telegram.org/bot"


def load_trades():
    if not os.path.exists(PAPER_FILE):
        return []

    try:
        with open(PAPER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def get_price(symbol):
    try:
        response = requests.get(
            OKX_URL,
            params={"instId": symbol},
            timeout=10
        )

        data = response.json()

        if data.get("code") != "0":
            return None

        if not data.get("data"):
            return None

        return float(data["data"][0]["last"])

    except Exception as e:
        print(f"Fiyat hatası {symbol}: {e}")
        return None


def send_message(chat_id, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        print("TELEGRAM_BOT_TOKEN bulunamadı.")
        return

    url = f"{TELEGRAM_URL}{token}/sendMessage"

    try:
        requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message
            },
            timeout=10
        )

        print("Telegram mesajı gönderildi.")

    except Exception as e:
        print("Telegram gönderme hatası:", e)


def get_updates():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        return []

    url = f"{TELEGRAM_URL}{token}/getUpdates"

    try:
        response = requests.get(
            url,
            params={
                "timeout": 0,
                "limit": 100
            },
            timeout=10
        )

        data = response.json()

        if not data.get("ok"):
            print("Telegram hatası:", data)
            return []

        return data.get("result", [])

    except Exception as e:
        print("Telegram bağlantı hatası:", e)
        return []


def confirm_updates(update_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        return

    url = f"{TELEGRAM_URL}{token}/getUpdates"

    try:
        requests.get(
            url,
            params={
                "offset": update_id + 1,
                "limit": 1
            },
            timeout=10
        )

    except Exception as e:
        print("Update onaylama hatası:", e)


def format_price(price):
    if price >= 100:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")

    return f"{price:.8f}".
