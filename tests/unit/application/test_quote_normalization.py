"""DH-42 — the verbatim-quote gate must not call a real quote fabricated.

``_normalize`` governs two things: whether a lens keeps its evidence as
"supports" or downgrades it to "quote-not-verified-as-substring", and whether
``CoVeVerifier`` HARD-DROPS the whole finding. In the 2026-09-08 single-lens
smoke, 6 of the 8 findings that reached CoVe died on ``cove_quote_fail`` — the
largest single loss in the chain.

A model asked to quote verbatim retypes the sentence rather than copying bytes,
so it renders the source's typography its own way. The reference bill
(Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf, 143,335 chars of paragraph text) carries 89 U+2019
apostrophes, 11 U+2013 en dashes and 4,265 final sigmas — every one a way for a
byte-strict gate to reject a genuine quote.

The gate's actual job is catching fabrication, and these tests pin BOTH halves:
real quotes survive typographic variation, invented ones still fail.
"""

from __future__ import annotations

import pytest

from leggie.application.services.cove_verifier import CoVeVerifier, _normalize

# Shapes taken from the reference bill's own text.
SOURCE = (
    "Με απόφαση του Υπουργού Δικαιοσύνης καθορίζονται οι όροι και οι "
    "προϋποθέσεις εφαρμογής της παρ’ 3 του άρθρου 12 – όπως ισχύει – "
    "για τους «δικαιούχους» της ρύθμισης."
)


class TestRealQuotesSurviveTypography:
    """Each case is a genuine quote from SOURCE, retyped the way a model does."""

    @pytest.mark.parametrize(
        ("quote", "hazard"),
        [
            ("της παρ' 3 του άρθρου 12", "U+2019 apostrophe retyped as U+0027 (89 in the bill)"),
            ("του άρθρου 12 - όπως ισχύει", "U+2013 en dash retyped as hyphen (11 in the bill)"),
            ('τους "δικαιούχους" της ρύθμισης', "« » guillemets retyped as straight quotes"),
            ("οι όροι και οι  προϋποθέσεις", "collapsed whitespace / PDF line break"),
            ("ΚΑΙ ΟΙ", "case difference (unaccented words fold cleanly)"),
            ("οι όροι​ και οι προϋποθέσεις", "zero-width space from PDF extraction"),
            ("με από­φαση του Υπουργού", "soft hyphen from PDF extraction"),
        ],
    )
    def test_quote_is_found(self, quote: str, hazard: str) -> None:
        assert _normalize(quote) in _normalize(SOURCE), (
            f"a REAL quote was rejected — {hazard}. CoVe hard-drops the whole "
            f"finding on this, so a normalizer miss destroys good analysis."
        )

    def test_final_sigma_folds_both_directions(self) -> None:
        """ "ΟΡΟΣ".lower() is "ορος" but the source reads "όρος"; a model may
        output either form mid-word."""
        assert _normalize("δικαιουχοσ") == _normalize("δικαιουχος")


class TestFabricatedQuotesStillFail:
    """The gate's reason to exist. Loosening normalization must not cost this."""

    @pytest.mark.parametrize(
        "quote",
        [
            "Με απόφαση του Υπουργού Οικονομικών καθορίζονται οι όροι",  # wrong ministry
            "καταργείται κάθε αντίθετη διάταξη",  # absent from the source
            "της παρ’ 4 του άρθρου 12",  # wrong paragraph number
            "του άρθρου 21",  # transposed article number
        ],
    )
    def test_invented_quote_is_rejected(self, quote: str) -> None:
        assert _normalize(quote) not in _normalize(SOURCE)

    def test_accents_are_not_stripped(self) -> None:
        """Deliberate limit: in Greek, accents carry meaning (πότε / ποτέ).
        Folding them would let a genuinely different word match."""
        assert _normalize("ποτε") != _normalize("πότε")


class TestKnownLimitations:
    """Recorded, not hidden. These are the cases the gate still gets wrong."""

    def test_all_caps_quote_of_accented_text_is_rejected(self) -> None:
        """Greek uppercase drops diacritics by convention (ΜΕ ΑΠΟΦΑΣΗ vs Με
        απόφαση), so lowercasing cannot restore them and the quote misses.

        NOT fixed by folding accents: that is the same knob as
        test_accents_are_not_stripped, and opening it would let πότε match ποτέ
        in every comparison, costing the gate its ability to catch a fabricated
        quote that differs by one accented word. The narrow all-caps case is
        not worth that. If it ever shows up in a real cove_quote_fail log line
        — which now records the quote — revisit with evidence.
        """
        assert _normalize("ΜΕ ΑΠΟΦΑΣΗ ΤΟΥ ΥΠΟΥΡΓΟΥ") not in _normalize(SOURCE)


class TestValidateQuoteContract:
    """validate_quote is the caller-facing gate; empty inputs must not pass."""

    @pytest.mark.parametrize(("quote", "source"), [("", SOURCE), ("οι όροι", ""), ("", "")])
    def test_empty_is_never_valid(self, quote: str, source: str) -> None:
        assert CoVeVerifier().validate_quote(quote, source) is False

    def test_typographic_variant_validates(self) -> None:
        assert CoVeVerifier().validate_quote("της παρ' 3 του άρθρου 12", SOURCE) is True
