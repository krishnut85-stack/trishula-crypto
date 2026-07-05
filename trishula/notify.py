"""Best-effort Telegram notifications. Silent no-op if not configured.

Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in the environment/.env to receive paper
updates on your phone. Never raises — reporting must never break the engine.
"""
from __future__ import annotations

from .config import CONFIG


def send_telegram(text: str) -> bool:
    token, chat = CONFIG.telegram_token, CONFIG.telegram_chat_id
    if not token or not chat:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False
