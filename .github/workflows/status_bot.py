import os
import json
import requests


PAPER_FILE = "paper_trades.json"

OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker"
TELEGRAM_API = "https://api.telegram.org/bot"


def load_paper_trades():
    if not os.path.exists(PAPER_FILE):
        return []

    try:
        with open(PAPER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, list) else []

    except Exception as e:
        print("paper_trades.json okunamadı:", e)
        return []


def format_price(price):
    price = float(price)

    if price >= 100:
        text = f"{price:.2f}"
    elif price >= 1:
        text = f"{price:.4f}"
    else:
        text = f"{price:.8f}"

    return text.rstrip("0").rstrip(".")


def get_current_price(symbol):
    try:
        response = requests.get(
            OKX_TICKER_URL,
            params={"instId": symbol},
            timeout=10
        )

        data = response.json()

        if data.get("code") != "0":
            return None

        ticker_data = data.get("data", [])

        if not ticker_data:
            return None

        return float(ticker_data[0]["last"])

    except Exception as e:
        print(f"{symbol} fiyat alınamadı:", e)
        return None


def send_telegram(message, chat_id=None):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    default_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token:
        print("TELEGRAM_BOT_TOKEN bulunamadı.")
        return False

    if chat_id is None:
        chat_id = default_chat_id

    if not chat_id:
        print("TELEGRAM_CHAT_ID bulunamadı.")
        return False

    url = f"{TELEGRAM_API}{bot_token}/sendMessage"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message
            },
            timeout=10
        )

        if response.status_code == 200:
            print("Telegram mesajı gönderildi.")
            return True

        print("Telegram gönderme hatası:", response.text)
        return False

    except Exception as e:
        print("Telegram bağlantı hatası:", e)
        return False


def get_updates():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        print("TELEGRAM_BOT_TOKEN bulunamadı.")
        return []

    url = f"{TELEGRAM_API}{bot_token}/getUpdates"

    try:
        response = requests.get(
            url,
            params={
                "timeout": 0,
                "limit": 100,
                "allowed_updates": json.dumps(["message"])
            },
            timeout=10
        )

        data = response.json()

        if data.get("ok") is not True:
            print("Telegram getUpdates hatası:", data)
            return []

        return data.get("result", [])

    except Exception as e:
        print("Telegram güncellemeleri alınamadı:", e)
        return []


def confirm_updates(max_update_id):
    if max_update_id is None:
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        return

    url = f"{TELEGRAM_API}{bot_token}/getUpdates"

    try:
        requests.get(
            url,
            params={
                "offset": max_update_id + 1,
                "limit": 1,
                "timeout": 0
            },
            timeout=10
        )

        print("Telegram güncellemeleri onaylandı.")

    except Exception as e:
        print("Telegram güncellemeleri onaylanamadı:", e)


def build_status_message(trades):
    open_trades = [
        trade
        for trade in trades
        if trade.get("status") == "OPEN"
    ]

    if not open_trades:
        return (
            "📊 SANAL İŞLEM DURUMU\n\n"
            "🟢 Açık işlem bulunmuyor."
        )

    lines = []

    lines.append("📊 SANAL İŞLEM DURUMU")
    lines.append("")
    lines.append(
        f"🟢 Açık işlem sayısı: {len(open_trades)}"
    )
    lines.append("")

    total_unrealized = 0.0

    for trade in open_trades:

        symbol = trade.get("symbol", "?")

        entry = float(trade.get("entry_price", 0))
        sl = float(trade.get("sl", 0))
        tp1 = float(trade.get("tp1", 0))
        tp2 = float(trade.get("tp2", 0))

        remaining_pct = float(
            trade.get("remaining_pct", 100)
        )

        realized_pnl = float(
            trade.get("realized_pnl_pct", 0)
        )

        score = int(
            trade.get("score", 0)
        )

        current_price = get_current_price(symbol)

        if current_price is not None and entry > 0:

            price_change_pct = (
                (current_price - entry)
                / entry
            ) * 100

            remaining_pnl = (
                price_change_pct
                * (remaining_pct / 100)
            )

            total_pnl = (
                realized_pnl
                + remaining_pnl
            )

            total_unrealized += total_pnl

            current_text = format_price(
                current_price
            )

            price_pnl_text = (
                f"{price_change_pct:+.2f}%"
            )

            total_pnl_text = (
                f"{total_pnl:+.2f}%"
            )

        else:

            current_text = "Alınamadı"
            price_pnl_text = "?"
            total_pnl_text = "?"

        tp1_hit = trade.get(
            "tp1_hit",
            False
        )

        tp1_status = (
            "✅ ALINDI"
            if tp1_hit
            else "❌ BEKLENİYOR"
        )

        lines.append(
            f"🟢 {symbol}"
        )

        lines.append(
            f"💵 Giriş: {format_price(entry)}"
        )

        lines.append(
            f"📍 Güncel: {current_text}"
        )

        lines.append(
            f"📈 Fiyat K/Z: {price_pnl_text}"
        )

        lines.append(
            f"💰 Toplam K/Z: {total_pnl_text}"
        )

        lines.append(
            f"🛑 SL: {format_price(sl)}"
        )

        lines.append(
            f"🎯 TP1: {format_price(tp1)}"
        )

        lines.append(
            f"🎯 TP2: {format_price(tp2)}"
        )

        lines.append(
            f"📌 TP1: {tp1_status}"
        )

        lines.append(
            f"📊 Skor: {score}/100"
        )

        lines.append(
            f"📦 Kalan: {remaining_pct:.0f}%"
        )

        lines.append("")
        lines.append("──────────────")
        lines.append("")

    lines.append(
        "ℹ️ Toplam K/Z, gerçekleşen "
        "ve kalan pozisyonun anlık K/Z'sini "
        "birlikte gösterir."
    )

    return "\n".join(lines)


def main():
