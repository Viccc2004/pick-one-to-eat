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

## M3.4 — 2026-09-27

The switch is on. First real call made.

### Tests are pinned to fixtures, whatever the switch says
With `USE_LIVE_API = True` and a real key in `.env` (loaded by
`find.py` on import), every plain fixture test — the ones that never set
the flag because `False` used to be the default — would have made a
billed call. `conftest.py` now pins the flag to `False` for every test
and replaces `requests.post` with one that fails the test. Live-path
tests turn the flag back on and stub the network themselves, as they
already did. A test that forgets fails loudly instead of spending.

This had to land in the same change as the flip. Flipping first and
fixing tests second would have made the first `pytest` run the first
real calls — up to one per fixture test.

### What the first call showed
`cheap chinese food` → `cheap chinese food in allston, boston`: 8 places
back, each carrying exactly the six field-mask fields and nothing else,
which is the mask doing its job. Re-running the query and following
"show me another" were both cache hits; the counter stayed at 1.

The recommendation was a closed restaurant, and so was the runner-up.
Nothing is broken — the M2 ranker was never asked to care, and the
fixtures happened not to show it. Recorded as an open question in
PROJECT.md § 5 rather than fixed here, to keep this change to the flip.

## M4.0 — 2026-11-08

Scope and provider decisions taken before any M4 code. Several of them
reverse earlier entries; each says which.

### Gemini free tier replaces Claude
Supersedes the `claude-opus-5` default in PROJECT.md § 3.4 and the
`ANTHROPIC_API_KEY` slot in `.env.example`.

The Gemini API has a free tier; Claude does not. Expected LLM spend drops
from ~$0.01 per recommendation to $0.00, which is the same posture § 3.5
takes for Places: any spend at all means something is broken.

Verified 2026-11-08 in AI Studio: the key lives in a **new, separate
project** (`PickAPI`, `gen-lang-client-0904457958`) whose billing tier is
shown as Free. It deliberately does not live in the Places project. Gemini
limits and billing attach to the project, and the Places project has a
billing account linked — a key created there would plausibly bill as paid
tier from the first call.

Accepted costs:

- Free-tier prompts and responses may be used by Google to improve its
  products. The content is restaurant requests; acceptable.
- Free-tier limits are best effort and have been cut before. If they drop
  again, this decision is the one to revisit, not the app's design.

### Model: `gemini-3.1-flash-lite`, pinned
Free-tier limits observed in AI Studio on 2026-11-08 for the `PickAPI`
project:

| Model | RPM | RPD |
| --- | --- | --- |
| Gemini 3.1 Flash Lite | 15 | **500** |
| Gemini 3 Flash | 5 | 20 |
| Gemini 2.5 Flash | 5 | 20 |
| Gemini 2.5 Flash Lite | 10 | 20 |
| Gemini 2.5 Pro / 3.1 Pro | 0 | 0 |

One recommendation can take up to four LLM calls (up to three
extraction turns plus one ranking call). At 20/day that is five
recommendations a day, which an M5 eval run of ~20 sentences would exhaust
on its own. At 500/day it is 125. Model quality is M5's question to answer
with evidence; quota decides the starting point.

The ID is the pinned `gemini-3.1-flash-lite`, not the
`gemini-flash-lite-latest` alias. An alias Google can repoint would make
an M5 score change impossible to attribute to the prompt.

### REST through `requests`, not the `google-genai` SDK
The SDK depends on `cryptography`, whose current release (50.0.2) has no
prebuilt wheel for this machine (x86_64 macOS) and failed to build from
source. Rather than pin an older `cryptography`, Gemini is called as a
plain HTTP endpoint with `requests` — the same pattern as `find.py`.

That turns out to be the better choice on its own merits: no new
dependency, one HTTP library in the codebase, and the `requests.post` guard
in `conftest.py` already makes it impossible for a test to reach Gemini.

### LLM calls get a cache and a daily cap, like Places
On the free tier Google enforces the daily limit itself with a 429, so
the cap is not protecting money today. It exists for the day billing gets
linked to the project by mistake, and so the app's own message fires
before Google's error. Cap set below the 500 RPD free limit.

The cache matters more than the cap. "Show me another" re-runs the
pipeline from the URL; with LLM responses cached by prompt, paging to
rank 2 costs no LLM call, the same way it already costs no Places call.

### Clarifying questions move into v1, capped at two
Supersedes § Single turn, not pseudo-multi-turn (2026-09-15). The
"multi-turn slot filling" BACKLOG entry moves into scope.

The LLM reads the sentence and decides what is missing. If something is,
the page asks; the user answers; at most **two** such rounds. The cap is
enforced in code, not left to the prompt. When the cap is reached:

- **When to eat** still unknown → assume now, and say so.
- **Where** still unknown → no Places call; ask the user for a location.
  (Only reachable when the sentence names no place *and* the browser gave
  no location — see the next entry.)

The server stays stateless. Prior turns travel in hidden form fields and
come back with each submit, which is how the 2026-09-11 pseudo-multi-turn
entry proposed to do it. The open-ended "change one condition and
recompute" BACKLOG entry is a different feature and stays in BACKLOG.

### Location: named place > browser location > ask
Supersedes § Geolocation fallback is "Allston, Boston", hardcoded and
§ The location is always appended, even when the user gave one. Absorbs
M3.5.

1. **The sentence names a place** ("I'm in LA but heading to Boston") →
   the LLM extracts "Boston", and it is appended to the Text Search query
   as text. Still no Geocoding API, for the reasons in § Fallback location
   is query text, not a geocoded coordinate.
2. **No place named** → the user's current location from
   `navigator.geolocation`, passed as `locationBias`. The page states that
   it searched near the user's current location, so the default is never
   silent and the user knows naming a place would change it.
3. **No place named and no browser location** → no Places call. The page
   asks where.

`FALLBACK_LOCATION` is removed. A hardcoded Allston answer for someone
standing elsewhere is a confident wrong answer, which § Reasoning may only
cite supported conditions exists to prevent.

### Open-at-that-time is decided in code, not by the LLM
Resolves the PROJECT.md § 5 question "Closed places ranked first."

The LLM extracts *when* the user wants to eat. Whether a place is open
then is arithmetic over opening hours, and is done in Python where it is
deterministic and testable. The LLM only ranks and explains candidates
that already passed.

Two findings force a schema change:

- A time other than now needs weekly hours, so `regularOpeningHours` joins
  the field mask. PROJECT.md § 3.1 lists it in the Enterprise row, the
  same tier as the existing `currentOpeningHours`, so the call price should
  not move. To be re-verified against current Google docs before the mask
  changes — the field mask is a money control.
- **The current `openNow` is wrong for up to 24 hours.** It is evaluated
  by Google at fetch time and then served from the 24-hour cache, so a
  place that closed at 21:00 can still read "open right now" at 23:00 from
  a 19:00 fetch. Computing open-ness from hours at request time fixes this
  as a side effect. How the place's local time zone is obtained (and which
  SKU that field sits in) is open until M4.5.

## M4.1 — 2026-11-08

The Gemini client, `llm.py`. One function, `generate_json(prompt, schema,
system)`, that knows nothing about restaurants.

### cache.py serves both APIs, one file and one cap per API
`cache.py` takes an `api` argument (`"places"` by default, `"gemini"` for
the LLM), and each API gets its own response file, counter file, and cap:
30/day for Places, 400/day for Gemini. Separate counters mean an
afternoon of prompt debugging cannot use up the day's Places calls.

The Places files were renamed from `responses.json` / `budget.json` to
`places_responses.json` / `places_budget.json` so both APIs follow one
pattern. The old files are orphaned and harmless; the September counter
in them had expired anyway.

### The key is checked before the call is counted
In `find.py` a missing `GOOGLE_PLACES_API_KEY` is discovered after
`record_call`, so a misconfigured `.env` spends one unit of budget per
attempt. `llm.py` checks the key first. `find.py` is left as it is — the
cost is one count per failure, and the fix belongs in a change about
`find.py`.

### Only a valid answer is cached
Malformed JSON, a non-object, a blocked prompt, or a `MAX_TOKENS` cut-off
all raise `LLMUnavailable` before `cache.store`. Caching a broken answer
would serve it back for 24 hours. The call is still counted, as with
Places: it was made.

### Timeout 30 seconds, thinking left at the default
The first real call timed out at the original 20s. Measured on the same
structured request: 0.8s with default thinking, 16.9s with
`thinkingLevel: minimal` — so the slowness is free-tier variance on
Google's side, not the model thinking, and turning thinking down buys
nothing. Timeout raised to 30s.

This matters for M4.3: one recommendation can take up to four calls in a
row, so worst-case latency is tens of seconds. The cache makes repeats
free; first-time latency is a known cost to watch, not yet a problem.

### What the first call showed
`responseJsonSchema` is accepted by `gemini-3.1-flash-lite` on v1beta, and
the answer came back schema-shaped. "I'm in LA right now but flying to
Boston tomorrow, want cheap dumplings for dinner there" was extracted as
`location: "Boston"`, `when: "later"` — the LA-vs-Boston case M4.2 has to
handle, right on a first try with a one-line system prompt.

Budget used on 2026-11-08: 2 counted calls (the timeout and the success),
plus 3 diagnostic calls made outside `llm.py` that count against Google's
500/day but not the app's counter.

### Follow-up 2026-11-08: find.py checks the key first too
Supersedes the "`find.py` is left as it is" paragraph above. `search_places`
now checks `GOOGLE_PLACES_API_KEY` before the budget, so a missing key
fails without counting a call, matching `llm.py`.

Doing it exposed a hidden dependency: the live-path tests had been
passing only because `find.py` loads the real key from `.env` on import.
On a machine without `.env` they would have failed. `conftest.py` now
sets fake values for both keys in every test, so no test ever holds a
real key and the suite passes with no `.env` present (verified by running
it from a copy of the repository without one).

## M4.2 — 2026-11-08

Extraction, `extract.py`: the conversation in, a `Request` out — what to
search for, where, when, price, what is still missing, and the question
to ask about it. Not yet wired into the page (M4.3).

### The LLM reads, the code decides what is missing
The first version asked the LLM to judge what was missing and write one
question about it. On the live run it got this wrong in both directions:
for "ramen" with browser location available it asked where *and* when;
for "ramen" without it, it asked only where. The fields it extracted were
right both times — the judgement on top of them was not.

Now the LLM always writes three questions (where, now-or-later, what
time) in the user's language, whatever the conversation says, and
`find_missing` picks which ones are shown. The LLM is never asked a
conditional. Re-run: all seven trial sentences correct.

### Only where and when are asked; food is not
"Somewhere to eat near me" is a complete request. With no food named,
the search phrase is `"restaurant"`. Follow-up questions are spent only
on what the search cannot run without (where, when) or cannot filter
without (a time for "later").

### The search phrase is written by the LLM, in English
Supersedes the 2026-09-11 "the sentence goes to Google largely as
written". With clarifying turns there is no single sentence to send, so
the LLM writes a short phrase — food plus qualities, no place, no time —
and code adds the place. It is asked for English so that a Chinese
request ("想吃火锅") searches as "hot pot"; on the first run, before that
instruction, it passed "火锅" through untranslated.

### Meal words map to fixed times
Breakfast 08:00, brunch 11:00, lunch 12:00, dinner 19:00, late night
22:00, stated in the prompt. This is the one place the LLM is told to
fill a gap, so it is a written rule rather than the model's taste, and
the page will say which time it assumed (M4.7).

### No clock and no permission flag in the prompt
The LLM answers "tomorrow" / "19:00"; code will turn that into a date at
request time (M4.5). Whether the browser gave a location only affects
what is missing, which code decides. Keeping both out means the prompt
depends on the user's words alone: the same words are one cache entry,
"show me another" costs no call, and "ramen" with and without location
permission was a cache hit on the live run.

### Bad fields degrade to "not said", one field at a time
Each field is checked on its own: an unknown enum, a time not in `HH:MM`,
a wrong type, or an absent key becomes `None` (or `"unknown"` for when).
That surfaces in `missing`, so a bad answer turns into a question rather
than a crash or a guess. Case and stray whitespace in enums are
forgiven. An `LLMUnavailable` is not caught here: the page owns the
sentence for that.

### Latency, second sample
Seven new prompts, twice: 0.8–3.7s mostly, one 10.8s, one 16.2s, and one
more 30s timeout. Two timeouts in fourteen calls. Whether to retry once
on timeout is M4.3's decision, where a person is actually waiting.

## M4.3 — 2026-11-08

The clarifying loop on the page. `app.py` now sends every submitted
sentence through `extract.extract` first; a request with gaps gets a
question, a complete one gets searched.

### Timeout 15 seconds, retried once
Chosen over showing a "try again" message. Two of fourteen M4.2 calls
hung past 30s while most answered in 1–4s: a hung call rarely recovers,
and a fresh one usually answers quickly. Worst case is 2 × 15s = 30s,
no worse than the old single timeout.

Only a timeout is retried. A 429, a 500, a blocked prompt or malformed
JSON would most likely fail the same way again. The retry passes the
budget check and is counted like any other call — this is the
difference from the SDK's automatic retries rejected in § M4.0, which
would have spent quota where the counter cannot see it.

### The question count is read off the conversation
`extract` counts the "app" turns in what it is given; there is no
separate counter to carry or to get out of step. At two questions it
stops asking:

- when still unknown → now, with a sentence on the page saying so;
- a later meal still without a time → searched anyway, with a sentence
  saying opening hours could not be checked. There is no honest default
  time for "tomorrow, some time";
- no place and no browser location → still asked, past the cap. With
  nothing to search near, asking is the only honest move.

### The conversation travels in repeated `t` fields, by GET
Prior turns go back to the server as hidden `<input name="t">` fields,
oldest first, alternating user and app. The form stays GET, so "show me
another" is still a plain link: it carries the same `t` fields, the same
`q`, and `rank + 1`. Re-reading the conversation is a cache hit in both
Gemini and Places — verified live, neither counter moved when paging.

A hand-edited odd-length history drops its trailing entry rather than
erroring. User text is HTML-escaped by Jinja's autoescape (checked: it
is on for `.html` templates in Starlette), and a test pins that.

### The page states what it searched for
Every result carries a line like `dumplings · Boston · tomorrow at
19:00`, and every assumption made after the cap is printed under the
card. Nothing the app filled in on the user's behalf is silent. This is
the "tell the user the default" requirement, applied before M4.4 adds
the default that needs it most ("near you").

### No browser location until M4.4
`HAS_BROWSER_LOCATION` is `False`, so a request that names no place is
always asked where. That is exactly the location-refused path, so it is
the first one built.

### What the live run showed
`I want ramen` → "Where would you like to eat? Are you planning to eat
now or later?" → `in Allston, right now` → Isshindo Ramen; "show me
another" → Hokkaido Ramen Santouka, with no new calls.
`想吃饺子，我现在在LA，明天晚饭去Boston吃` → `dumplings · Boston ·
tomorrow at 19:00` → Dumpling center, no question asked. Four requests,
8.3s in total, three Gemini calls and two Places calls.

## Scope addition — 2026-11-08

### The card shows the address and a Google Maps link
Supersedes § Dropped: formattedAddress (M1) and removes the BACKLOG entry
that waited for exactly this. Getting to the place is part of acting on
the answer. `formattedAddress` and `googleMapsUri` are both Text Search
Pro fields (checked against Google's Text Search (New) page on
2026-11-08), and the call already bills at Enterprise because of
`rating` and friends, so adding them costs nothing. Done in M4.5, with
the other field mask change, so the SKU is checked once.

`googleMapsUri` is used rather than building a Maps URL by hand: it is
Google's own link to the exact place, not a search that could land on
a namesake.

### The recommendation is a short paragraph in the user's language
Widens M4.6 from "one sentence" to a few sentences, written in whatever
language the user wrote in — a Chinese request gets a Chinese reason.
The rule that it may cite only supported conditions is unchanged; more
sentences means more room to invent, which M4.6's tests and M5's eval
have to watch.

## M4.4 — 2026-11-08

Browser geolocation, previously M3.5. `FALLBACK_LOCATION` is gone.

### How each source reaches Google
- **A named place** → appended to the text, `"ramen in Boston"`, with
  **no** bias. Live check: LA coordinates plus "ramen in Boston" returned
  a Boston restaurant. Biasing toward where the user stands would pull
  the results back toward LA.
- **Browser position only** → the search phrase alone, plus a
  `locationBias` circle of 5 km. A bias rather than a restriction, so a
  sparse area returns something further out instead of nothing. Live
  check: Allston coordinates returned Allston ramen; LA coordinates
  returned Little Tokyo.
- **Neither** → `find._plan` raises `ValueError`. The page asks where
  before it ever gets there, so reaching it is a bug, and it should be a
  loud one rather than a silent search of somewhere arbitrary.

### Coordinates rounded to 3 decimals, on both sides
About 110 m. The page rounds before submitting and the server rounds
again before using them. Precise enough for a 5 km bias; coarse enough
that GPS jitter on the same block is one cache entry, not one call per
refresh; and the exact position never lands in a URL, the browser
history, or `.cache/`.

### When the page asks the browser
On submit, only if the form has no position yet. A follow-up answer
carries the position the first submit found, so the browser is not
asked again mid-conversation. A fresh search starts empty and looks
again, since the user may have moved — the browser's own 10-minute
`maximumAge` keeps that cheap. Refused, unavailable, or slower than 8s:
the form submits without a position and the server asks where.

### "Near you" is always said
A search run on the browser's position prints "You didn't name a place,
so this searched near your current location. Name one to search
somewhere else." The default is the requirement; saying it is what keeps
it from being a silent guess.

### Not tested automatically
The script itself has no test — there is no browser in the test suite.
The server side of every path it can produce (position, no position,
nonsense position) is covered; the script is checked by hand in a
browser.
