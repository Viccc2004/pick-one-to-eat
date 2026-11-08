# pick-one-to-eat

Describe what you want to eat in one sentence. Get back one restaurant
and one line explaining why.

**Status:** M4.3 done — the page reads your sentence through Gemini
(free tier), asks up to two follow-up questions if where or when is
missing, then searches the live Google Places API and shows one
restaurant. Ranking and the reason are still placeholder rules. Next is
M4.4, using the browser's location when you don't name a place. Milestones live in [docs/PROJECT.md](docs/PROJECT.md) § 4.

Set `find.USE_LIVE_API = False` to run on fixtures with no key and no
cost. Tests always run on fixtures and never reach the network.

## v1 scope

Anything outside this list goes to BACKLOG.md and does not get built.

- Interface: a web page — FastAPI backend, server-rendered HTML
- Input: a single natural-language sentence
- Output: **one** restaurant, one sentence of reasoning
- Clarification: if the sentence leaves out where or when, the app asks
  — at most two follow-up questions, then it searches
- Location: a place named in the sentence wins; otherwise the browser's
  current location, and the page says so; if neither, the app asks
- Time: now unless the user says otherwise; places closed at that time
  are filtered out in code
- Ranking: a top-3 is ranked internally; ranks 2 and 3 are held back for
  a "show me another" action. A rule ranks them today, an LLM from M4
- State: the server holds no session state — prior turns travel in
  hidden form fields, which candidate to show in the URL
- Data source: Google Places API (New), and nothing else
- LLM: Gemini API free tier (`gemini-3.1-flash-lite`)

## Explicitly out of scope

User accounts, saved favorites, maps, additional data sources,
deployment, open-ended conversation — refining a result after it is
shown, or more than two follow-up questions — wait-time or queue-length
features.

## Project type

This is a training project, not a pain-point project.
Success means the full pipeline runs end to end at least once.
Success does not mean anyone uses it.

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://127.0.0.1:8000

Copy `.env.example` to `.env` and fill in both keys:
`GOOGLE_PLACES_API_KEY` (Places, billing-linked project) and
`GEMINI_API_KEY` (created in AI Studio in a **separate project with no
billing**, so it stays on the free tier — see
[docs/DECISIONS.md](docs/DECISIONS.md) § M4.0). Without keys, set
`find.USE_LIVE_API = False` to run on fixtures.

Tests:

```bash
pytest
```

## Spend guards

Live Places calls are capped at **30 per day**, enforced in `cache.py`
rather than in the Google console — Maps quotas are not adjustable and are
rate limits, not spending limits ([docs/PROJECT.md](docs/PROJECT.md) § 3.5).
Responses are cached for 24 hours, so re-running a debug query is free.

The counter is plain JSON and safe to read:

```bash
cat .cache/places_budget.json
cat .cache/gemini_budget.json
```

Gemini calls have their own cache and their own cap (400/day, under the
free tier's 500), in the same files with a `gemini_` prefix. Deleting
`.cache/` resets every cache and every counter.

## Documents

- [docs/PROJECT.md](docs/PROJECT.md) — working rules, requirements,
  technology comparisons, milestones
- [docs/DECISIONS.md](docs/DECISIONS.md) — one entry per settled
  technical decision
- [BACKLOG.md](BACKLOG.md) — deliberately not in v1
