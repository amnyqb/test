"""Record types for the dependency graph.

Six records carry the whole system: SourceSnapshot, Node, QuantityContext,
Relation, CheckDefinition, CheckResult. Schema validation is an enforcement
gate (N03/S01), not documentation: a record that fails validation is rejected
and never reaches a checker.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeKind(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE_CELL = "table_cell"
    SHEET_CELL = "sheet_cell"
    PDF_REGION = "pdf_region"
    DEFINITION = "definition"
    CLAUSE = "clause"


class RelationType(str, Enum):
    REFERENCES = "REFERENCES"
    SAME_QUANTITY_AS = "SAME_QUANTITY_AS"
    VALUE_FROM = "VALUE_FROM"
    SUM_OF = "SUM_OF"
    RATIO_OF = "RATIO_OF"
    REQUIRES = "REQUIRES"
    DATE_CONSTRAINT = "DATE_CONSTRAINT"
    DEPENDS_ON = "DEPENDS_ON"


#: Relations a deterministic checker may act on. DEPENDS_ON is deliberately
#: absent: it supports impact review but is never automatically executable.
EXECUTABLE_RELATIONS = frozenset({
    RelationType.SAME_QUANTITY_AS,
    RelationType.VALUE_FROM,
    RelationType.SUM_OF,
    RelationType.RATIO_OF,
    RelationType.REQUIRES,
    RelationType.DATE_CONSTRAINT,
})

#: Relations whose formalisation a domain reviewer must approve before any
#: solver runs, even once the relation itself is accepted.
REVIEW_BEFORE_EXECUTION = frozenset({
    RelationType.REQUIRES,
    RelationType.DATE_CONSTRAINT,
})


class ReviewState(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    AMENDED = "AMENDED"
    STALE = "STALE"
    UNRESOLVED = "UNRESOLVED"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_CHECKED = "NOT_CHECKED"
    ERROR = "ERROR"


class ExtractionMethod(str, Enum):
    EXPLICIT_FORMULA = "explicit_formula"
    EXPLICIT_REFERENCE = "explicit_reference"
    LLM_PROPOSED = "llm_proposed"
    HUMAN_AUTHORED = "human_authored"


class AnchorStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class TextQuote(BaseModel):
    """W3C Web Annotation TextQuoteSelector.

    The quote is the anchor that survives renumbering and row insertion; the
    positional selector alone never identifies a node.
    """

    model_config = ConfigDict(frozen=True)

    exact: str = Field(min_length=1)
    prefix: str = ""
    suffix: str = ""


class LocationSelector(BaseModel):
    """Where a node lives, expressed three independent ways.

    Resolution is quote-first, position-second, structure-as-tiebreak. A node
    identified only by coordinate can silently migrate to different content on
    revision, which is exactly the failure T05 tests for.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str
    kind: NodeKind
    quote: Optional[TextQuote] = None
    # Positional: paragraph index (DOCX), page (PDF), or A1 address (XLSX).
    paragraph_index: Optional[int] = None
    page_number: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    sheet_name: Optional[str] = None
    cell_ref: Optional[str] = None
    # W3C TextPositionSelector over the document's flat text (DOCX). Used only
    # to find which node owns a re-anchored span, never to identify a node.
    text_start: Optional[int] = None
    text_end: Optional[int] = None
    # Structural: heading path, table header path, defined name, list numbering.
    structural_path: tuple[str, ...] = ()

    def describe(self) -> str:
        if self.cell_ref:
            return f"{self.sheet_name}!{self.cell_ref}"
        if self.page_number is not None:
            return f"page {self.page_number}"
        if self.paragraph_index is not None:
            return f"paragraph {self.paragraph_index}"
        return self.document_id


class SourceSnapshot(BaseModel):
    """An immutable copy of one source file at one version."""

    model_config = ConfigDict(frozen=True)

    package_id: str
    document_id: str
    version_hash: str = Field(min_length=64, max_length=64)
    media_type: str
    import_time: dt.datetime
    permissions: str = "read-only"
    parser_version: str
    blob_path: str
    # Content the parser could not read. Never silently dropped (F01).
    unsupported_objects: list[str] = Field(default_factory=list)


class QuantityContext(BaseModel):
    """What a number actually means.

    Every field is optional and a missing one stays ``None``. A guessed period
    or currency is how a checker produces a persuasive false alarm, so absence
    is recorded rather than inferred.
    """

    model_config = ConfigDict(frozen=True)

    entity: Optional[str] = None
    metric: Optional[str] = None
    period: Optional[str] = None
    scenario: Optional[str] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    scale: Optional[str] = None
    sign: Optional[str] = None
    rounding_policy: Optional[str] = None

    def missing_fields(self, required: tuple[str, ...]) -> list[str]:
        return [f for f in required if getattr(self, f) is None]


class Node(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    source_version: str
    kind: NodeKind
    selector: LocationSelector
    evidence_text: str
    normalized_value: Optional[Decimal] = None
    raw_value: Optional[str] = None
    context: QuantityContext = Field(default_factory=QuantityContext)
    # True when the value came from a workbook's cached formula result rather
    # than a fresh recalculation. openpyxl does not evaluate formulas [ref 47].
    is_cached_value: bool = False
    formula: Optional[str] = None

    @field_validator("evidence_text")
    @classmethod
    def _evidence_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a node must carry non-empty evidence text")
        return v


class Endpoint(BaseModel):
    """A role-labelled participant in a relation.

    Roles keep multi-input relations meaningful: SUM_OF has one ``total`` and
    many ``operand`` endpoints, never a bag of pairwise arrows.
    """

    model_config = ConfigDict(frozen=True)

    role: str
    node_id: str


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    source_version: str
    quote: TextQuote


class Relation(BaseModel):
    model_config = ConfigDict(frozen=True)

    relation_id: str
    type: RelationType
    endpoints: tuple[Endpoint, ...]
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    extraction_method: ExtractionMethod
    model_version: Optional[str] = None
    prompt_version: Optional[str] = None
    review_state: ReviewState = ReviewState.PROPOSED
    valid_source_versions: tuple[str, ...] = ()
    rule_approved: bool = False
    note: Optional[str] = None

    @field_validator("endpoints")
    @classmethod
    def _at_least_two(cls, v: tuple[Endpoint, ...]) -> tuple[Endpoint, ...]:
        if len(v) < 2:
            raise ValueError("a relation needs at least two endpoints")
        return v

    def role(self, role: str) -> list[str]:
        return [e.node_id for e in self.endpoints if e.role == role]


class CheckDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_id: str
    relation_id: str
    expression: str
    operand_node_ids: tuple[str, ...]
    preconditions: tuple[str, ...] = ()
    tolerance: Decimal = Decimal("0")
    checker_version: str
    expected_result_type: str = "boolean"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_id: str
    run_id: str
    status: CheckStatus
    detail: str
    input_hashes: tuple[str, ...] = ()
    computation_trace: tuple[str, ...] = ()
    evidence: tuple[EvidenceSpan, ...] = ()
    computed_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    stale: bool = False

    def as_row(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
