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
speaking to the user, in the language the user wrote in.

Rules for every recommendation:
- Use only the facts listed for that place. Never mention dishes, menu \
items, atmosphere, service, wait times, distance, or anything else that is \
not listed.
- Never write a number that is not in that place's facts or in the time \
they want to eat.
- If its hours are unknown, tell them to check the hours before going.
- If its rating comes from few reviews, say so.
- Do not repeat the address.
"""

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
    if place.get("price_level") in PRICE_LABELS:
        facts.append(f"price: {PRICE_LABELS[place['price_level']]}")
    open_then = place.get("open_then")
    facts.append(
        f"open {when_label}: " + {True: "yes", None: "unknown"}.get(open_then, "no")
    )
    return f"[{index}] " + "; ".join(facts)


def build_prompt(turns: list[tuple[str, str]], when_label: str, candidates: list[dict]) -> str:
    """Deterministic, so the same request is one cache entry."""
    lines = ["Conversation:"]
    for speaker, text in turns:
        lines.append(f"{'User' if speaker == 'user' else 'App'}: {text.strip()}")
    lines += ["", f"They want to eat: {when_label}", "", "Candidates:"]
    lines += [_fact_line(i, p, when_label) for i, p in enumerate(candidates, start=1)]
    return "\n".join(lines)


def _allowed_numbers(place: dict, when_label: str) -> set[float]:
    """Every number a recommendation for this place may contain."""
    allowed = {float(MIN_TRUSTED_RATING_COUNT)}
    for value in (place.get("rating"), place.get("rating_count")):
        if value is not None:
            allowed.add(float(value))
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


def _fallback(candidates: list[dict], when_label: str) -> list[dict]:
    return [
        {**place, "reason": build_reason(place, when_label)}
        for place in rank_places(candidates)
    ]


def choose(
    turns: list[tuple[str, str]], when_label: str, places: list[dict]
) -> tuple[list[dict], str | None]:
    """Return (up to three places, each with a "reason"; a note or None).

    Places closed at the requested time never reach the LLM. The note is
    set when the rule-based fallback was used, so the page can say so.
    """
    candidates = [p for p in places if p.get("open_then") is not False][:MAX_CANDIDATES]
    if not candidates:
        return [], None

    try:
        answer = llm.generate_json(
            build_prompt(turns, when_label, candidates), SCHEMA, system=SYSTEM
        )
    except (llm.LLMUnavailable, DailyBudgetExceeded, BudgetUnreadable):
        return _fallback(candidates, when_label), FALLBACK_NOTE

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
        return _fallback(candidates, when_label), FALLBACK_NOTE
    return chosen, None
