"""Central configuration for Trishula.

Safety-first. PAPER_MODE is HARD-ON by default:

  * Live trading requires BOTH ``PAPER_MODE=false`` AND
    ``LIVE_TRADING_AUTHORIZED=true`` set explicitly in the environment.
  * With either unset/default, ``CONFIG.paper_mode`` is True and no live
    execution path may run (see ``assert_live_authorized``).
  * No live-order module ships by default, so even with both switches on
    there is nothing to place a real order until one is deliberately wired.

Credentials come from the environment / .env only. Never hardcode secrets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # optional: load a local .env when present
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # Environment / endpoints. Use India endpoints, never the global ones.
    delta_env: str = os.getenv("DELTA_ENV", "testnet")  # testnet | india_live
    delta_base_url: str = os.getenv(
        "DELTA_BASE_URL", "https://api.india.delta.exchange"
    )
    delta_ws_url: str = os.getenv(
        "DELTA_WS_URL", "wss://socket.india.delta.exchange"
    )

    # Credentials (empty by default; supply via .env on the droplet).
    delta_api_key: str = os.getenv("DELTA_API_KEY", "")
    delta_api_secret: str = os.getenv("DELTA_API_SECRET", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Risk / sizing knobs (placeholders; tune once strategy is defined).
    max_pct_per_position: float = float(os.getenv("MAX_PCT_PER_POSITION", "0.10"))

    @property
    def live_enabled(self) -> bool:
        """True only when BOTH explicit live switches are set."""
        return (not _flag("PAPER_MODE", True)) and _flag(
            "LIVE_TRADING_AUTHORIZED", False
        )

    @property
    def paper_mode(self) -> bool:
        return not self.live_enabled


CONFIG = Config()


def assert_live_authorized() -> None:
    """Guard any code path that would touch real money.

    Every future live-execution call site MUST call this first. It raises
    unless the operator has explicitly authorized live trading.
    """
    if not CONFIG.live_enabled:
        raise RuntimeError(
            "PAPER_MODE_HARD: live trading is not authorized. "
            "Set PAPER_MODE=false and LIVE_TRADING_AUTHORIZED=true to enable."
        )
