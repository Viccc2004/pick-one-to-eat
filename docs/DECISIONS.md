# Decisions

One entry per decision, grouped by milestone, oldest first. This is a log:
earlier entries are left as written and are corrected by later ones, never
edited in place. Where an entry has been superseded it says so and names the
entry that replaced it.

Two standing reads for anything in the M0/M1 sections:

- Those entries were written against a CLI product and a different milestone
  numbering. Where they say **M3**, they mean the ranking and reasoning layer,
  which under the current plan is M2 (rule-based stub) and M4 (LLM). M3 is now
  the live-data milestone.
- The interface parts of that spec are superseded by
  § Product redefinition — 2026-09-11.

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

### Exit condition — superseded 2026-09-11
Running find.py prints N restaurants, one per line,
using the fields above, without crashing on incomplete API responses.

This was a CLI exit condition. find.py no longer has a `__main__`; the
equivalent guarantee is now M2's exit condition — the web page renders a
restaurant without crashing on incomplete input — and is covered by
`tests/test_find.py`.

## Product redefinition — 2026-09-11

M0 and M1 were built against a CLI spec. The product shape changed this
day; the entries below supersede the interface parts of that spec. The
M1 schema and function split survive unchanged.

### Web page replaces the CLI
The interface is a FastAPI app serving one server-rendered HTML page,
not a command-line tool. README's v1 scope and the BACKLOG entry that
deferred Web UI to Phase 3 were both superseded; both were rewritten in
commit `c97bfb8`.

### Rank three internally, show one
The LLM ranks a top-3; the page shows rank 1 only and holds ranks 2 and
3 for a "show me another" action. Keeps the product's opinionated
single answer while reusing candidates already paid for.

### Pseudo-multi-turn, stateless server — partly superseded 2026-09-15
The page resends prior turns with each request; the server keeps no
session state. Gives the conversational feel of changing a condition
and recomputing without introducing session storage, expiry, or a state
machine into v1.

The stateless-server half stands and is implemented. The resending-prior-turns
half was cut — see § Single turn, not pseudo-multi-turn (2026-09-15).

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
added. `README.md` and `BACKLOG.md` stay at the root. This layout rule, and
the other standing rules once cited as "CLAUDE.md §4/§5/§7/§8", now live in
PROJECT.md § 0 — that file is not in the repository and the citations were
dangling.

## M2 — 2026-09-11

Implemented in commit `71592fe`, together with the M1 design that had never
been written as code.

### search_places reads fixtures at M2
`search_places` loads `fixtures/places_sample.json` instead of calling
Google. The function signature and return shape are already the real
ones, so M3 replaces the body and nothing downstream changes. This is
what lets the entire pipeline — parsing, ranking, reason generation,
rendering — be built and tested for $0, before an API key exists.

Ten hand-written fixtures, seven complete and three deliberately
incomplete: one missing `rating`/`userRatingCount`, one missing
`priceLevel`, one missing `currentOpeningHours`, and one missing
`displayName` entirely to exercise the drop path.

### Ranking rule at M2: trusted ratings first, original order behind
`rank_places` is a fixed rule, not an LLM — the LLM arrives at M4. It
splits candidates into trusted and untrusted, sorts the trusted ones by
rating, and leaves the untrusted ones in their original order behind
them.

The point is the second half. An unrated restaurant is unknown, not bad.
Sorting it by a coerced 0 would rank it below a genuinely 1-star place,
which is a claim the data does not support. Keeping original order makes
no claim at all, which is the honest position and matches the M1 rule
that missing values are not coerced.

### MIN_TRUSTED_RATING_COUNT = 50
A rating is used for ranking only when `rating_count >= 50`. This puts a
number on the M1 rule that `rating` and `rating_count` must be evaluated
together: 4.8 from 3 reviews is not comparable to 4.8 from 2,000.

50 is a judgement call, not a derived value, and it is the kind of
threshold M5's eval exists to test. It is a named module-level constant
in `recommend.py` precisely so the eval can move it and measure the
difference.

### Stateless via URL parameters
The query and the index of the candidate being shown both live in the
URL (`/?q=...&rank=1`). "Show me another" is an ordinary link, not a
POST, not a session. The server stores nothing, the browser's back
button works, and a result is a shareable link for free.

`rank` is clamped to the candidate list rather than trusted, so a
hand-edited URL cannot produce an IndexError.

### Reason clauses are guarded by the field they cite
`build_reason` builds a clause list where every clause is gated on the
field it depends on, and a `None` field contributes nothing. When no
field is available the output says so outright. `open_now is None` adds
an explicit "opening hours are unknown — call ahead."

This is the M0/M1 rule "reasoning may only cite supported conditions"
implemented structurally rather than by prompt instruction — at M2 there
is no prompt. M4 has to reproduce this guarantee with an LLM, which is
harder, and that is why M4 is its own milestone.

## Scope alignment — 2026-09-15

A pass over all four markdown files to remove conflicts between them and
against the code.

### Single turn, not pseudo-multi-turn
v1 sends one sentence and gets one answer, plus "show me another." The
page does not carry prior turns.

"The client resends prior turns" appeared in README, PROJECT.md, and the
2026-09-11 decision above, but was never assigned to a milestone and is
not in the M2 code. It would have landed on M4, which is already the
riskiest milestone and already owns the open question the project exists
to answer. Cut to [../BACKLOG.md](../BACKLOG.md) rather than left as
scope nobody scheduled.

The stateless-server property that entry was protecting is unaffected —
it is delivered by URL parameters instead.

### Location comes from browser geolocation
Closes the open question that had been "blocks M3." The page fills a
hidden lat/lng field with `navigator.geolocation` and passes it to
Places as location bias.

Chosen over a fixed `.env` home location because a fixed location makes
the recommendation wrong for the user standing somewhere else, and over
LLM extraction from the sentence because that would make M3 depend on
M4's LLM call.

Cost: this is the first JavaScript in the project. It stays compatible
with the no-framework rule — a handful of vanilla lines calling a
browser API is not a framework and needs no build step — but it must be
built with two failure paths in mind. The API needs a secure context,
which `127.0.0.1` satisfies, so localhost is fine. Permission can be
denied or the call can time out, so a fallback location is required, not
optional. Which fallback is still open; see PROJECT.md § 5.

### Standing rules moved into PROJECT.md § 0
Four places cited "CLAUDE.md §4/§5/§7/§8" — including the Definition of
Done every milestone is measured against — for a file that is not in the
repository. The cited rules are now stated in PROJECT.md § 0, which is
the authority going forward. No rule was invented: each was already
written out alongside the citation that referenced it.

## M3.1 — 2026-09-15

### Geolocation fallback is "Allston, Boston", hardcoded
When the browser denies or cannot provide a location, the query falls back
to "Allston, Boston" — the location M0 was validated against, so it is
already known to return real results from Text Search.

It lives as a named module constant in `find.py`, not in `.env`. It is
neither a secret nor deployment-specific, and deployment is out of scope
for v1; putting it in `.env` would add a way for a half-filled `.env` to
break the app while buying nothing. Same pattern as
`MIN_TRUSTED_RATING_COUNT` — named and module-level, so it is easy to find
and easy to promote to configuration the day that is actually needed.

### Quota caps are sized to guarantee the free tier, not to be affordable
Expected spend is $0.00, because 1,000 Enterprise-SKU calls per month are
free and this project will not approach that. The caps are therefore set
so that any spend at all is evidence of a bug, rather than set to a
tolerable monthly number.

Configured: 30 requests/day, 6 requests/minute, a $1 billing budget alert,
and the API key restricted to Places API (New). Full reasoning per number
is in PROJECT.md § 3.5.

The number that does the work is **30/day**: 30 × 31 = 930 < 1,000, so the
free tier becomes arithmetic rather than a hope, even if the project is
touched every day of a long month. The 24-hour cache is what makes 30
comfortable — it is a budget of distinct queries, not of page loads, which
is also why the cache is built before the network code and not after.

Rejected: relying on the billing budget alert as the primary control.
Google budget alerts notify, they do not stop anything; hard enforcement
would need Pub/Sub wired to a function that disables billing, which is
disproportionate for a training project. The alert is kept as a tripwire
for the case the caps were set on the wrong SKU or API — which the caps
themselves cannot detect.

### Correction 2026-09-15: the daily cap moves into code
The entry above records 30 requests/day and 6 requests/minute as
"configured" in the Google console. They were not, and could not be.

Checking the Places API (New) quota page showed every row marked not
adjustable, `SearchTextRequest per day` (75,000) and
`SearchTextRequest per minute` (600) included. Google does not let these
be lowered.

The underlying mistake was treating a Maps Platform quota as a spending
limit. It is an anti-abuse rate limit with no relationship to billing.
The free tier is 1,000 calls per *month* while the daily quota is 75,000
— about 2,300× larger — so the quota page cannot protect this project
even in principle.

Revised: the 30/day cap is enforced in the cache layer in M3.2, as a
persisted counter checked before any network call. The per-minute cap is
dropped rather than reimplemented — with a hard daily cap already failing
closed, a rate limit only changes how fast the day's budget is spent, not
how much. The console keeps the two controls it can actually provide: the
$1 budget alert and the API key restriction.

This is a better outcome than the original plan. A cap in the repository
is reviewable, testable, and visible to anyone reading the code; a console
setting is none of those.

### Fallback location is query text, not a geocoded coordinate
When geolocation succeeds, lat/lng are passed to Text Search as a
`locationBias`. When it is refused, "Allston, Boston" is appended to the
query string instead — not converted into coordinates.

Geocoding the fallback would mean enabling the Geocoding API: a second
billable API, a second failure mode, and a second network call on the path
that is already the degraded one. Appending text is what M0 already did and
validated, and it reuses matching that Places is being paid for anyway.

The consequence is a constraint, not just a preference: Geocoding API stays
disabled, so this decision has to be revisited before anything in the
project starts needing coordinates for a place name.

### Twelve of thirteen enabled Google APIs disabled
The Maps Platform project had thirteen APIs enabled by default; the project
calls one. Places API (New) stays, the rest are off. Full list in
PROJECT.md § 3.6.

This saves nothing — an API that is never called is free. It removes
surface. The case that justifies the five minutes is Places API (Legacy):
a different SKU at different prices, reachable from a near-identical URL,
which a bug could hit and bill in a way § 3.5 does not model.

Noted to avoid a costly confusion later: Google's **Geolocation API** is
disabled and must stay that way. The user's location comes from the
browser's `navigator.geolocation`, which is a W3C API — free, keyless, and
unrelated to Google despite the near-identical name.

### Done 2026-09-15: only Places API (New) remains enabled
The twelve unused APIs were disabled. Places API (New) is now the only
enabled API on the project, which closes the Places API (Legacy) exposure
described above.

Still outstanding from M3.1: the $1 budget alert and restricting the API
key to Places API (New). Neither blocks M3.2 or writing M3.3, since no live
call happens until `find.USE_LIVE_API` is flipped. Both should exist before
that flip — the code cap in `cache.py` bounds a runaway loop, but nothing in
the repository can bound a leaked key.

## M3.3 — 2026-09-15

The live Places call, written and tested but not yet switched on.

### The field mask is a money control, and now has a test
`FIELD_MASK` names exactly the six M1 schema fields. Google bills each
call at the highest tier of any field requested, so the mask — not the
query, not the result count — is what decides whether a call costs $32,
$35, or $40 per thousand.

Two tests hold it: one asserts the mask covers exactly the M1 schema and
nothing more, the other asserts no Atmosphere-tier field (`reviews`,
`editorialSummary`, `generativeSummary`) appears in it. The second is the
one that matters. The SKU trap is invisible at the call site — adding one
field to get "a bit more context for the LLM" re-prices every call with
no error and no warning — so it is worth a test that fails loudly instead
of a comment nobody reads.

### Every failure becomes PlacesUnavailable
Timeouts, connection errors, non-200 statuses, non-JSON bodies, and a
missing API key all raise one exception type. Callers get one thing to
catch, and the web layer turns it into a sentence plus the technical
detail rather than a traceback. That is M3's exit condition.

The missing-key case deliberately raises before any request is attempted,
so a forgotten `.env` fails instantly and says exactly what to do.

### No matches is an empty list, not a failure
A Text Search with nothing to return comes back as `{}` with no `places`
key at all — not as an empty list. Reading it naively would turn an
ordinary "nothing found" into an error message. `body.get("places", [])`
and a test pin this, because it is the kind of thing that only shows up
in production against a weird query.

### Location is part of the cache key
`search_places` composes the full query — sentence plus location — before
doing anything else, and that composed string is both the cache key and
the text sent to Google. Keying on the bare sentence would let a result
fetched for one neighbourhood be served to someone standing somewhere
else, which is silent and wrong rather than loud and wrong.

### The location is always appended, even when the user gave one
"dumplings in Allston" becomes "dumplings in Allston, Boston". Text
Search resolves the redundancy without complaint. Detecting that the user
already named a location is constraint extraction — M4's job, and it
needs an LLM. Doing it now with string matching would be exactly the
regex approach already rejected for that work.
