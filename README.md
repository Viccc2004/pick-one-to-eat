# pick-one-to-eat

Describe what you want to eat in one sentence. Get back one restaurant
and one line explaining why.

## v1 scope

Anything outside this list goes to BACKLOG.md and does not get built.

- Interface: a web page — FastAPI backend, server-rendered HTML
- Input: a single natural-language sentence
- Output: **one** restaurant, one sentence of reasoning
- Ranking: an LLM ranks a top-3 internally; ranks 2 and 3 are held back
  for a "show me another" action
- Conversation: the page resends prior turns, the server stays stateless
- Data source: Google Places API (New), and nothing else

## Explicitly out of scope

User accounts, saved favorites, maps, additional data sources,
deployment, real multi-turn follow-up questions (the app asking *you*
something), wait-time or queue-length features.

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
call, and no cost. `.env` becomes necessary at M3 - copy `.env.example`
and fill it in then.

Tests:

```bash
pytest
```

## Documents

- [docs/PROJECT.md](docs/PROJECT.md) — requirements, technology
  comparisons, milestones
- [docs/DECISIONS.md](docs/DECISIONS.md) — one entry per settled
  technical decision
- [BACKLOG.md](BACKLOG.md) — deliberately not in v1
