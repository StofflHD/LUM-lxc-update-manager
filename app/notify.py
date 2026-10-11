"""Notifications through a Telegram bot.

Create a bot with @BotFather (gives the token), send it a message, and read the chat id
from https://api.telegram.org/bot<token>/getUpdates (a group: add the bot, the id is
negative). LUM only sends; it never reads messages.
"""

import asyncio
import html
import json
import logging
import urllib.error
import urllib.request

from .config import Settings
from .i18n import LumError, t

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_LINES = 15  # per message: Telegram allows 4096 characters


def esc(text) -> str:
    return html.escape(str(text), quote=False)


class Notifier:
    def __init__(self, settings: Settings, api: str = API):
        self._s = settings
        self._api = api

    @property
    def lang(self) -> str:
        """Language of the messages (LUM_NOTIFY_LANGUAGE)."""
        return self._s.notify_language

    @property
    def enabled(self) -> bool:
        return bool(self._s.telegram_token and self._s.telegram_chat_id) and self._s.notify != "off"

    def wants(self, failed: bool) -> bool:
        """Per update: "all" always, "failures" only when it failed."""
        return self.enabled and (self._s.notify == "all" or failed)

    def _post(self, text: str) -> None:
        body = json.dumps({"chat_id": self._s.telegram_chat_id, "text": text, "parse_mode": "HTML",
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"{self._api}/bot{self._s.telegram_token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                res.read()
        except urllib.error.HTTPError as err:  # Telegram explains in the body, e.g. "chat not found"
            try:
                detail = json.loads(err.read()).get("description", "")
            except ValueError:
                detail = ""
            raise LumError("err.tg_http", code=err.code, msg=detail or err.reason) from None
        except (urllib.error.URLError, TimeoutError) as err:
            raise LumError("err.tg_unreachable", msg=getattr(err, "reason", err)) from None

    async def send(self, text: str, test: bool = False) -> None:
        """Send one message; errors are logged (and raised for a test message)."""
        if not (self._s.telegram_token and self._s.telegram_chat_id):
            if test:
                raise LumError("err.tg_not_set")
            return
        try:
            await asyncio.to_thread(self._post, text)
        except RuntimeError as err:
            log.warning("notification not sent: %s", err)
            if test:
                raise


def lines(items: list[str], lang: str = "en") -> str:
    """Bullet list, cut to MAX_LINES."""
    shown = [f"• {i}" for i in items[:MAX_LINES]]
    if len(items) > MAX_LINES:
        shown.append(t("tg.more", lang, count=len(items) - MAX_LINES))
    return "\n".join(shown)
