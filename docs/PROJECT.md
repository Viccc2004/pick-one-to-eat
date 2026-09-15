# PROJECT — pick-one-to-eat

Requirements, technology comparisons, and milestones. Living document.
Settled technical decisions get a dated entry in [DECISIONS.md](DECISIONS.md)
and are stated here only as conclusions.

---

## 0. Working rules

These are the standing constraints every milestone is held to. They were
previously cited as "CLAUDE.md §4/§5/§7/§8"; that file is not in the
repository, so the rules are stated here and this section is now the
authority. Citations elsewhere in this document point here.

- **Frontend.** Server-rendered HTML. No React, no JS framework, no build
  step. Small amounts of hand-written vanilla JS are allowed where the
  browser is the only source of a value (see § 3.3, geolocation).
- **Dev cache.** Development must run against a local cache, so debugging
  does not hit the live API on every run. Constrained by Google's terms —
  see § 3.1.
- **Definition of Done**, applied to every milestone: it runs end to end;
  pytest covers the happy path plus at least one edge case; README is
  updated; the work is committed and pushed; and every file in the repo
  can be explained out loud.
- **Document layout.** `README.md` and `BACKLOG.md` at the root;
  `PROJECT.md` and `DECISIONS.md` under `docs/`.

---

## 1. Product

Describe what you want to eat in one sentence. Get back one restaurant and one
sentence explaining why it fits what you asked for.

**Status as of 2026-09-15:** M0–M2 done; M3.1 partly, M3.2 and M3.3 done. The
app runs end to end on fixtures with 47 passing tests. The live Places call and
both spend guards are written and tested against stubs, but `USE_LIVE_API` is
still `False`, so nothing has cost anything yet. M3.4 — flipping that switch —
is the first step that spends money.

### Shape (settled 2026-09-11, conversation row revised 2026-09-15)

| Aspect | Decision |
| --- | --- |
| Input | One natural-language sentence |
| Ranking | LLM ranks a top-3 internally |
| Output | **One** restaurant shown, with one sentence of reasoning |
| Held back | Ranks 2 and 3 are kept for a "show me another" action |
| Conversation | None. One sentence in, one answer out. The server is stateless: query and candidate index travel in the URL |
| Interface | Web page — FastAPI backend, server-rendered HTML, no JS framework |
| Data source | Google Places API (New), Text Search |

The reasoning sentence must cite only conditions the data can actually support.
Where the data is silent, the app says so and tells the user to verify — it does
not guess. See [DECISIONS.md](DECISIONS.md) § Reasoning may only cite supported
conditions.

### Out of scope for v1

User accounts, saved favorites, maps, multiple data sources, conversation
history of any kind — neither the page resending prior turns nor the LLM asking
follow-up questions — deployment, wait-time or queue-length features. Anything
cut goes to [../BACKLOG.md](../BACKLOG.md).

---

## 2. Is this hard enough to be a real engineering project?

Asked directly in the first-round prompt, so answered directly.

**The worry is correct, but it is aimed at the wrong part of the system.**

The pipeline really is one HTTP call plus one LLM call. That will not change no
matter how many features get added, and adding features will not make it more
of an engineering project — it will make it a bigger toy. Chasing engineering
weight through feature count is the wrong move.

The part that is genuinely hard, and genuinely the thing a Forward Deployed
Engineer does, is this: **how do you know whether a recommendation is any good?**
There is no ground truth for "the best restaurant." There is no
`assert result == expected` to write. When someone says "the recommendations
feel off," turning that sentence into something measurable — an eval set, a
definition of what counts as a good recommendation, and a loop that uses it to
drive prompt changes — is the skill. Almost every side project skips it. It is
harder than adding three more features and worth more.

So the recommendation is: do not add features to raise the engineering content.
Add **evaluation, cost observability, and failure degradation** — and add them
at M5, once the pipeline exists and there is something to measure. Building the
eval harness before there is a pipeline produces a harness for nothing.

Second honest note, unprompted: the single highest-risk unknown in this project
is not any piece of infrastructure. It is whether an LLM, given only the fields
Google returns, can write a reason sentence that is specific enough to feel
useful rather than generic. That is why M4 is scoped as its own milestone with
its own exit condition, instead of being bolted onto the end of M3.

---

## 3. Technology choices

### 3.1 Restaurant data

Verified against official documentation on 2026-09-11. Do not trust these
numbers past roughly one quarter without re-checking — Google restructured this
billing model in March 2025 and could again.

| | Google Places API (New) | Yelp Fusion / Places API |
| --- | --- | --- |
| Free allowance | Per-SKU, per month, ongoing | 30-day trial, 5,000 calls, evaluation only |
| After free tier | $32–40 per 1,000 calls by SKU | $7.99–14.99 per 1,000 by plan |
| Credit card | Required (billing account) | Required (paid subscription) |
| Coverage, US restaurants | Strong | Strong, arguably better review depth |
| Natural-language query | Text Search takes a sentence directly | Structured parameters |

**Chosen: Google Places API (New).** The deciding factor is not price per call —
it is that Google's free allowance renews monthly and forever, while Yelp's is a
one-time 30-day trial. For a project worked on ~10 hours a week over months, a
trial that expires mid-project is disqualifying. Text Search also accepts a
natural-language string directly, which was already validated in M0.

Not compared in depth: Foursquare and OpenStreetMap/Overpass. OSM is free but
carries no ratings, no price level, and no reviews, which removes most of what
the ranking step would use. Worth revisiting only if Google's cost becomes a
real constraint.

#### The SKU trap

Google bills each Text Search call **at the highest tier of any field in the
field mask**. One expensive field re-prices the entire call.

| SKU | Fields that trigger it | Free / month | Price / 1,000 |
| --- | --- | --- | --- |
| Essentials (ID Only) | `id` | Unlimited | — |
| Pro | `displayName`, `formattedAddress`, `location`, `primaryType` | 5,000 | $32 |
| Enterprise | `rating`, `userRatingCount`, `priceLevel`, `currentOpeningHours`, `regularOpeningHours` | **1,000** | $35 |
| Enterprise + Atmosphere | `reviews`, `editorialSummary`, `generativeSummary` | **1,000** | $40 |

This has a direct consequence that was not visible when the M1 schema was
designed on purely structural grounds: the current M0 code requests
`displayName` + `formattedAddress` and bills as **Pro** (5,000 free/month). The
M1 schema adds `rating`, `userRatingCount`, `priceLevel`, and
`currentOpeningHours` — every one of them an **Enterprise** field. Implementing
M1 as specified silently cuts the monthly free allowance from 5,000 to 1,000, an
80% reduction.

At this project's volume 1,000 calls/month is still comfortable, so the schema
stands. But it means a dev-time cache stops being good hygiene and becomes the
thing that keeps the project free, and it means adding `reviews` later is a
deliberate decision, not a small one.

#### Caching is contractually limited

Google's Maps Platform terms forbid caching or storing Places content, with
narrow exceptions: `place_id` may be stored indefinitely (Google suggests
refreshing IDs older than 12 months), and latitude/longitude may be cached for
up to 30 consecutive days.

§ 0 requires a local cache during development so that debugging does not hit
the live API every run. That requirement and these terms are compatible, but
only if the cache is built deliberately:

- Dev cache TTL stays well inside 30 days — 24 hours is enough for debugging and
  leaves no ambiguity.
- Test fixtures are **hand-written**, not recorded API responses. This was
  already the M1 decision, made for testability reasons; it happens to also be
  the compliant choice.

### 3.2 Natural language → structured query

M0 established that Text Search accepts a natural-language string directly, so
the question is narrower than it first appears: how much structure has to be
extracted before the query is sent?

| Option | Verdict |
| --- | --- |
| Pass the sentence straight through | Chosen for the query itself |
| LLM structured output to extract slots | Chosen for filtering and ranking |
| Hand-written regex / keyword rules | Rejected |

Regex is rejected because the failure mode is silent and unbounded: an unmatched
phrasing yields a wrong filter with no signal that anything went wrong, and each
patch invites the next. The LLM's failure mode — malformed or schema-invalid
output — is at least detectable and can be handled.

The split: the sentence goes to Google largely as written (Google's own matching
is the thing being paid for), while an LLM call extracts the constraints that
have to be applied *after* results come back — price ceiling, party size,
open-now. Failure handling for that call is M4's problem and is listed in its
exit condition.

### 3.3 Frontend

FastAPI with server-rendered HTML templates, one page, one form. No React, no
build step, per § 0.

The server holds no session state. The query string and which candidate to show
travel in the URL (`/?q=...&rank=1`), so "show me another" is a plain link and
needs no session storage, no expiry, and no state machine. Carrying prior turns
in the form was in scope until 2026-09-15 and is now in
[../BACKLOG.md](../BACKLOG.md); it can be added later without touching the
ranking pipeline.

**Location comes from browser geolocation** (settled 2026-09-15). This is the
one place the page runs JavaScript: a few lines of vanilla `navigator.geolocation`
that fill a hidden lat/lng field before submit. That is consistent with § 0 — the
rule bans frameworks and build steps, not the browser API that is the only source
of this value. Two consequences to build for in M3: the API requires a secure
context, which `127.0.0.1` satisfies, so local development works without HTTPS;
and permission can be denied or unavailable, so a fallback location must exist.
The fallback is **"Allston, Boston"** (settled 2026-09-15) — the location M0 was
validated against — as a named constant in `find.py`, not a `.env` variable. It
carries nothing secret and nothing deployment-specific, and putting it in `.env`
would let a half-filled `.env` break the app for no benefit while deployment is
out of scope.

The two paths reach Places differently, and deliberately so:

- **Geolocation succeeded** → lat/lng go in as a Text Search `locationBias`.
- **Geolocation refused or unavailable** → the string is appended to the query
  text. This is exactly what M0 did and validated; Text Search resolves place
  names itself.

The fallback is **not** geocoded into coordinates. Turning the string into
lat/lng would require the Geocoding API — a second billable API, a second
failure mode, and a second call on the path that is already the degraded one.
Appending text costs nothing and reuses matching that is already being paid for.
This is why Geocoding API stays disabled (§ 3.6).

One naming trap worth recording: `navigator.geolocation` is the **browser's**
W3C API — free, no key, unrelated to Google. Google's own **Geolocation API** is
a different, billable, server-side product that infers position from cell and
WiFi signals. This project uses the former and must never enable the latter.

### 3.4 Cost per query

| Component | Estimate |
| --- | --- |
| Places Text Search, Enterprise SKU | $0.035 |
| LLM ranking — `claude-opus-5`, ~1.2K in / ~150 out | ~$0.010 |
| **Total per recommendation** | **~$0.045** |

The counterintuitive result: **Google is the expensive half, not the LLM** —
roughly 3.5x. Optimization effort should go to reducing Places calls (cache,
fixtures) before it goes anywhere near model choice.

LLM pricing per 1M tokens: `claude-opus-5` $5 in / $25 out; `claude-sonnet-5`
$2 / $10; `claude-haiku-4-5` $1 / $5. Default is `claude-opus-5` — at ~$0.01 per
recommendation, downgrading to save a fraction of a cent is not worth the
quality risk while the reason-sentence quality is still the open question.
Revisit if M5's eval shows a cheaper model scores the same.

Development-time spend control, in order of effect:

1. Fixtures for anything that does not need the network — most of M2 and all
   `parse_places` tests cost $0.
2. A 24-hour local cache keyed by query string, so re-running the same debug
   query is free.
3. Both API keys in `.env`, both quotas capped in their consoles before the
   first real call — the configured values are in § 3.5.

### 3.5 Spend controls (M3.1, 2026-09-15)

Expected spend for this project is **$0.00**: 1,000 Enterprise-SKU calls per
month are free and the project will not approach that. So these limits are not
tuned to "stay affordable" — they are tuned so that **any** spend at all is a
signal that something is broken.

#### The console cannot do this (verified 2026-09-15)

The original plan was to cap requests per day and per minute in the Google
console. **That is not possible.** In the Places API (New) quota page every row,
including `SearchTextRequest per day` and `SearchTextRequest per minute`, is
marked not adjustable. The defaults are:

| Quota | Default | Adjustable |
| --- | --- | --- |
| `SearchTextRequest per day` | 75,000 | No |
| `SearchTextRequest per minute` | 600 | No |

The deeper point, which is easy to miss: **a Maps Platform quota is not a
spending limit.** It is an anti-abuse rate limit and is unrelated to billing.
The free tier is 1,000 calls *per month*; the daily quota is 75,000 — roughly
2,300× larger. Running inside quota for a single day would burn 75 months of
free allowance. The quota page offers this project no protection whatsoever,
and reading it as a budget control is a category error.

#### What actually controls spend

| Control | Value | Where | Failure mode it stops |
| --- | --- | --- | --- |
| Daily call cap | **30/day** | **In code** (M3.2) | Steady over-use, and a loop left running overnight |
| Billing budget alert | **$1** | Console | A cap set on the wrong SKU or API |
| API key restriction | Places API (New) only | Console | A leaked key spent on anything else |
| Unused APIs disabled | — | Console | Spend surface that nothing is watching |

**Why 30/day, and why it moved into code.** This is the load-bearing number:
30 × 31 = 930 < 1,000, so the free tier becomes arithmetic rather than a hope,
even if the project is touched every day of a long month. Thirty *distinct*
queries a day is generous, because the 24-hour cache means re-running the same
debug query costs nothing — it is a budget of new queries, not of page loads.

Since Google will not enforce it, the cache layer does: a persisted per-day
counter, checked before any network call, that refuses rather than requests once
the day's budget is gone. This is strictly better than the console version would
have been. It lives in the repository where it can be read and reviewed, it is
covered by tests, and it fails closed with an error message written for this
project rather than a generic 429.

**Why a $1 budget alert.** The code cap protects the one call site that goes
through the cache. It cannot see a second enabled API, a different SKU, or a
field-mask change that silently re-prices calls to Enterprise + Atmosphere. A $1
threshold against an expected $0.00 fires after roughly 14 paid calls — a
tripwire, not a budget.

Honest limit: **a Google budget alert notifies, it does not stop anything.** Hard
enforcement needs a Pub/Sub topic wired to a function that disables billing,
which is disproportionate here. The code cap is the actual stop; the alert is how
you learn the cap was set on the wrong thing.

**Why key restriction belongs in this list.** An unrestricted Google API key
works from anywhere for anyone who has it. `.env` is gitignored, which handles
the obvious leak, but a key scoped to one API means a leak costs at most this
project's quota instead of everything billable in the account. Restricting the
key is part of capping spend, not a separate security chore.

### 3.6 Enabled APIs (audited and reduced 2026-09-15)

Creating the Maps Platform project enabled thirteen APIs by default. This
project calls exactly one. The other twelve were disabled on 2026-09-15;
**Places API (New) is now the only API enabled on the project.**

**Kept:** Places API (New) — Text Search only.

**Disabled:** Places API (Legacy), Places Aggregate API, Places UI Kit,
Geocoding API, Geolocation API, Directions API, Distance Matrix API, Routes API,
Roads API, Route Optimization API, Time Zone API, Address Validation API.

Be honest about the benefit: an enabled API that is never called costs nothing,
so this saves $0 today. What it removes is *surface*. A leaked key or a wrong
endpoint in a bug can only reach what is enabled, and Places API (Legacy) is the
sharpest example — it is a different SKU at different prices reachable from a
near-identical URL, so an accidental call there would bill in a way none of the
reasoning in § 3.5 accounts for.

Two of the disabled ones are worth naming because they sound required and are
not: **Geolocation API** is not what supplies the user's location (the browser
is — see § 3.3), and **Geocoding API** is not needed because the fallback
location is appended to the query as text rather than converted to coordinates.

Re-enabling any of these is one click if a later milestone needs it. Disabling
is reversible and free, so the default is off.

---

## 4. Milestones

M0 and M1 predate this document. They were research and design milestones and
are recorded in [DECISIONS.md](DECISIONS.md); their conclusions survive the
change in product shape. From M2 on, every milestone is a **vertical slice**:
it ends with something that runs end to end.

Definition of Done for each is the one in § 0 — end-to-end run, pytest covering
the happy path plus one edge case, README updated, committed and pushed, and
able to explain out loud what every file does.

### M0 — Places API validated ✅

Text Search returns real restaurants for a natural-language query. Confirmed the
API cannot index menus, which is why a ranking layer exists at all.

### M1 — Schema and function split, designed ✅ absorbed into M2

`search_places` / `parse_places` split at the network boundary, the six-field
internal schema, and per-field missing-value handling were decided and
documented here, then implemented as part of M2 against fixtures for $0. M1 is
not a separate milestone going forward.

Note on numbering: the M1 entries in DECISIONS.md say "M3" where they mean the
ranking and reasoning layer. Under the current plan that layer is M2 (rule stub)
and M4 (LLM); M3 is the live-data milestone.

### M2 — End-to-end skeleton, zero API calls ✅ done (commit `71592fe`)

The first thing that runs end to end.

- FastAPI app, one page: a text input and a result.
- 10 hand-written fixture restaurants in the M1 schema, including deliberately
  incomplete ones (missing `rating`, missing `priceLevel`, missing `name`).
- `parse_places` implemented and tested against those fixtures.
- A stubbed reason sentence — a fixed template, no LLM.
- `requirements.txt`, venv, `.env.example`.

Exit ✅: typing a sentence in a browser returns one restaurant and one sentence,
with no network call and no API key. 16 tests pass.

### M3 — Real data

Replace the fixtures with live Places calls.

- **M3.1** Console hardening before the first real call. Unused APIs disabled ✅
  (2026-09-15, only Places API (New) remains enabled). Still open: the $1 budget
  alert and restricting the key to Places API (New). Per-day and per-minute
  quotas are *not* adjustable — see § 3.5.
- **M3.2** ✅ A 24-hour local cache keyed by query string, plus the 30/day call
  cap the console cannot enforce — both in `cache.py`, wired into
  `search_places`, 15 tests. Built before the network code, so every later
  debugging run is free.
- **M3.3** ✅ *code written, switch still off.* `_fetch_live` calls Text Search
  with the M1 field mask, a 10-second timeout, and every failure funnelled into
  one `PlacesUnavailable`. The web layer renders those as a sentence. 16 tests,
  all against stubbed responses — no live call has happened.
- **M3.4** Flip `USE_LIVE_API` to `True` and make the first real call. This is
  the step that spends money; § 3.5's remaining console items should exist
  first.
- **M3.5** Browser geolocation: hidden lat/lng field, `locationBias` on the
  request, fallback to the appended text location.
- The incomplete-response path stays covered by the M2 fixtures.

Exit: the same page, now backed by real restaurants near the user, with the API
down — or geolocation refused — producing a clear message rather than a
traceback.

### M4 — The LLM layer

The riskiest milestone, isolated on purpose.

- Constraint extraction from the sentence.
- Rank a top-3; show rank 1; hold 2 and 3 for "show me another."
- Generate the reason sentence under the rule that it may cite only supported
  conditions.
- Handle malformed LLM output without crashing the request.

Exit: for five hand-picked test sentences, the reason sentence references the
actual conditions asked for and invents nothing.

### M5 — Evaluation and cost observability

The milestone that makes the rest defensible, and the answer to §2.

- An eval set of ~20 sentences with a written definition of what counts as a
  good recommendation.
- A script that runs the eval and reports a score.
- Per-request cost logging, so spend is a number rather than a monthly surprise.

Exit: a prompt change can be shown to improve or hurt the score, with evidence.

---

## 5. Open questions

- **~~Location.~~** Settled 2026-09-15: browser geolocation, with a fallback for
  denied permission. See § 3.3 and DECISIONS.md.
- **`place_type` usefulness.** In the schema since M1, extracted by
  `parse_places`, but still has no consumer — M2's rule-based ranker ignores it.
  Decide by M4 or drop it.
- **~~Geolocation fallback value.~~** Settled 2026-09-15: "Allston, Boston", as a
  named constant in `find.py` rather than a `.env` variable. See § 3.3.
