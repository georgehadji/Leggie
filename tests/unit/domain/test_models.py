"""Tests for domain models — frozen Pydantic entities and value objects."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from leggie.domain.models import (
    IRAC,
    Article,
    Citation,
    CitationScheme,
    Confidence,
    ConfidenceGrade,
    Document,
    Event,
    EventType,
    Evidence,
    Finding,
    FindingType,
    ModelTier,
    Paragraph,
    Severity,
    SubParagraph,
    is_greek,
)


class TestConfidence:
    def test_from_score_certain(self):
        c = Confidence.from_score(0.98)
        assert c.grade == ConfidenceGrade.CERTAIN
        assert c.score == 0.98

    def test_from_score_high(self):
        c = Confidence.from_score(0.88)
        assert c.grade == ConfidenceGrade.HIGH

    def test_from_score_medium(self):
        c = Confidence.from_score(0.75)
        assert c.grade == ConfidenceGrade.MEDIUM

    def test_from_score_low(self):
        c = Confidence.from_score(0.60)
        assert c.grade == ConfidenceGrade.LOW

    def test_from_score_very_low(self):
        c = Confidence.from_score(0.30)
        assert c.grade == ConfidenceGrade.VERY_LOW

    def test_from_score_abstain(self):
        c = Confidence.from_score(0.10)
        assert c.grade == ConfidenceGrade.ABSTAIN

    def test_above_threshold(self):
        c = Confidence.from_score(0.70)
        assert c.above_threshold(0.5) is True
        assert c.above_threshold(0.8) is False

    def test_frozen(self):
        c = Confidence.from_score(0.8)
        with pytest.raises(ValidationError):
            c.score = 0.5


class TestCitation:
    def test_create_fek(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        assert cite.scheme == CitationScheme.FEK
        assert cite.identifier == "ΦΕΚ Α 137/2023"
        assert cite.resolved is False

    def test_create_celex(self):
        cite = Citation(
            scheme=CitationScheme.CELEX,
            identifier="32018L1972",
            original_text="CELEX:32018L1972",
        )
        assert cite.scheme == CitationScheme.CELEX

    def test_resolved_citation(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
            resolved=True,
            checked=True,
            resolution_evidence="verified against gov-et-laws index",
        )
        assert cite.resolved is True

    def test_identifier_not_empty(self):
        with pytest.raises(ValidationError):
            Citation(
                scheme=CitationScheme.FEK,
                identifier="   ",
                original_text="ΦΕΚ",
            )

    def test_checked_defaults_false(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        assert cite.checked is False

    def test_resolved_without_checked_is_rejected(self):
        """resolved=True must never be claimable without checked=True — a
        citation that was never checked against an index cannot be 'verified',
        only 'unverified' (see CoVeVerifier._check_citations)."""
        with pytest.raises(ValidationError):
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 137/2023",
                original_text="ΦΕΚ Α 137/2023",
                resolved=True,
                checked=False,
            )

    def test_resolved_with_checked_is_accepted(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
            resolved=True,
            checked=True,
        )
        assert cite.resolved is True
        assert cite.checked is True


class TestIRAC:
    def test_create_irac(self):
        irac = IRAC(
            issue="Does Article 3 exceed constitutional delegation limits?",
            rule="Article 43 of the Constitution limits delegation of legislative power",
            application="Article 3 grants broad rule-making authority without defined criteria",
            conclusion="Article 3 likely violates Article 43",
        )
        assert irac.issue.startswith("Does")
        assert irac.conclusion.startswith("Article 3")


class TestFinding:
    def test_create_finding(self):
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue="Test issue",
                rule="Test rule",
                application="Test application",
                conclusion="Test conclusion",
            ),
            confidence=Confidence.from_score(0.85),
            lens="constitutional",
            model="claude-sonnet-4",
        )
        assert isinstance(finding.id, UUID)
        assert finding.finding_type == FindingType.CONSTITUTIONAL
        assert finding.severity == Severity.MEDIUM
        assert finding.version == 1
        assert finding.is_admissible() is True

    def test_finding_is_admissible_below_threshold(self):
        finding = Finding(
            finding_type=FindingType.FACTUAL,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.3),
            lens="test",
            model="test-model",
        )
        assert finding.is_admissible() is False

    def test_finding_with_evidence(self):
        finding = Finding(
            finding_type=FindingType.EU_COMPLIANCE,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.9),
            lens="eu",
            model="test-model",
            evidence=[
                Evidence(
                    citation=Citation(
                        scheme=CitationScheme.CELEX,
                        identifier="32018L1972",
                        original_text="CELEX:32018L1972",
                        resolved=True,
                        checked=True,
                    ),
                    text_excerpt="Directive 2018/1972 defines...",
                    verdict="supports",
                )
            ],
        )
        assert len(finding.evidence) == 1
        citation = finding.evidence[0].citation
        assert citation is not None
        assert citation.resolved is True

    def test_finding_frozen(self):
        finding = Finding(
            finding_type=FindingType.OTHER,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        with pytest.raises(ValidationError):
            finding.lens = "changed"

    def test_article_id_defaults_empty(self):
        """Legacy/pre-fix findings have no article_id — consumers fall back to
        parsing 'Άρθρο N' out of irac.issue (see article_number_of)."""
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        assert finding.article_id == ""

    def test_article_id_can_be_set(self):
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
            article_id="15",
        )
        assert finding.article_id == "15"


class TestFrozenModelsShallowImmutability:
    """DH-34, fixed. ``model_config = {"frozen": True}`` blocks attribute
    REASSIGNMENT (see ``test_finding_frozen`` above) and used to do nothing
    at all to protect a list-VALUED field from in-place mutation:
    ``finding.evidence.append(...)`` succeeded silently on an instance the
    architecture calls immutable, and ``model_copy()``'s shallow default
    (``deep=False`` at every production call site — ``skeptic.py:246``,
    ``cove_verifier.py:479``, ``bill_analysis_flow.py:592``/``604``) handed
    the *same* list object to both the old and the new version, so mutating
    the revision corrupted its parent. That narrowed
    leggie-architecture-contract Invariant #3 from a type-enforced guarantee
    to an unenforced convention.

    Collection fields are now ``Frozen[T]`` — ``Sequence[T]`` validated to a
    tuple. Lists are still accepted at construction (no call site changed),
    but the stored value is immutable, and ``Sequence`` has no ``.append`` in
    mypy's view either, so the invariant is enforced statically as well.

    Still accepted, deliberately: ``Event.data`` remains a ``dict``. There is
    no stdlib frozen mapping for pydantic to validate to, nothing in
    ``leggie/`` mutates it, and inventing one is the kind of abstraction this
    repo's change control exists to prevent — see
    docs/ESCALATED_DEFECTS_PLAN.md §6, and
    ``test_event_data_dict_is_still_mutable_accepted_residual`` below.
    """

    def test_reassignment_and_in_place_mutation_are_both_blocked(self):
        finding = Finding(
            finding_type=FindingType.OTHER,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
            evidence=[],
        )
        # The documented, intended guard (mirrors test_finding_frozen above):
        with pytest.raises(ValidationError):
            finding.evidence = [Evidence(text_excerpt="reassigned")]
        # ...and the gap it used to leave open:
        with pytest.raises(AttributeError):
            finding.evidence.append(Evidence(text_excerpt="snuck in", verdict="supports"))
        assert len(finding.evidence) == 0

    def test_a_list_is_still_accepted_and_stored_as_a_tuple(self):
        """No-regression for the reason ``Sequence`` was chosen over
        ``tuple[...]``: every existing construction site passes a list
        literal and must keep working untouched."""
        finding = Finding(
            finding_type=FindingType.OTHER,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
            evidence=[Evidence(text_excerpt="a"), Evidence(text_excerpt="b")],
        )
        assert isinstance(finding.evidence, tuple)
        assert [e.text_excerpt for e in finding.evidence] == ["a", "b"]
        assert finding.evidence[0].text_excerpt == "a"
        assert len(finding.evidence) == 2

    def test_model_copy_can_no_longer_leak_into_its_parent(self):
        """model_copy(update=...) is still shallow — a field not named in
        ``update=`` is still the same object — but that object is now a tuple,
        so sharing it cannot corrupt anything."""
        finding = Finding(
            finding_type=FindingType.OTHER,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
            evidence=[Evidence(text_excerpt="original")],
        )
        revised = finding.model_copy(
            update={"confidence": Confidence.from_score(0.9), "version": 2}
        )
        assert revised.evidence is finding.evidence  # still shared...

        with pytest.raises(AttributeError):  # ...but no longer mutable
            revised.evidence.append(Evidence(text_excerpt="added to the revision only?"))
        assert len(finding.evidence) == 1

    def test_nested_collection_fields_are_frozen_too(self):
        """Boundary: the same gap existed on every list field of every frozen
        model, not just Finding.evidence — Document.articles,
        Article.paragraphs, Paragraph.subparagraphs."""
        document = Document(
            title="t",
            source_format="txt",
            articles=[
                Article(
                    id="1",
                    raw_text="Άρθρο 1",
                    paragraphs=[Paragraph(number="1", text="p", subparagraphs=[])],
                )
            ],
        )
        with pytest.raises(AttributeError):
            document.articles.append(Article(id="2", raw_text="Άρθρο 2"))
        with pytest.raises(AttributeError):
            document.articles[0].paragraphs.append(Paragraph(number="2", text="q"))
        with pytest.raises(AttributeError):
            document.articles[0].paragraphs[0].subparagraphs.append(
                SubParagraph(letter="α", text="s")
            )

    def test_event_data_dict_is_still_mutable_accepted_residual(self):
        """Accepted residual risk, recorded rather than hidden: Event.data is
        still a plain dict and can be mutated in place. Nothing in leggie/
        does so; freezing it has no stdlib answer pydantic can validate to."""
        event = Event(
            event_type=EventType.FINDING_CREATED,
            aggregate_id="agg-1",
            data={"finding_id": "f-1"},
        )
        event.data["tampered"] = "not part of the original event"
        assert "tampered" in event.data


class TestArticle:
    def test_create_article(self):
        article = Article(
            id="1",
            title="Test Article",
            raw_text="Άρθρο 1 test content",
            paragraphs=[
                Paragraph(number="1", text="Paragraph 1 text"),
            ],
        )
        assert article.id == "1"
        assert len(article.paragraphs) == 1
        assert article.paragraph_by_number("1") is not None
        assert article.paragraph_by_number("2") is None

    def test_article_with_subparagraphs(self):
        article = Article(
            id="2",
            raw_text="Άρθρο 2 test",
            paragraphs=[
                Paragraph(
                    number="1",
                    text="Main paragraph",
                    subparagraphs=[
                        SubParagraph(letter="α", text="Sub alpha"),
                        SubParagraph(letter="β", text="Sub beta"),
                    ],
                ),
            ],
        )
        assert len(article.paragraphs[0].subparagraphs) == 2
        assert article.paragraphs[0].subparagraphs[0].letter == "α"


class TestDocument:
    def test_create_document(self):
        doc = Document(
            title="Test Bill",
            source_format="pdf",
            articles=[
                Article(id="1", raw_text="Άρθρο 1"),
                Article(id="2", raw_text="Άρθρο 2"),
            ],
            preamble="Preamble text",
            raw_text="Full bill text",
        )
        assert len(doc.articles) == 2
        assert doc.preamble == "Preamble text"

    def test_document_auto_id(self):
        doc = Document(title="Test", source_format="txt", raw_text="text")
        assert doc.document_id is not None


class TestEvent:
    def test_create_event(self):
        event = Event(
            event_type=EventType.ANALYSIS_STARTED,
            aggregate_id="run-001",
            data={"bill_id": "bill-001"},
        )
        assert event.event_type == EventType.ANALYSIS_STARTED
        assert isinstance(event.id, UUID)

    def test_event_frozen(self):
        event = Event(
            event_type=EventType.WORKFLOW_COMPLETED,
            aggregate_id="run-001",
        )
        with pytest.raises(ValidationError):
            event.event_type = EventType.ANALYSIS_STARTED


class TestEventTypeRuntimeRepresentation:
    """DH-35, fixed. Event was the only model in this file whose
    model_config set ``use_enum_values``, which flattens an enum field to its
    plain ``str`` ``.value`` immediately after validation — so despite
    ``event_type`` being ANNOTATED ``EventType``, the value actually stored
    (and returned by every attribute access) was a bare ``str``, never an
    ``EventType`` instance. ``==``/``in``/dict-key lookups all still worked by
    accident because ``EventType`` is a ``StrEnum``, which is exactly why
    ``TestEvent.test_create_event`` above (an ``==`` check) never caught it;
    ``isinstance()`` and ``.value`` got no such safety net.

    The flag is gone. Every consumer was audited first — all use ``==``,
    ``in``, ``str()``, a dict key, or an explicit conversion, and both
    ``model_dump_json()`` and ``model_dump(mode="json")`` are byte-identical
    either way, so no serialized checkpoint or SQLite row changed shape.
    """

    def test_event_type_is_the_real_enum_member_at_runtime(self):
        event = Event(event_type=EventType.LENS_COMPLETED, aggregate_id="agg-1")
        assert type(event.event_type) is EventType
        assert isinstance(event.event_type, EventType) is True

    def test_value_attribute_access_works_as_the_annotation_promises(self):
        event = Event(event_type=EventType.WORKFLOW_FAILED, aggregate_id="agg-1")
        assert event.event_type.value == "workflow_failed"

    def test_string_input_still_validates_to_the_enum(self):
        """Boundary: events rehydrated from JSON/SQLite arrive as strings."""
        event = Event(event_type="degraded", aggregate_id="agg-1")
        assert event.event_type is EventType.DEGRADED

    def test_serialization_is_unchanged(self):
        """No-regression: the wire format must not move — existing checkpoint
        files and the events.event_type TEXT column depend on it."""
        event = Event(event_type=EventType.DEGRADED, aggregate_id="agg-1")
        assert event.model_dump(mode="json")["event_type"] == "degraded"
        assert '"event_type":"degraded"' in event.model_dump_json()
        assert str(event.event_type) == "degraded"

    def test_equality_and_membership_still_work_which_is_why_this_hid(self):
        event = Event(event_type=EventType.DEGRADED, aggregate_id="agg-1")
        assert event.event_type == EventType.DEGRADED
        assert event.event_type in {EventType.DEGRADED, EventType.BUDGET_TRIPPED}

    def test_sibling_enum_field_keeps_the_real_enum(self):
        """Contrast case, kept: Finding.model_tier uses a StrEnum too
        (ModelTier) and always kept the real enum instance -- which is what
        proved the gap was Event's own config, not a general StrEnum/Pydantic
        limitation. Event now matches it."""
        finding = Finding(
            finding_type=FindingType.OTHER,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        assert type(finding.model_tier) is ModelTier
        assert isinstance(finding.model_tier, ModelTier)


class TestIsGreek:
    """Tests for is_greek() domain helper (FX1)."""

    def test_pure_greek(self):
        text = "Αυτό είναι ένα ελληνικό κείμενο"
        assert is_greek(text) is True

    def test_pure_english(self):
        text = "This is an English text"
        assert is_greek(text) is False

    def test_mixed_dominantly_greek(self):
        text = "Article 1: Περιέχει ελληνικό κείμενο για ανάλυση"
        assert is_greek(text, min_ratio=0.4) is True

    def test_mixed_below_threshold(self):
        text = "English text with one greek word: καλημέρα"
        assert is_greek(text, min_ratio=0.5) is False

    def test_empty_string(self):
        assert is_greek("") is False

    def test_default_threshold(self):
        # Default 50% — barely Greek-sparse text
        text = "αβγδε f g h i j"
        assert is_greek(text) is False  # 5/15 ≈ 0.33 < 0.50

    def test_custom_low_threshold(self):
        text = "αβγδε f g h i j"
        assert is_greek(text, min_ratio=0.30) is True

    def test_greek_extended_range(self):
        # U+1F00–U+1FFF Greek Extended (e.g., polytonic)
        text = "\u1f00\u1f01\u1f02 — polytonic"
        assert is_greek(text, min_ratio=0.15) is True
