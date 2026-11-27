# Public data files

`latest.json` and `history.json` are what the dashboard reads. Money on a stay is an integer number of grosze (1 PLN = 100). `alert` amounts are whole PLN, the same as `docs/config.json`. Dates are `YYYY-MM-DD`. Timestamps are ISO 8601 with seconds and a numeric offset, for example `2026-11-09T10:00:00+01:00`. Names have already had the hotel terms removed.

`state.json` is written by the scanner. The dashboard does not read it.

`alerts_sent` is `{ "<search_id>": { "<stay_key>": <last emailed price in grosze> } }`. A stay is emailed again only when its new price is strictly lower than that remembered price by at least `again_cheaper_by`. An older file that still nests prices under rule ids is read by keeping the lowest price stored for each stay. `last_digest_date` is gone. There is no weekly digest. Tracked-trip state keeps `first_price` and `last_seen_price`.

## latest.json

Top-level object:

| Field | Type | Meaning |
| --- | --- | --- |
| `scanned_at` | string | When this scan started, in Europe/Warsaw. |
| `config_revision` | string or null | Git blob SHA of the exact config bytes used by this scan. The dashboard matches this against the saved config revision before completing a pending edit. Null for a scan constructed without a config file. Older snapshots can omit it. |
| `searches` | array of objects | One entry per search in `docs/config.json`, in file order. Empty before any search is configured. |
| `tracked` | array of objects | One entry per tracked trip whose check-in is today or later, in config order. Empty when none qualify. |

Each search:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Search id from the config. |
| `name` | string | The custom name, or the derived name when the config name is null. Derived form is `{d} {Mon} – {d} {Mon}`, with the year on both sides only when the window end is more than 365 days after the scan date. |
| `custom_name` | boolean | True when the config name is a string. False when the name was derived. |
| `window_start` | string | Earliest check-in, `YYYY-MM-DD`. |
| `window_end` | string | Latest check-out, `YYYY-MM-DD`. |
| `nights` | number | Nights in each stay. |
| `must_include` | array of strings | Weekday names the stay must cover: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`, in that order. Empty means every check-in in the window. |
| `adults` | number | Adults in the party. |
| `top` | number or null | How many cheapest stays to show. Null means all of them. |
| `codes` | array of strings | Promo codes sent with the request. Empty when there are none. |
| `history_key` | string | Key into `history.json` for this party, codes, and filters. Shape: `adults=<n>;codes=<a,b>;offers=<filters>;rooms=<filters>`. |
| `alert` | object or null | The price gate for this search, in whole PLN, or null when the search sends no email. See below. |
| `stays` | array of objects | One row per planned stay. Priced stays come first, cheapest first, then sold-out stays by check-in. Empty when the window has no stays. |

`alert` uses the same shape as `docs/config.json`, with defaults filled in. Amounts here are whole PLN, unlike the grosze fields on a stay. Null means this search never emails.

| Field | Type | Meaning |
| --- | --- | --- |
| `under` | number | Whole PLN. A stay matches only when its price is strictly under this. |
| `again_cheaper_by` | number | Whole PLN, 0 or more. How much cheaper a stay must be than the last emailed price before it emails again. |
| `require.recent_low` | boolean | When true, the quote's 30-day lowest must be present and the price must be strictly under it. |
| `require.discounted` | boolean | When true, the quote must include a pre-discount price. |
| `require.drop_pct` | number or null | Whole percent from 1 to 100, or null. When set, the price must have fallen by at least that percent since the previous scan. |

Each stay:

| Field | Type | Unit | Null means |
| --- | --- | --- | --- |
| `key` | string | | Never null. `{check_in}_{check_out}`. |
| `check_in` | string | date | Never null. |
| `check_out` | string | date | Never null. |
| `nights` | number | nights | Never null. |
| `sold_out` | boolean | | Never null. True when `price` is null. |
| `offer_count` | number | offers | Never null. Count of distinct offers that matched the filters. 0 when nothing matched. |
| `previous_price` | number or null | grosze | No earlier recorded price to compare with the current one. |
| `alert_match` | boolean | | Never null. True when the stay's price passes `alert`, ignoring `again_cheaper_by`. False when the search has no alert, the stay is sold out, or a require filter fails. |
| `price` | number or null | grosze | No matching quote (sold out or filtered out). |
| `per_night` | number or null | grosze | No quote. Otherwise `price` divided by nights, rounded down. |
| `recent_lowest` | number or null | grosze | The feed had no 30-day lowest, or there is no quote. |
| `original` | number or null | grosze | No pre-discount price was shown, or there is no quote. |
| `rooms_left` | number or null | rooms | No quote, or the quote did not say how many rooms were left. A number is how many rooms of this quote were left. |
| `offer_name` | string or null | | No quote. |
| `room_name` | string or null | | No quote. |
| `meal_plan` | string or null | | No quote. Otherwise `breakfast`, `half board`, or `other (code N)`. |
| `cancel` | string or null | | No quote. Otherwise a cancellation label such as `non-refundable` or `free until 3 days before`. |

Each tracked trip uses the same quote fields as a stay (`sold_out`, `offer_count`, `previous_price`, `price`, `per_night`, `recent_lowest`, `original`, `rooms_left`, `offer_name`, `room_name`, `meal_plan`, `cancel`), plus:

| Field | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `id` | string | | Trip id from the config. |
| `key` | string | | `{check_in}_{check_out}`. |
| `check_in` | string | date | |
| `check_out` | string | date | |
| `nights` | number | nights | |
| `adults` | number | | |
| `codes` | array of strings | | Promo codes. Empty when there are none. |
| `added` | string | date | The day the trip was tracked. |
| `history_key` | string | | Same shape as a search history key, with empty offer and room filters. |
| `first_price` | number or null | grosze | Price on the first scan after tracking. Null if that scan was sold out. |
| `lowest_seen` | number or null | grosze | Lowest non-null price in this trip's history, including the current scan. |

Tracked trips are shown on the dashboard only. They do not send email.

## history.json

Top-level object. Each key is a search `history_key`. Each value is an object whose keys are stay keys (`YYYY-MM-DD_YYYY-MM-DD`).

Each stay value is an array of points, oldest first. A point is a four-element array:

| Index | Type | Unit | Null means |
| --- | --- | --- | --- |
| 0 | string | timestamp | Never null. When this price was observed. |
| 1 | number or null | grosze | That stay had no matching quote at that time. |
| 2 | string or null | | Offer name. Null when the stay had no quote, or the offer had no name. |
| 3 | string or null | | Room name. Null when the stay had no quote, or the room had no name. |

A new point is appended only when the price (index 1) changes, including a change to or from null. Stays whose check-in is before today are removed. A group with no stays left is removed. The file is `{}` when nothing remains.

## config.json

`docs/config.json` is the watch list. The dashboard reads and writes it. The scanner reads the same file. Money in this file is whole PLN. Money in `latest.json` is grosze.

Top-level object, `version` 2. Version 1 is rejected. Unknown keys are ignored. A structurally invalid file is rejected and the data files are left untouched. A search that still has an `alerts` list is rejected.

| Field | Meaning |
| --- | --- |
| `defaults` | Optional `adults`, `nights`, `must_include`, `top`, `codes`, `offers`, and `rooms` used when a search omits them. |
| `searches` | Objects with required `id`, `from`, and `to`. `id` matches `[a-z0-9-]{1,40}` and is unique. `name` is a string or null. `to` is after `from`. `top` is a positive integer or null (every stay). `alert` is the object described above, or null. Amounts in this file are whole PLN. Absent `alert` means the search sends no email. |
| `tracked` | One object per trip. `id` is `{check_in}_{check_out}_a{adults}`, plus `_c` and the codes joined by `-` when codes are set. The scanner derives the id when it is omitted and rejects one that does not match. `added` is the day it was tracked. Tracked trips do not send email. |
