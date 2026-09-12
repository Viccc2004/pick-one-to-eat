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

## Product redefinition — 2026-09-11

M0 and M1 were built against a CLI spec. The product shape changed this
day; the entries below supersede the interface parts of that spec. The
M1 schema and function split survive unchanged.

### Web page replaces the CLI
The interface is a FastAPI app serving one server-rendered HTML page,
not a command-line tool. README's v1 scope and the BACKLOG entry that
deferred Web UI to Phase 3 are both superseded and need rewriting.

### Rank three internally, show one
The LLM ranks a top-3; the page shows rank 1 only and holds ranks 2 and
3 for a "show me another" action. Keeps the product's opinionated
single answer while reusing candidates already paid for.

### Pseudo-multi-turn, stateless server
The page resends prior turns with each request; the server keeps no
session state. Gives the conversational feel of changing a condition
and recomputing without introducing session storage, expiry, or a state
machine into v1.

### Reasoning may only cite supported conditions
The reason sentence may reference only conditions the data actually
supports. Where the data is silent, the output says so and tells the
user to verify — it never guesses.

Consequences: no wait-time or queue-length feature, because Places
carries no such data and the honest answer is to tell the user to call
the restaurant. The same rule settles dish-level claims — the app does
not assert that a place serves a specific dish beyond what Google's own
text matching supports. This generalizes an explicit decision about
queue length; if the generalization is wrong, this entry is the one to
correct.

It also matches the M1 rule that `open_now == None` must be stated in
the output rather than assumed.

### Google Places over Yelp
Google's free allowance renews monthly and indefinitely; Yelp's is a
one-time 30-day trial of 5,000 calls, evaluation-use only. A trial that
expires mid-project is disqualifying for a project running months at
~10 hours a week. Verified against both vendors' docs 2026-09-11.

### The M1 schema costs the Enterprise SKU
`rating`, `userRatingCount`, `priceLevel`, and `currentOpeningHours` are
all Enterprise-tier fields, and Google bills each call at the highest
tier in its field mask. The M1 schema therefore moves every call from
Pro (5,000 free/month) to Enterprise (1,000 free/month) — an 80% cut in
free allowance that was invisible when the schema was designed on
structural grounds.

Schema stands: 1,000 calls/month is comfortable at this volume. But a
dev cache is now load-bearing rather than hygiene, and adding `reviews`
later means Enterprise + Atmosphere, which is a deliberate decision.

### Dev cache: 24-hour TTL, hand-written fixtures
Google's terms forbid caching Places content except `place_id`
(indefinite) and lat/lng (30 days). A 24-hour TTL sits well inside that
window, and test fixtures are hand-written rather than recorded
responses. The M1 fixture decision was made for testability; it is also
the compliant choice.

### LLM structured output over regex for constraint extraction
The sentence goes to Google largely as written; an LLM call extracts
only the constraints applied after results return. Regex was rejected
because its failure mode is silent — an unmatched phrasing produces a
wrong filter with no signal — while malformed LLM output is detectable
and can be handled.

### Docs move to docs/
`DECISIONS.md` moves to `docs/DECISIONS.md` and `docs/PROJECT.md` is
added, per CLAUDE.md §8. `README.md` and `BACKLOG.md` stay at the root.
