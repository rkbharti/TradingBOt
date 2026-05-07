import requests

from config.settings import (
    ENABLE_TELEGRAM,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


def send_telegram(message: str, silent: bool = False):
    if not ENABLE_TELEGRAM:
        return None

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram config missing")
        return None

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_notification": silent,
            },
            timeout=10,
        )

        if response.status_code != 200:
            print(f"❌ Telegram HTTP {response.status_code}: {response.text}")
            return None

        print("✅ Telegram sent")
        return response.json()

    except Exception as e:
        print(f"❌ Telegram exception: {e}")
        return None