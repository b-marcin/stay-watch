# Public data files

`latest.json` and `history.json` are what the dashboard reads. Money is an integer number of grosze (1 PLN = 100). Dates are `YYYY-MM-DD`. Timestamps are ISO 8601 with seconds and a numeric offset, for example `2026-11-09T10:00:00+01:00`. Names have already had the hotel terms removed.

`state.json` is not part of this contract.

## latest.json

Top-level object:

| Field | Type | Meaning |
| --- | --- | --- |
| `scanned_at` | string | When this scan finished, in Europe/Warsaw. |
| `searches` | array of objects | One entry per search in `watch.toml`, in file order. Empty before any search is configured. |

Each search:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Search id from the config. |
| `name` | string | Search name from the config. |
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
| `rooms_left` | number or null | rooms | No quote. A number is how many rooms of this quote were left. |
| `offer_name` | string or null | | No quote. |
| `room_name` | string or null | | No quote. |
| `meal_plan` | string or null | | No quote. Otherwise `breakfast`, `half board`, or `other (code N)`. |
| `cancel` | string or null | | No quote. Otherwise a cancellation label such as `non-refundable` or `free until 3 days before`. |

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
