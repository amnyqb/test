"""T05/F08: revisions must remap on evidence or refuse - never silently reattach."""

from __future__ import annotations

from ddg.anchors import make_quote, remap, resolve_quote
from ddg.models import AnchorStatus, LocationSelector, NodeKind


def sel(text: str, needle: str, idx: int = 3) -> LocationSelector:
    start = text.index(needle)
    return LocationSelector(
        document_id="d", kind=NodeKind.PARAGRAPH,
        quote=make_quote(text, start, start + len(needle)), paragraph_index=idx,
    )


def test_unique_quote_resolves():
    t = "Section 4. Base-case CAPEX is USD 12.4 million."
    assert resolve_quote(t, make_quote(t, t.index("12.4"), t.index("12.4") + 4)).resolved


def test_survives_insertion_and_renumbering():
    v1 = "Section 4. Base-case CAPEX is USD 12.4 million. Section 5 follows."
    v2 = ("Section 3. A newly inserted paragraph. "
          "Section 5. Base-case CAPEX is USD 12.4 million. Section 6 follows.")
    new, res = remap(sel(v1, "12.4"), v2)
    assert res.resolved and new is not None
    assert new.quote.exact == "12.4"


def test_deleted_evidence_becomes_unresolved_not_a_guess():
    v1 = "Base-case CAPEX is USD 12.4 million."
    v2 = "This section has been removed pending revision."
    new, res = remap(sel(v1, "12.4"), v2)
    assert new is None
    assert res.status is AnchorStatus.UNRESOLVED


def test_identical_occurrences_without_context_are_ambiguous():
    """Two indistinguishable candidates must not be resolved by a coin flip."""
    q = make_quote("x 12.4 y", 2, 6)
    bare = q.model_copy(update={"prefix": "", "suffix": ""})
    res = resolve_quote("total 12.4 and also 12.4 again", bare)
    assert res.status is AnchorStatus.AMBIGUOUS
    assert len(res.candidates) == 2


def test_positional_only_selector_refuses_to_remap():
    """A coordinate alone is exactly how an inserted row inherits a neighbour's edges."""
    s = LocationSelector(document_id="d", kind=NodeKind.PARAGRAPH, paragraph_index=3)
    new, res = remap(s, "any new text at all")
    assert new is None and res.status is AnchorStatus.UNRESOLVED
