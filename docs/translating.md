# Translating LUM

[← Back to the README](../README.md)

LUM's web UI, its error messages and the Telegram notifications are translated with one
JSON file per language in [`app/static/lang/`](../app/static/lang):

| File | Language |
|---|---|
| `en.json` | English – the reference; every text exists here |
| `de.json` | German |

A new file there is a new language: it shows up in ☰ → **Settings** → *Language* and as
*Language of the messages* in the Telegram settings – nothing else needs to be changed.

## Adding a language

1. Copy `en.json` to `<code>.json`, with the language code as the name: `fr.json`,
   `nl.json`, `pt-BR.json` (lower-case language, optionally `-` and the upper-case
   country).
2. Set the two entries at the top:
   - `"_name"` – the language's own name for the drop-down, e.g. `"Français"`;
   - `"_locale"` – the locale of the language, e.g. `"fr-FR"` (dates and numbers follow
     the user's *Date and time format* setting, by default the system).
3. Translate the values (right side), never the keys (left side). Save the file as UTF-8.
4. Check it and try it (see below).

A missing key is not an error at runtime: that text is shown in English. So a language
can also start half translated.

## Contributing a translation to LUM

New languages and fixes to existing ones are welcome.

**With git and GitHub** (the usual way):

1. Fork [StofflHD/LUM-lxc-update-manager](https://github.com/StofflHD/LUM-lxc-update-manager)
   on GitHub and clone your fork.
2. Create a branch, e.g. `git checkout -b lang-fr`.
3. Add or change `app/static/lang/<code>.json` as described above – only that file;
   the language shows up in LUM by itself.
4. Run the check and look at it in the demo (see [Development](development.md) for the
   setup):

   ```bash
   .venv/bin/python -m app.i18n_check
   LUM_DEMO=true .venv/bin/uvicorn app.main:app --port 8080
   ```

   Open <http://localhost:8080> (login `admin` / `demo`), choose the language in ☰ →
   **Settings** → *Language*, go through the list, the dialogs (update, backups,
   settings, clear history, export / import) and the login page. Longer words can
   break buttons on a phone – try a narrow window too.
5. Commit, push, and open a pull request against `main`. Say in it which language it is
   and whether everything is translated.

**Without git:** open an [issue](https://github.com/StofflHD/LUM-lxc-update-manager/issues)
and attach the translated `<code>.json` (zipped, if GitHub refuses the file type).

## Keeping a translation up to date

New LUM versions add texts – only to `en.json` at first; they appear in English in the
other languages until someone translates them. `python -m app.i18n_check` lists them
as *missing (shown in English)*: translate those keys and open a pull request as above.
The [Changelog](../CHANGELOG.md) says which features are new.

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
