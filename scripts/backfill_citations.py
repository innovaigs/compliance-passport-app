"""
Backfills answer_citations for answers produced before verification existed.

Runs the real verifier against the real chunks — a migration cannot do this,
because verification needs the evidence text, not just the schema.

One compromise, stated plainly: these answers predate any record of which chunks
were retrieved for them, so verification runs against every chunk of the cited
document rather than against the six that were actually shown to the model. That
is slightly more permissive than the live path. Answers produced from now on are
verified against the retrieved set.

Usage:
    python scripts/backfill_citations.py --dry-run
    python scripts/backfill_citations.py --apply
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

import citation_verify  # noqa: E402
from database import create_sqlite_engine  # noqa: E402
from models import AnswerCitation, EvidenceChunk, RunAnswer  # noqa: E402


def backfill(apply_changes: bool) -> int:
    engine = create_sqlite_engine()
    counts = Counter()

    with Session(engine) as db:
        chunks_by_doc = {}
        for c in db.query(EvidenceChunk).all():
            chunks_by_doc.setdefault(c.doc_id, []).append(
                {"id": c.id, "doc_id": c.doc_id, "clause_ref": c.clause_ref, "content": c.content}
            )

        answers = (
            db.query(RunAnswer)
            .filter(RunAnswer.evidence_status.in_(["answered", "partial"]))
            .all()
        )

        for ans in answers:
            counts["answers_examined"] += 1
            if not ans.citation_document:
                counts["no_citation"] += 1
                continue

            candidates = chunks_by_doc.get(ans.citation_document, [])
            if not candidates:
                counts["document_not_in_library"] += 1
                continue

            result = citation_verify.verify_citation(
                {
                    "doc_id": ans.citation_document,
                    "clause_ref": ans.citation_clause,
                    "quote": ans.quote,
                },
                candidates,
            )
            result["rank"] = 0
            counts[f"resolution:{result['resolution']}"] += 1

            new_status = citation_verify.downgraded_status(ans.evidence_status, [result])
            if new_status != ans.evidence_status:
                counts["downgraded_answered_to_partial"] += 1

            if not apply_changes:
                continue

            db.query(AnswerCitation).filter_by(answer_id=ans.id).delete()
            db.add(
                AnswerCitation(
                    run_id=ans.run_id,
                    answer_id=ans.id,
                    chunk_id=result["chunk_id"],
                    doc_id=result["doc_id"],
                    clause_ref=result["clause_ref"],
                    quote=result["quote"],
                    quote_verified=result["quote_verified"],
                    clause_verified=result["clause_verified"],
                    resolution=result["resolution"],
                    actual_clause_ref=result["actual_clause_ref"],
                    rank=0,
                )
            )
            counts["citations_written"] += 1

            if new_status != ans.evidence_status:
                # Order matters: the CHECK constraint ties the confidence columns
                # to evidence_status, and both move together here.
                ans.evidence_status = new_status
                ans.verification_note = citation_verify.verification_note([result])
            elif not (result["quote_verified"] and result["clause_verified"]):
                ans.verification_note = citation_verify.verification_note([result])

        if apply_changes:
            db.commit()

    width = max(len(k) for k in counts) if counts else 10
    for key in sorted(counts):
        print(f"  {key:<{width}}  {counts[key]}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print("DRY RUN — nothing written" if args.dry_run else "APPLYING")
    raise SystemExit(backfill(apply_changes=args.apply))
