"""
Citation verification: does a model-supplied citation actually point at text that
exists in the clause it names?

Pure functions over plain data — no database, no network, no settings. This is
where the product's central claim lives, so it must be trivially testable.

Three things are checked, independently:
  1. clause_verified — the named clause_ref exists in the named document.
  2. quote_verified  — the quoted text appears, verbatim after whitespace and
                       punctuation normalisation, in that clause.
  3. resolution      — which chunk the quote actually came from, if any.

A quote that is thematically right but not present in the source is the exact
failure this product exists to prevent. Measured against the Altura seed library,
about 6% of model citations fail one of these checks; captured examples live in
tests/fixtures/citation_regression_cases.json.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

# Resolution outcomes, most to least trustworthy.
EXACT_CLAUSE = "exact_clause"                  # quote is in the clause the model named
OTHER_CLAUSE_SAME_DOC = "other_clause_same_doc"  # quote is real, clause_ref was wrong
UNRESOLVED = "unresolved"                      # quote is not in the document at all

# Quotes shorter than this are too weak to treat as evidence of anything.
MIN_QUOTE_CHARS = 12

_PUNCTUATION_EQUIVALENTS = {
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
}


def normalize_text(text: Optional[str]) -> str:
    """
    Collapses the differences that are not meaningful in a quotation: unicode
    form, smart punctuation, whitespace runs, and case.

    Deliberately does NOT strip words or stem. A quote that differs by a word is
    a different quote, and must fail.
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    for source, target in _PUNCTUATION_EQUIVALENTS.items():
        out = out.replace(source, target)
    return re.sub(r"\s+", " ", out).strip().lower()


def quote_is_present(quote: str, content: str) -> bool:
    """True when the normalised quote appears verbatim inside the normalised content."""
    nq = normalize_text(quote)
    if len(nq) < MIN_QUOTE_CHARS:
        return False
    return nq in normalize_text(content)


def verify_citation(
    citation: Dict[str, Any],
    retrieved_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Verifies one citation against the chunks that were actually retrieved.

    `citation` needs doc_id, clause_ref, quote. Each chunk needs id, doc_id,
    clause_ref, content. Only chunks that were genuinely put in front of the
    model are considered — verifying against the whole library would let a
    citation the model could not have seen pass.
    """
    doc_id = (citation.get("doc_id") or "").strip()
    clause_ref = (citation.get("clause_ref") or "").strip()
    quote = citation.get("quote") or ""

    same_doc = [c for c in retrieved_chunks if c.get("doc_id") == doc_id]
    named_clause = [c for c in same_doc if (c.get("clause_ref") or "").strip() == clause_ref]

    clause_verified = bool(named_clause)

    holding_named = next(
        (c for c in named_clause if quote_is_present(quote, c.get("content", ""))), None
    )
    if holding_named is not None:
        return _result(True, clause_verified, EXACT_CLAUSE, holding_named, doc_id, clause_ref, quote)

    holding_other = next(
        (c for c in same_doc if quote_is_present(quote, c.get("content", ""))), None
    )
    if holding_other is not None:
        # The quoted text is real; the clause number attached to it is not.
        return _result(True, False, OTHER_CLAUSE_SAME_DOC, holding_other, doc_id, clause_ref, quote)

    fallback = named_clause[0] if named_clause else None
    return _result(False, clause_verified, UNRESOLVED, fallback, doc_id, clause_ref, quote)


def _result(quote_verified, clause_verified, resolution, chunk, doc_id, clause_ref, quote):
    return {
        "doc_id": doc_id,
        "clause_ref": clause_ref,
        "quote": quote,
        "quote_verified": quote_verified,
        "clause_verified": clause_verified,
        "resolution": resolution,
        "chunk_id": chunk.get("id") if chunk else None,
        "actual_clause_ref": (chunk.get("clause_ref") if chunk else None),
    }


def verify_all(
    citations: List[Dict[str, Any]],
    retrieved_chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Verifies every citation, preserving model order as rank."""
    verified = []
    for rank, citation in enumerate(citations or []):
        result = verify_citation(citation, retrieved_chunks)
        result["rank"] = rank
        verified.append(result)
    return verified


def downgraded_status(status: str, verified_citations: List[Dict[str, Any]]) -> str:
    """
    An answer presented as fully supported must have at least one citation whose
    quote was found in the clause it named. Otherwise it is at best partial.

    Not downgraded to gap: the retrieved evidence may still support the answer,
    and the reviewer needs to see the text to judge. It must simply stop
    claiming to be verified.
    """
    if status != "answered":
        return status
    if any(c["quote_verified"] and c["clause_verified"] for c in verified_citations):
        return "answered"
    return "partial"


def verification_note(verified_citations: List[Dict[str, Any]]) -> Optional[str]:
    """Reviewer-facing explanation of why an answer was downgraded, or None."""
    if not verified_citations:
        return "No citation was supplied for this answer."
    unresolved = [c for c in verified_citations if c["resolution"] == UNRESOLVED]
    misattributed = [c for c in verified_citations if c["resolution"] == OTHER_CLAUSE_SAME_DOC]
    if unresolved and not any(c["quote_verified"] for c in verified_citations):
        return (
            f"The quoted text was not found in {unresolved[0]['doc_id']}. "
            "The wording may be a paraphrase rather than a quotation — check the "
            "source clause before submitting."
        )
    if misattributed:
        c = misattributed[0]
        return (
            f"The quoted text is present in {c['doc_id']} but in clause "
            f"{c['actual_clause_ref'] or 'unknown'}, not the cited "
            f"{c['clause_ref'] or 'unknown'}."
        )
    return None
