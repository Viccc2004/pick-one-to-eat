# pick-one-to-eat

Describe what you want to eat in one sentence. Get back one restaurant
and one line explaining why.

**Status:** M3.1–M3.3 complete — the Google console is hardened, and the live
Places call, the 24-hour cache and the 30/day spend cap are all written and
tested, but `find.USE_LIVE_API` is still
`False`, so the app runs on fixtures and has never cost anything. Flipping that
switch is M3.4. Milestones live in [docs/PROJECT.md](docs/PROJECT.md) § 4.

## v1 scope

Anything outside this list goes to BACKLOG.md and does not get built.

- Interface: a web page — FastAPI backend, server-rendered HTML
- Input: a single natural-language sentence
- Output: **one** restaurant, one sentence of reasoning
- Ranking: a top-3 is ranked internally; ranks 2 and 3 are held back for
  a "show me another" action. A rule ranks them at M2, an LLM from M4
- Conversation: none. Each request is one sentence in, one answer out.
  The server holds no session state — the query and which candidate to
  show both travel in the URL
- Data source: Google Places API (New), and nothing else

## Explicitly out of scope

User accounts, saved favorites, maps, additional data sources,
deployment, conversation history of any kind — including the app asking
*you* a follow-up question — wait-time or queue-length features.

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

No environment variables are needed yet. M2 runs entirely on the
hand-written fixtures in `fixtures/`, so there is no API key, no network
call, and no cost. `.env` becomes necessary at M3 — copy `.env.example`
and fill it in then.

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
cat .cache/budget.json
```

Deleting `.cache/` resets both the cache and the day's counter.

## Documents

- [docs/PROJECT.md](docs/PROJECT.md) — working rules, requirements,
  technology comparisons, milestones
- [docs/DECISIONS.md](docs/DECISIONS.md) — one entry per settled
  technical decision
- [BACKLOG.md](BACKLOG.md) — deliberately not in v1
