"""Check the language files: python -m app.i18n_check

- every key used in the code (t("..."), data-i18n="...") exists in en.json;
- every other language has the keys of en.json (missing ones show in English) and no
  unknown ones, with the same {placeholders}.

Plural keys ("x_one", "x_other", ...) count as one: a language may use other forms
(e.g. "x_few" in Polish), but needs at least "x_other".
Exit code 1 when something is wrong.
"""

import re
import sys
from pathlib import Path

from .i18n import DEFAULT, LANG_DIR, _strings, languages

APP = Path(__file__).parent
PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")
USED = re.compile(r"""(?:\bt|\bte|tOr|I18N\.has)\(\s*["']([a-z_]+\.[A-Za-z0-9_.]+)["']|data-i18n(?:-\w+)?="([a-z_]+\.[a-z0-9_.]+)\"""")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def base(key: str) -> str:
    return PLURAL.sub("", key)


def placeholders(strings: dict, key: str) -> set[str]:
    return {p for k, v in strings.items() if base(k) == key for p in PLACEHOLDER.findall(v)}


def main() -> int:
    problems = 0
    en = _strings(DEFAULT)
    en_keys = {base(k) for k in en if not k.startswith("_")}

    used = set()
    for path in [*APP.glob("*.py"), *(APP / "static").glob("*.js"), *(APP / "static").glob("*.html")]:
        for m in USED.finditer(path.read_text(encoding="utf-8")):
            used.add(m.group(1) or m.group(2))
    for key in sorted(used - en_keys):
        print(f"en: used in the code but missing: {key}")
        problems += 1

    for lang in languages():
        if lang == DEFAULT:
            continue
        strings = _strings(lang)
        keys = {base(k) for k in strings if not k.startswith("_")}
        for key in sorted(en_keys - keys):
            print(f"{lang}: missing (shown in English): {key}")
            problems += 1
        for key in sorted(keys - en_keys):
            print(f"{lang}: unknown key: {key}")
            problems += 1
        for key in sorted(keys & en_keys):
            if any(PLURAL.search(k) for k in en if base(k) == key) and f"{key}_other" not in strings:
                print(f"{lang}: plural key without \"_other\": {key}")
                problems += 1
            if placeholders(strings, key) - placeholders(en, key):
                print(f"{lang}: {key}: placeholders {sorted(placeholders(strings, key))}, "
                      f"English has {sorted(placeholders(en, key))}")
                problems += 1
        for meta in ("_name", "_locale"):
            if meta not in strings:
                print(f"{lang}: {meta} is missing")
                problems += 1
    print(f"{len(languages())} languages in {LANG_DIR}, {len(en_keys)} texts: "
          + ("OK" if not problems else f"{problems} problem(s)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
