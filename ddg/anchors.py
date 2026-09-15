"""Anchor resolution and revision remapping.

The whole lifecycle story turns on this module. A node identified only by its
coordinate inherits whatever content later occupies that coordinate, which is
how an inserted row silently acquires its neighbour's dependencies. So every
node carries a quote as well as a position, resolution runs quote-first, and
anything the evidence cannot settle becomes UNRESOLVED rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ddg.models import AnchorStatus, LocationSelector, TextQuote

#: How much context either side of the quote is used to disambiguate.
CONTEXT_CHARS = 32


@dataclass(frozen=True)
class Resolution:
    status: AnchorStatus
    offset: int | None = None
    reason: str = ""
    candidates: tuple[int, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.status is AnchorStatus.RESOLVED


def make_quote(text: str, start: int, end: int) -> TextQuote:
    """Build a quote selector for ``text[start:end]`` with surrounding context."""
    return TextQuote(
        exact=text[start:end],
        prefix=text[max(0, start - CONTEXT_CHARS):start],
        suffix=text[end:end + CONTEXT_CHARS],
    )


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    out: list[int] = []
    i = haystack.find(needle)
    while i != -1:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _context_score(text: str, offset: int, quote: TextQuote) -> int:
    """How many context characters agree on each side of a candidate offset."""
    score = 0
    if quote.prefix:
        before = text[max(0, offset - len(quote.prefix)):offset]
        score += _common_suffix_len(before, quote.prefix)
    if quote.suffix:
        after = text[offset + len(quote.exact):offset + len(quote.exact) + len(quote.suffix)]
        score += _common_prefix_len(after, quote.suffix)
    return score


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n


def resolve_quote(text: str, quote: TextQuote) -> Resolution:
    """Locate a quote in a document version.

    Unique match wins outright. Several matches are separated by context, and
    only a strictly best context score counts as resolved - a tie is ambiguous,
    because picking either one would be a coin flip presented as a fact.
    """
    hits = _all_occurrences(text, quote.exact)
    if not hits:
        return Resolution(AnchorStatus.UNRESOLVED, reason="quote text no longer present")
    if len(hits) == 1:
        return Resolution(AnchorStatus.RESOLVED, offset=hits[0], reason="unique quote match")

    scored = sorted(((_context_score(text, h, quote), h) for h in hits), reverse=True)
    best_score, best_offset = scored[0]
    runner_up = scored[1][0]
    if best_score > runner_up and best_score > 0:
        return Resolution(
            AnchorStatus.RESOLVED,
            offset=best_offset,
            reason=f"disambiguated by surrounding context ({best_score} chars)",
            candidates=tuple(h for _, h in scored),
        )
    return Resolution(
        AnchorStatus.AMBIGUOUS,
        reason=f"{len(hits)} identical occurrences, context does not separate them",
        candidates=tuple(h for _, h in scored),
    )


def remap(
    selector: LocationSelector,
    new_text: str,
    *,
    new_paragraph_index: int | None = None,
) -> tuple[LocationSelector | None, Resolution]:
    """Carry a selector onto a new source version.

    Returns ``(None, resolution)`` whenever the evidence does not support the
    mapping. The caller marks the node UNRESOLVED; it must never fall back to
    the old coordinate.
    """
    if selector.quote is None:
        return None, Resolution(
            AnchorStatus.UNRESOLVED,
            reason="no quote anchor; a positional selector alone cannot be remapped safely",
        )

    res = resolve_quote(new_text, selector.quote)
    if not res.resolved:
        return None, res

    assert res.offset is not None
    updated = selector.model_copy(update={
        "quote": make_quote(new_text, res.offset, res.offset + len(selector.quote.exact)),
        "paragraph_index": new_paragraph_index if new_paragraph_index is not None
        else selector.paragraph_index,
    })
    return updated, res


#: Minimum context agreement, per side, before an edited span may be followed.
MIN_CONTEXT = 8


def resolve_by_context(text: str, quote: TextQuote) -> Resolution:
    """Follow a quote whose exact text was edited, using the context around it.

    ``resolve_quote`` answers "is this text still here?". This answers "is the
    place it used to occupy still here?": if the text before and after the old
    quote both survive with something new between them, the span between is the
    edited successor. It is how a changed value (12.4 -> 13.6) stays attached to
    its sentence. A document boundary may stand in for one side; otherwise both
    sides must agree for at least ``MIN_CONTEXT`` characters, and only a strictly
    best candidate resolves. ``Resolution.offset`` is the span start and
    ``Resolution.candidates`` holds ``(start, end)`` of the chosen span first.
    """
    pre = quote.prefix[-16:]
    suf = quote.suffix[:16]
    at_start, at_end = quote.prefix == "", quote.suffix == ""
    if (len(pre) < MIN_CONTEXT and not at_start) or (len(suf) < MIN_CONTEXT and not at_end):
        return Resolution(AnchorStatus.UNRESOLVED,
                          reason="exact text gone and too little context to follow the edit")

    window = 2 * len(quote.exact) + 64
    starts = [0] if at_start else [i + len(pre) for i in _all_occurrences(text, pre)]
    spans: list[tuple[int, int]] = []
    for s in starts:
        if at_end:
            e = len(text)
        else:
            e = text.find(suf, s)
            if e == -1:
                continue
        if 0 < e - s <= window:
            spans.append((s, e))

    if not spans:
        return Resolution(AnchorStatus.UNRESOLVED,
                          reason="neither the text nor its surrounding context survives")

    def score(span: tuple[int, int]) -> int:
        s, e = span
        return (_common_suffix_len(text[max(0, s - len(quote.prefix)):s], quote.prefix)
                + _common_prefix_len(text[e:e + len(quote.suffix)], quote.suffix))

    ranked = sorted(spans, key=score, reverse=True)
    if len(ranked) == 1 or score(ranked[0]) > score(ranked[1]):
        s, e = ranked[0]
        return Resolution(
            AnchorStatus.RESOLVED, offset=s,
            reason=f"text edited in place; context agrees ({score(ranked[0])} chars)",
            candidates=(s, e),
        )
    return Resolution(
        AnchorStatus.AMBIGUOUS,
        reason=f"{len(spans)} places match the surrounding context equally",
        candidates=tuple(s for s, _ in ranked),
    )
