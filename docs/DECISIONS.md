# Decisions

One entry per decision, grouped by milestone, oldest first.

## M0 — 2026-09-01

### Text Search over Nearby Search
Text Search accepts natural language with cuisine and place name
in one string. Nearby Search requires lat/lng plus a fixed type,
which would need an extra geocoding step before M2 is even started.

### Rerank layer confirmed necessary
M0 returned real Chinese restaurants in Allston, but name and
address alone cannot tell whether a place serves a specific dish.
Confirmed that the API cannot index menus.

## M1 — 2026-09-07

### Split find.py at the network boundary
find.py is split into two functions:

- `search_places(query: str) -> list[dict]` — raw Google response items
- `parse_places(raw: list[dict]) -> list[dict]` — normalized dicts

The line is drawn at the network boundary: `search_places` touches
the network, `parse_places` is pure. This lets `parse_places` be
tested against hand-made fixtures, including ones with missing
fields, without spending API calls or waiting for Google to happen
to return an incomplete response.

### Field-name translation stays inside parse_places
Translation from Google's shape to my shape lives inside `parse_places`
for now, so `parse_places` is Google-specific. A separate
`normalize_google()` adapter layer was considered and rejected for M1:
with only one data source, any "generic" format invented now would
likely be wrong. When a second source (e.g. Yelp) is actually added,
`parse_places` will be split into a per-source normalizer plus a shared
cleaning step, designed against two real schemas instead of one guess.

### Internal schema
Field names are snake_case and deliberately mine, not Google's.

| field          | type            | source field                  | notes                    |
| -------------- | --------------- | ----------------------------- | ------------------------ |
| `name`         | `str`           | `displayName.text`            |                          |
| `rating`       | `float \| None` | `rating`                      | read with `rating_count` |
| `rating_count` | `int \| None`   | `userRatingCount`             | read with `rating`       |
| `price_level`  | `str \| None`   | `priceLevel`                  | raw Google enum          |
| `open_now`     | `bool \| None`  | `currentOpeningHours.openNow` |                          |
| `place_type`   | `str \| None`   | `primaryType`                 |                          |

`price_level` keeps Google's raw enum (e.g. `"PRICE_LEVEL_MODERATE"`)
and is not translated in M1 — formatting is a presentation concern.

### Dropped: formattedAddress
An address string does not let the M3 reranker reason about distance;
proximity is already constrained at query time via the Places API
location bias. Reconsidering address (or lat/lng) is in BACKLOG.md,
in case the output ever needs to tell the user how to get there.

### Missing-field handling
- `name` — missing means drop the whole candidate. A result with no name
  cannot be recommended or displayed.
- `rating` — keep as `None`. Do not coerce to 0: an unrated new
  restaurant and a 0-rated one must stay distinguishable to M3.
  M3 skips rating-based filtering when `None`.
- `rating_count` — keep as `None`. Same reasoning as `rating`.
- `price_level` — keep as `None`. Missing often for small independent
  places; dropping would drain the candidate pool.
  M3 skips price filtering when `None`.
- `open_now` — keep as `None`. A candidate with unknown hours is still
  recommendable, but M3 must state in its output that opening hours
  are unknown and advise calling ahead.
- `place_type` — keep as `None`.

`rating` and `rating_count` must always be evaluated together —
4.8 with 3 reviews and 4.8 with 2000 reviews are not comparable.
M3 must not filter on rating alone.

### Exit condition
Running find.py prints N restaurants, one per line,
using the fields above, without crashing on incomplete API responses.
