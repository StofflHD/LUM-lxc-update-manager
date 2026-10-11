"""Translations, shared with the web UI: app/static/lang/<code>.json.

One flat JSON object per language: "key": "text with {placeholders}". English (en.json)
is the reference and the fallback for missing keys. Plural forms are "key_one" /
"key_other" (and further CLDR forms like "key_few" in the browser), picked by the
"count" parameter. "_name" is the language's own name for the menu, "_locale" the
locale for dates.

Server side the language comes from the request (cookie "lum_lang", set by the web UI,
or Accept-Language) for error messages, and from LUM_NOTIFY_LANGUAGE for Telegram.
"""

import contextvars
import json
import re
from functools import lru_cache
from pathlib import Path

LANG_DIR = Path(__file__).parent / "static" / "lang"
DEFAULT = "en"
_CODE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")

current: contextvars.ContextVar[str] = contextvars.ContextVar("lum_lang", default=DEFAULT)


@lru_cache
def _strings(lang: str) -> dict[str, str]:
    path = LANG_DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache
def languages() -> dict[str, str]:
    """{"en": "English", "de": "Deutsch", ...} - every lang/*.json, English first."""
    codes = sorted((p.stem for p in LANG_DIR.glob("*.json") if _CODE.match(p.stem)),
                   key=lambda c: (c != DEFAULT, c))
    return {c: _strings(c).get("_name", c) for c in codes}


def match(wanted: str | None) -> str:
    """Best available language for "de-AT,de;q=0.9,en" or "de" (else English)."""
    available = languages()
    for part in (wanted or "").split(","):
        code = part.split(";")[0].strip()
        if not code:
            continue
        for candidate in (code, code.split("-")[0].lower()):
            if candidate in available:
                return candidate
    return DEFAULT


def merged(lang: str) -> dict[str, str]:
    """All strings of a language, English where it has none (for the web UI)."""
    return {**_strings(DEFAULT), **_strings(lang)} if lang != DEFAULT else dict(_strings(DEFAULT))


def t(key: str, lang: str | None = None, **params) -> str:
    """Translated text; lang None = the language of the current request."""
    lang = lang or current.get()
    strings, fallback = _strings(lang), _strings(DEFAULT)
    text = None
    if isinstance(params.get("count"), int):
        form = "one" if params["count"] == 1 else "other"  # enough for the server's languages
        for k in (f"{key}_{form}", f"{key}_other"):
            text = text or strings.get(k) or fallback.get(k)
    text = text or strings.get(key) or fallback.get(key) or key
    return re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


class Translated:
    """Mixin for exceptions whose message is translated when it is shown: str() in a
    request gives the user's language, in the log (no request) English."""

    def __init__(self, key: str, **params):
        self.key, self.params = key, params
        super().__init__(key)

    def __str__(self) -> str:
        return t(self.key, **self.params)


class LumError(Translated, RuntimeError):
    pass


class LumValueError(Translated, ValueError):
    pass
