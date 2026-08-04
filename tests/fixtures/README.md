# Scrape fixtures

Verbatim captures of the two pages `rs_tracker.py` scrapes, used by `tests/test_parsers.py`.
Captured 2026-08-04.

| file | source |
|---|---|
| `slu.html` | <https://oldschool.runescape.com/slu> |
| `index.html` | <https://oldschool.runescape.com/> |

## What these do and don't catch

They catch **us** breaking the parsers — a refactor that drops a column, mishandles the
full-world case, or changes a returned key.

They do **not** catch **Jagex** changing their markup, because they're a frozen copy. That's what
the live check is for: `RUN_LIVE_SCRAPE_TESTS=1 python -m unittest tests.test_parsers` hits the real
pages and asserts the parsers still find plausible data.

CI runs it as its own `live-scrape` job on every push and PR, plus a weekly cron for quiet stretches.
The env-var gate exists so the *local* run stays offline, fast and deterministic — not to keep it
out of CI.

## Refreshing

```sh
curl -A "$(python -c 'import config; print(config.USER_AGENT)')" \
    -o tests/fixtures/slu.html   https://oldschool.runescape.com/slu
curl -A "$(python -c 'import config; print(config.USER_AGENT)')" \
    -o tests/fixtures/index.html https://oldschool.runescape.com/
```

Then re-run the tests and update the expected counts at the top of `test_parsers.py`. A refresh
that makes the tests fail *is the signal* — read the diff before fixing the assertions.
