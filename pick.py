"""Choose the top three and say why, in the user's own language.

One Gemini call ranks the candidates and writes a short recommendation
for each of its three picks, so "show me another" already has its
reasons and costs nothing more.

The LLM writes; the code checks. Every pick must name a real candidate,
and every number in a recommendation must be one of that place's facts -
a cheap, mechanical guard on the rule that the reason may cite only
supported conditions. A recommendation that fails falls back to the
template sentence from recommend.py; a call that fails falls back to the
rule-based ranking. Either way the user still gets an answer. See
docs/DECISIONS.md § M4.6.
"""

import re

import llm
from cache import BudgetUnreadable, DailyBudgetExceeded
from recommend import MIN_TRUSTED_RATING_COUNT, PRICE_LABELS, build_reason, rank_places

TOP_N = 3

# Google returns at most 10 (find.MAX_RESULTS); the cap is restated here so
# the prompt cannot grow with a future change to the search.
MAX_CANDIDATES = 10

# A recommendation is "a few sentences". Anything far longer has stopped
# being one and is more room to invent; it gets the template instead.
MAX_REASON_CHARS = 600

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "maxItems": TOP_N,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
            },
        }
    },
    "required": ["picks"],
}

SYSTEM = f"""\
You pick restaurants for someone and tell them why. You get the \
conversation in which they said what they want, the time they want to \
eat, and a numbered list of candidate places with the only facts known \
about each.

Return exactly {TOP_N} picks, or every candidate if there are fewer than \
{TOP_N}: the user can ask for another, so a second and third choice are \
always wanted, even if they fit less well. Order them best first, by how \
well each fits what they asked for: the food or kind of place (judge it \
from the name and type), price if they mentioned it, and rating. A rating \
from fewer than {MIN_TRUSTED_RATING_COUNT} reviews is weak evidence.

For each pick, write a recommendation of two or three short sentences, \
speaking to the user. Write it in the language named on the "Write in" \
line; if there is none, in the language of the user's own messages. The \
facts below are in English - that is not the user's language.

Rules for every recommendation:
- Use only the facts listed for that place. Never mention dishes, menu \
items, atmosphere, service, wait times, distance, or anything else that is \
not listed.
- Never write a number that is not in that place's facts or in the time \
they want to eat.
- If its hours are unknown, tell them to check the hours before going.
- If its rating comes from few reviews, say so.
- If they named a budget and this place's price is different, say so \
plainly. If its price is unknown, say that.
- If they named no budget, do not weigh price at all.
- Do not repeat the address.

When they named a budget, the candidates are listed with matching prices \
first; keep that order between groups and rank within them.
"""

# What each budget word from extract.PRICES accepts. Strict, as decided
# 2026-11-08: "cheap" does not stretch to mid-priced. Free counts as cheap.
PRICE_MATCH = {
    "cheap": {"PRICE_LEVEL_FREE", "PRICE_LEVEL_INEXPENSIVE"},
    "moderate": {"PRICE_LEVEL_MODERATE"},
    "expensive": {"PRICE_LEVEL_EXPENSIVE", "PRICE_LEVEL_VERY_EXPENSIVE"},
}

_BUDGET_WORDS = {"cheap": "cheap", "moderate": "mid-priced", "expensive": "expensive"}

FALLBACK_NOTE = (
    "Gemini couldn't rank these just now, so they are ordered by rating "
    "and the reason is a plain summary."
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _fact_line(index: int, place: dict, when_label: str) -> str:
    facts = [f"name: {place['name']}"]
    if place.get("place_type"):
        facts.append(f"type: {place['place_type'].replace('_', ' ')}")
    if place.get("rating") is not None and place.get("rating_count") is not None:
        facts.append(f"rating: {place['rating']} from {place['rating_count']} reviews")
    else:
        facts.append("rating: unknown")
    facts.append(f"price: {PRICE_LABELS.get(place.get('price_level'), 'unknown')}")
    open_then = place.get("open_then")
    facts.append(
        f"open {when_label}: " + {True: "yes", None: "unknown"}.get(open_then, "no")
    )
    return f"[{index}] " + "; ".join(facts)


def build_prompt(
    turns: list[tuple[str, str]],
    when_label: str,
    candidates: list[dict],
    price: str | None = None,
    language: str | None = None,
) -> str:
    """Deterministic, so the same request is one cache entry."""
    lines = ["Conversation:"]
    for speaker, text in turns:
        lines.append(f"{'User' if speaker == 'user' else 'App'}: {text.strip()}")
    lines += ["", f"They want to eat: {when_label}"]
    if price:
        lines.append(f"Budget they named: {_BUDGET_WORDS[price]}")
    if language:
        lines.append(f"Write in: {language}")
    lines += ["", "Candidates:"]
    lines += [_fact_line(i, p, when_label) for i, p in enumerate(candidates, start=1)]
    return "\n".join(lines)


def _allowed_numbers(place: dict, when_label: str) -> set[float]:
    """Every number a recommendation for this place may contain."""
    allowed = {float(MIN_TRUSTED_RATING_COUNT)}
    for value in (place.get("rating"), place.get("rating_count")):
        if value is not None:
            allowed.add(float(value))
    # Digits in the name are the name, not a claim: "B2J Tasty Pot",
    # "Pho 75". Found live - a correct reason rejected for repeating "B2J".
    allowed.update(float(n) for n in _NUMBER_RE.findall(place.get("name", "")))
    for match in _NUMBER_RE.findall(when_label):
        number = float(match)
        allowed.add(number)
        # "19:00" may be written "7pm" or "晚上7点".
        if number > 12:
            allowed.add(number - 12)
    return allowed


def numbers_are_supported(reason: str, place: dict, when_label: str) -> bool:
    """True if every number in the reason is one of the place's facts.

    Thousands separators are removed first, so "2,338 reviews" matches a
    count of 2338. Language-independent: it checks "4.8分" as readily as
    "4.8 stars".
    """
    text = re.sub(r"(?<=\d),(?=\d{3})", "", reason)
    allowed = _allowed_numbers(place, when_label)
    return all(float(n) in allowed for n in _NUMBER_RE.findall(text))


def _tier(place: dict, price: str | None) -> int:
    """0 matches the budget, 1 price unknown, 2 a different price.

    With no budget named every place is tier 0, so price changes nothing.
    """
    if price is None:
        return 0
    level = place.get("price_level")
    if level in PRICE_MATCH[price]:
        return 0
    return 1 if level not in PRICE_LABELS else 2


def _budget_pool(candidates: list[dict], price: str | None) -> list[dict]:
    """Order candidates by tier; keep other prices only to make up three.

    The order inside a tier is Google's, untouched. Places at a different
    price are offered to the ranking only when fewer than TOP_N match -
    the user asked for cheap, so with three cheap places a mid-priced one
    is not a choice at all.
    """
    matching = [p for p in candidates if _tier(p, price) == 0]
    unknown = [p for p in candidates if _tier(p, price) == 1]
    other = [p for p in candidates if _tier(p, price) == 2]
    pool = matching + unknown
    if len(matching) < TOP_N:
        pool += other
    return pool


def price_note(place: dict, price: str | None) -> str | None:
    """A sentence the code guarantees, whatever the LLM wrote.

    The prompt asks the model to say when a pick misses the budget; this
    makes sure the page says it even when the model does not.
    """
    tier = _tier(place, price)
    if tier == 0:
        return None
    asked = _BUDGET_WORDS[price]
    if tier == 1:
        return f"You asked for {asked}; this place's price level is unknown."
    return f"You asked for {asked}; this one is {PRICE_LABELS[place['price_level']]}."


def _with_notes(chosen: list[dict], price: str | None) -> list[dict]:
    """Enforce tier order over the picks and attach each price note.

    sorted() is stable, so the LLM's order is kept within a tier; it is
    only overruled where it put a worse price match above a better one.
    """
    chosen = sorted(chosen, key=lambda p: _tier(p, price))
    return [{**p, "price_note": price_note(p, price)} for p in chosen]


def _fallback(candidates: list[dict], when_label: str, price: str | None = None) -> list[dict]:
    """The rules, one tier at a time, so a budget still holds without Gemini."""
    ranked: list[dict] = []
    for tier in (0, 1, 2):
        ranked += rank_places([p for p in candidates if _tier(p, price) == tier])
    return _with_notes(
        [{**place, "reason": build_reason(place, when_label)} for place in ranked[:TOP_N]],
        price,
    )


def choose(
    turns: list[tuple[str, str]],
    when_label: str,
    places: list[dict],
    price: str | None = None,
    use_llm: bool = True,
    language: str | None = None,
) -> tuple[list[dict], str | None]:
    """Return (up to three places, each with a "reason"; a note or None).

    Places closed at the requested time never reach the LLM. The note is
    set when the rule-based fallback was used, so the page can say so.

    use_llm=False goes straight to the rules. The page passes it when
    paging through a list that the rules produced: a failed call is not
    cached, so without it page 2 could ask Gemini again, get a different
    order, and repeat or skip a place. Found live in the M4 exit check.
    """
    candidates = [p for p in places if p.get("open_then") is not False][:MAX_CANDIDATES]
    candidates = _budget_pool(candidates, price)
    if not candidates:
        return [], None
    if not use_llm:
        return _fallback(candidates, when_label, price), FALLBACK_NOTE

    try:
        answer = llm.generate_json(
            build_prompt(turns, when_label, candidates, price, language),
            SCHEMA,
            system=SYSTEM,
        )
    except (llm.LLMUnavailable, DailyBudgetExceeded, BudgetUnreadable):
        return _fallback(candidates, when_label, price), FALLBACK_NOTE

    picks = answer.get("picks") if isinstance(answer, dict) else None
    chosen: list[dict] = []
    seen: set[int] = set()

    for pick in picks if isinstance(picks, list) else []:
        if not isinstance(pick, dict):
            continue
        index = pick.get("id")
        # bool is an int in Python; True must not mean candidate 1.
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not 1 <= index <= len(candidates) or index in seen:
            continue
        seen.add(index)
        place = candidates[index - 1]

        reason = pick.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > MAX_REASON_CHARS
            or not numbers_are_supported(reason, place, when_label)
        ):
            reason = build_reason(place, when_label)

        chosen.append({**place, "reason": reason.strip()})
        if len(chosen) == TOP_N:
            break

    if not chosen:
        return _fallback(candidates, when_label, price), FALLBACK_NOTE
    return _with_notes(chosen, price), None
