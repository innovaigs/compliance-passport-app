"""
Regression tests for citation verification.

Every case comes from tests/fixtures/citation_regression_cases.json — real
failures produced by claude-sonnet-4-5 against the Altura seed library, with
expectations derived by independently searching the evidence chunks, not by
running the code under test.

These do not replace real-data acceptance. They exist so that the next change to
retrieval, chunking, or the answer prompt cannot silently reintroduce a failure
that was already observed once.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from citation_verify import (
    EXACT_CLAUSE,
    OTHER_CLAUSE_SAME_DOC,
    UNRESOLVED,
    downgraded_status,
    normalize_text,
    quote_is_present,
    verify_citation,
)

FIXTURE = Path(__file__).parent / "fixtures" / "citation_regression_cases.json"
DB = Path(__file__).resolve().parent.parent / "data" / "compliance.db"


def _cases():
    return json.loads(FIXTURE.read_text())["cases"]


def _chunks_for(doc_id):
    if not DB.exists():
        pytest.skip("evidence database not present")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return [
            {"id": r[0], "doc_id": r[1], "clause_ref": r[2], "content": r[3]}
            for r in con.execute(
                "select id, doc_id, clause_ref, content from evidence_chunks where doc_id=?",
                (doc_id,),
            )
        ]
    finally:
        con.close()


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["case_id"])
def test_known_failure_is_rejected(case):
    """Each captured failure must produce exactly the recorded verdict."""
    stored, expected = case["stored"], case["expected"]
    result = verify_citation(
        {
            "doc_id": stored["citation_document"],
            "clause_ref": stored["citation_clause"],
            "quote": stored["quote"],
        },
        _chunks_for(stored["citation_document"]),
    )
    assert result["quote_verified"] is expected["quote_verified"]
    assert result["clause_verified"] is expected["clause_verified"]
    assert result["resolution"] == expected["resolution"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["case_id"])
def test_known_failure_downgrades_the_answer(case):
    """No captured failure may leave an answer presented as fully supported."""
    stored = case["stored"]
    result = verify_citation(
        {
            "doc_id": stored["citation_document"],
            "clause_ref": stored["citation_clause"],
            "quote": stored["quote"],
        },
        _chunks_for(stored["citation_document"]),
    )
    assert downgraded_status("answered", [result]) == "partial"


def test_verbatim_quote_from_a_real_clause_passes():
    """The positive case: a genuine quotation must verify."""
    chunks = _chunks_for("ALS-POL-001")
    numbered = next(c for c in chunks if (c["clause_ref"] or "").strip())
    excerpt = " ".join(numbered["content"].split()[:14])
    result = verify_citation(
        {"doc_id": "ALS-POL-001", "clause_ref": numbered["clause_ref"], "quote": excerpt},
        chunks,
    )
    assert result["quote_verified"] is True
    assert result["clause_verified"] is True
    assert result["resolution"] == EXACT_CLAUSE
    assert downgraded_status("answered", [result]) == "answered"


def test_whitespace_and_smart_punctuation_do_not_break_a_real_quote():
    content = "Altura maintains a written  policy — reviewed annually."
    quote = "Altura maintains a written policy - reviewed annually."
    assert quote_is_present(quote, content)


def test_a_single_changed_word_fails():
    """Normalisation must not be so loose that a different claim passes."""
    content = "Altura prohibits worker-paid recruitment fees at all tiers."
    assert not quote_is_present("Altura permits worker-paid recruitment fees at all tiers.", content)


def test_trivially_short_quote_is_not_evidence():
    assert not quote_is_present("Yes.", "Yes. Altura prohibits such fees.")


def test_normalisation_is_idempotent():
    text = "  Altura’s  policy —  reviewed  "
    assert normalize_text(normalize_text(text)) == normalize_text(text)
