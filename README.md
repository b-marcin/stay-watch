# Stay Watch

Stay Watch checks one hotel's prices on a schedule, emails you when a stay is priced under the limit you set, and publishes a small dashboard. The hotel's name and booking link stay in GitHub secrets. They are not written into this repository.

## Edit what to watch

Searches and tracked trips are edited on the dashboard. One-time setup is a fine-grained GitHub token with **Contents: read and write** on this repository only, pasted into the dashboard's settings.

Hand-editing `docs/config.json` is the fallback. Saving, from the dashboard or by committing that file on `main`, starts a scan within a minute. The dashboard shows the new results to you about 3 minutes after you save; the public page catches up a few minutes later. The scheduled scan still runs at minute 17 of every third hour.

A search name is optional. Leave it null and the scan names the search from its dates, for example `1 Dec – 8 Dec`.

`must_include` of `["sat"]` keeps stays that cover Saturday night (Friday to Sunday, and Saturday to Monday, when the stay is 2 nights). `top` of `3` lists the three cheapest. `top` of `null` lists every stay. Add `offers` or `rooms` to keep names that contain that text.

`docs/config.json` is version 2. A search can set `alert`. `under` is the whole-stay price in whole PLN, and a stay is emailed only when its price is strictly under that. `again_cheaper_by` (whole PLN, default 0) is how much cheaper that same stay must be before it is emailed again. `require.recent_low`, `require.discounted`, and `require.drop_pct` are optional extra filters. They never cause a stay at or above `under` to be emailed. Leave `alert` null, or omit it, and that search sends no mail. The scanner remembers the last emailed price in `alerts_sent` as `{search id: {stay key: price in grosze}}`.

A tracked trip is one stay on the dashboard. Tracked trips do not send email. There is no weekly digest.

## Secrets

In the repository settings, add these Actions secrets:

- `HOTEL_SLUG` — the hotel's id in the booking URL
- `HOTEL_NAME_TERMS` — comma-separated stems to strip from the public dashboard. Enter the shortest distinctive stem of each name word, not a full grammatical ending. The stem matches the rest of that word, in any case, and a multi-word name still matches when the words are split by an odd space. For Example Hotel in Exampletown, use `Example,Hotel,Examplet`
- `SMTP_USER` — the Gmail address that sends mail
- `SMTP_PASSWORD` — a Gmail app password
- `ALERT_TO` — optional; defaults to `SMTP_USER`

## Schedule and a one-off search

The scan workflow runs at minute 17 of every third hour. It can also be started by hand.

On a phone: open the repository, tap Actions, open Scan, tap Run workflow, leave mode on `scan`.

For a one-off search, choose mode `search` and fill in the dates. That run emails the result and does not change the dashboard.

## Dashboard

In Settings, then Pages, set the source to the main branch and the folder `/docs`. The page reads `docs/data` after the first successful scan.

## Run it on your machine

Python 3.12. No packages are required to scan. Tests need pytest (`python -m pip install pytest`).

Run the Python tests with `python -m pytest -q`. Dashboard tests need Node.js 24 and run with `node --test tests/*.test.cjs`. Both suites run in CI.

Windows Command Prompt:

```
set HOTEL_SLUG=your-slug
set HOTEL_NAME_TERMS=Example,Hotel,Examplet
set SMTP_USER=you@gmail.com
set SMTP_PASSWORD=app-password
set DRY_RUN=1
python -m stay_watch search --from 2026-12-04 --to 2026-12-08 --email
```

PowerShell:

```
$env:HOTEL_SLUG = "your-slug"
$env:HOTEL_NAME_TERMS = "Example,Hotel,Examplet"
$env:DRY_RUN = "1"
python -m stay_watch search --from 2026-12-04 --to 2026-12-08
```

`DRY_RUN=1` prints the email instead of sending it. `--fixtures tests/fixtures` reads the saved sample responses instead of the network.

## If the schedule stops

GitHub turns off scheduled workflows after 60 days without a commit or another repository event. Open the Actions tab and enable the workflow again. A commit, or running the workflow by hand, starts the 60 days over.
