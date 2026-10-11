# Translating LUM

[← Back to the README](../README.md)

LUM's web UI, its error messages and the Telegram notifications are translated with one
JSON file per language in [`app/static/lang/`](../app/static/lang):

| File | Language |
|---|---|
| `en.json` | English – the reference; every text exists here |
| `de.json` | German |

A new file there is a new language: it shows up in menu **☰** → *Language* and as
*Language of the messages* in the Telegram settings – nothing else needs to be changed.

## Adding a language

1. Copy `en.json` to `<code>.json`, with the language code as the name: `fr.json`,
   `nl.json`, `pt-BR.json` (lower-case language, optionally `-` and the upper-case
   country).
2. Set the two entries at the top:
   - `"_name"` – the language's own name for the menu, e.g. `"Français"`;
   - `"_locale"` – the locale for dates and numbers, e.g. `"fr-FR"`.
3. Translate the values (right side), never the keys (left side).
4. Check it (see below), try it in the demo, open a pull request.

A missing key is not an error at runtime: that text is shown in English. So a language
can also start half translated.

## Rules for the texts

- **Placeholders** in braces stay as they are, in any position the sentence needs:
  `"Last check: {time}"` → `"Dernière vérification : {time}"`.
- **Plural forms:** keys ending in `_one` / `_other` are one text in singular and plural,
  chosen by the number `{count}`:

  ```json
  "pkg.packages_one": "{count} package",
  "pkg.packages_other": "{count} packages",
  ```

  Languages with more forms add them – `_zero`, `_two`, `_few`, `_many` as defined by
  [CLDR](https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html)
  (e.g. Polish `_one`, `_few`, `_many`, `_other`). `_other` is always needed. The web UI
  picks the form with the browser's `Intl.PluralRules`; the server (error messages,
  Telegram) only knows `_one` and `_other`.
- **`\n`** is a line break (in dialogs and Telegram messages).
- **HTML** is only allowed where the English text has it (`settings.note`, `tg.*` with
  `<b>`); everything else is shown as plain text.
- Leave technical terms that the user types or sees on the host as they are: `apt`,
  `vzdump`, `lum_*`, `no-lum`, `no-app`, `LUM_…` options, commands.
- Key groups: `set.*` / `setgroup.*` / `setph.*` are the settings dialog (labels, groups,
  placeholders), `err.*` error messages from the server, `skip.*` reasons in the queue,
  `tg.*` Telegram messages, `login.*` the login page.

## What is not translated

- The update logs and the server log stay English – the output of `apt` and the
  community scripts is English anyway, and logs should read the same for everyone when
  asking for help.
- Messages from the Proxmox host and the community scripts (e.g. *"The app offers a
  built-in updater"*).
- The documentation.

## Checking

```bash
.venv/bin/python -m app.i18n_check
```

It reports keys used in the code but missing in `en.json`, keys missing in a language
(shown in English) or unknown there, plural keys without `_other`, and placeholders that
don't exist in English. LUM reads the files when it starts – restart it (or the demo)
to see changes.
