# Public data files

`latest.json` and `history.json` are what the dashboard reads. Money is an integer number of grosze (1 PLN = 100). Dates are `YYYY-MM-DD`. Timestamps are ISO 8601 with seconds and a numeric offset, for example `2026-11-09T10:00:00+01:00`. Names have already had the hotel terms removed.

`state.json` is not part of this contract.

## latest.json

Top-level object:

| Field | Type | Meaning |
| --- | --- | --- |
| `scanned_at` | string | When this scan finished, in Europe/Warsaw. |
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
| `top` | number or null | How many cheapest stays the digest lists. Null means all of them. |
| `codes` | array of strings | Promo codes sent with the request. Empty when there are none. |
| `history_key` | string | Key into `history.json` for this party, codes, and filters. Shape: `adults=<n>;codes=<a,b>;offers=<filters>;rooms=<filters>`. |
| `alerts` | array of objects | Rules for this search. See below. |
| `stays` | array of objects | One row per planned stay. Priced stays come first, cheapest first, then sold-out stays by check-in. Empty when the window has no stays. |

Each alert object has `kind`:

| `kind` | Other fields | Units |
| --- | --- | --- |
| `below` | `amount` number | Total stay price, grosze. The rule matches when the price is strictly under this. |
| `per_night_below` | `amount` number | Per-night price, grosze. |
| `drop_pct` | `pct` number | Whole percent from 1 to 100. |
| `below_recent_lowest` | none | Cheaper than the hotel's own 30-day lowest. |
| `discounted` | none | The quote had an original price. |

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
| `matched_rules` | array of strings | | Never null. May be empty. Ids such as `below:130000`, `per_night_below:65000`, `drop_pct:10`, `below_recent_lowest`, `discounted`. Amounts in the id are grosze. |
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
| `notify` | string | | `any` or `drop`. |
| `below` | number or null | grosze | Optional threshold. Null when unset. |
| `added` | string | date | The day the trip was tracked. |
| `history_key` | string | | Same shape as a search history key, with empty offer and room filters. |
| `first_price` | number or null | grosze | Price on the first scan after tracking. Null if that scan was sold out. |
| `lowest_seen` | number or null | grosze | Lowest non-null price in this trip's history, including the current scan. |
| `last_notified_price` | number or null | grosze | Price in the last email about this trip. Null before that email. |

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

Top-level object, `version` 1. Unknown keys are ignored. A structurally invalid file is rejected and the data files are left untouched.

| Field | Meaning |
| --- | --- |
| `defaults` | Optional `adults`, `nights`, `must_include`, `top`, `codes`, `offers`, and `rooms` used when a search omits them. |
| `digest.weekday` | `mon` through `sun`, or `off`. Omitted means no digest. |
| `searches` | Objects with required `id`, `from`, and `to`. `id` matches `[a-z0-9-]{1,40}` and is unique. `name` is a string or null. `to` is after `from`. `top` is a positive integer or null (every stay). `alerts` is a list of `{kind, ...}` objects. Kinds match the alert table above, with PLN amounts instead of grosze. |
| `tracked` | One object per trip. `id` is `{check_in}_{check_out}_a{adults}`, plus `_c` and the codes joined by `-` when codes are set. The scanner derives the id when it is omitted and rejects one that does not match. `notify` is `any` or `drop`. `below` is whole PLN or null. `added` is the day it was tracked. |
