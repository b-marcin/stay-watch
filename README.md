# Stay Watch

Stay Watch checks one hotel's prices on a schedule, emails you when a stay matches a rule, and publishes a small dashboard. The hotel's name and booking link stay in GitHub secrets. They are not written into this repository.

## Edit what to watch

Open `watch.toml` on GitHub and use the pencil icon. Change the dates, the number of nights, or the price rules, then commit to the main branch. The next run uses the new file.

`must_include = ["sat"]` keeps stays that cover Saturday night (Friday to Sunday, and Saturday to Monday, when the stay is 2 nights). `top = 3` lists the three cheapest. `top = "all"` lists every stay. Add `offers` or `rooms` under a search to keep names that contain that text.

Alert keys, one per `[[search.alert]]` block: `below` (total PLN), `per_night_below`, `drop_pct`, `below_recent_lowest`, `discounted`.

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
