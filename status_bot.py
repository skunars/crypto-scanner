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
            data = json.load(f)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            return list(data.values())

        return []

    except Exception as e:
        print("Trade okuma hatası:", e)
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

    # Telegram mesaj sınırı nedeniyle mesajı parçalıyoruz.
    chunks = [
        message[i:i + 4000]
        for i in range(0, len(message), 4000)
    ]

    try:
        for chunk in chunks:
            response = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": chunk
                },
                timeout=10
            )

            print("Telegram cevap:", response.text)

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


def format_price(price):
    if price >= 100:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")

    return f"{price:.8f}"


def create_status():
    trades = load_trades()

    open_trades = [
        trade for trade in trades
        if str(trade.get("status", "")).upper() == "OPEN"
    ]

    if not open_trades:
        return "📊 PAPER TRADE STATUS\n\nAçık işlem bulunmuyor."

    message = "📊 PAPER TRADE STATUS\n\n"

    for trade in open_trades:
        symbol = trade.get("symbol", "UNKNOWN")
        side = trade.get("side", "UNKNOWN")
        entry = trade.get("entry_price")
        sl = trade.get("sl")
        tp1 = trade.get("tp1")
        tp2 = trade.get("tp2")

        current = get_price(symbol)

        message += f"🪙 {symbol}\n"
        message += f"📈 Yön: {side}\n"

        if entry is not None:
            message += f"🎯 Giriş: {format_price(float(entry))}\n"

        if current is not None:
            message += f"💰 Güncel: {format_price(current)}\n"

        if sl is not None:
            message += f"🛑 SL: {format_price(float(sl))}\n"

        if tp1 is not None:
            message += f"🥇 TP1: {format_price(float(tp1))}\n"

        if tp2 is not None:
            message += f"🥈 TP2: {format_price(float(tp2))}\n"

        message += "\n"

    return message


def main():
    print("Telegram Status Bot başladı.")

    updates = get_updates()

    if not updates:
        print("Bekleyen Telegram mesajı bulunamadı.")
        return

    for update in updates:
        update_id = update.get("update_id")

        message = update.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = chat.get("id")

        if not chat_id:
            continue

        print(f"Gelen mesaj: {text}")

        if text.strip().lower() == "/status":
            status = create_status()
            send_message(chat_id, status)

        # İşlenen Telegram mesajını temizle
        if update_id is not None:
            token = os.getenv("TELEGRAM_BOT_TOKEN")

            if token:
                url = f"{TELEGRAM_URL}{token}/getUpdates"

                try:
                    requests.get(
                        url,
                        params={"offset": update_id + 1},
                        timeout=10
                    )
                except Exception as e:
                    print("Update temizleme hatası:", e)


if __name__ == "__main__":
    main()
