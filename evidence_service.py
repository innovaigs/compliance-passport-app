"""
Evidence Library processing: document parsing, YAML frontmatter extraction,
clause-aware markdown chunking, and dual FTS5 BM25 / Pure-Python IDF search.
"""

import os
import re
import math
import yaml
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy import text, select, delete
from sqlalchemy.orm import Session

import models


# Function words plus questionnaire boilerplate. Domain nouns (policy, audit, code,
# conduct, security, labour...) are deliberately NOT here — they carry the signal.
RETRIEVAL_STOP_WORDS = {
    "the", "and", "are", "for", "you", "your", "yours", "our", "its", "their",
    "does", "did", "has", "have", "had", "was", "were", "been", "being",
    "any", "all", "with", "from", "that", "this", "these", "those", "which",
    "what", "when", "where", "who", "whom", "how", "why", "not", "may", "can",
    "will", "would", "should", "must", "please", "provide", "confirm", "state",
    "describe", "detail", "details", "attach", "list", "explain", "indicate",
    "organisation", "organization", "organisations", "organizations",
    "company", "companies", "supplier", "suppliers", "vendor", "vendors",
    "yes", "no", "n/a", "date", "dates", "own", "per", "via", "such", "each",
}


def init_fts5(db: Session):
    """Initializes FTS5 virtual table for evidence chunks if supported."""
    try:
        db.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS evidence_chunks_fts "
            "USING fts5(doc_id, doc_title, heading, clause_ref, content)"
        ))
        db.commit()
    except Exception as e:
        print(f"Warning: FTS5 initialization failed/unavailable: {e}")
        db.rollback()


def parse_and_chunk_document(filename: str, file_bytes: bytes) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Parses a markdown or text file, extracts YAML frontmatter, and chunks content
    by markdown headings and numbered-clause boundaries (e.g., '3.2 ').
    """
    text_content = file_bytes.decode("utf-8", errors="replace")

    frontmatter: Dict[str, Any] = {}
    body = text_content

    if text_content.startswith("---"):
        parts = text_content.split("---", 2)
        if len(parts) >= 3:
            try:
                parsed = yaml.safe_load(parts[1])
                if isinstance(parsed, dict):
                    frontmatter = parsed
            except Exception as e:
                print(f"Error parsing frontmatter in {filename}: {e}")
            body = parts[2]

    # Derive doc_id
    doc_id = str(frontmatter.get("doc_id") or "").strip()
    if not doc_id:
        stem = os.path.splitext(os.path.basename(filename))[0]
        doc_id = stem.split("_")[0]

    # Derive title
    title = str(frontmatter.get("title") or "").strip()
    if not title:
        first_head = re.search(r"^#+\s+(.*)$", body, re.MULTILINE)
        title = first_head.group(1).strip() if first_head else os.path.basename(filename)

    doc_metadata = {
        "doc_id": doc_id,
        "title": title,
        "owner": str(frontmatter.get("owner") or "").strip() or None,
        "version": str(frontmatter.get("version") or "").strip() or None,
        "effective_date": str(frontmatter.get("effective_date") or "").strip() or None,
        "next_review": str(frontmatter.get("next_review") or "").strip() or None,
        "file_size": len(file_bytes),
        "file_type": os.path.splitext(filename)[1].lstrip(".").lower() or "md",
    }

    # Chunking logic
    chunks: List[Dict[str, Any]] = []

    # Split by markdown headings
    heading_blocks = re.split(r"\n(?=#\s+|\n##\s+|\n###\s+|\n####\s+)", body)

    for block in heading_blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        heading = ""
        if lines[0].startswith("#"):
            heading = re.sub(r"^#+\s*", "", lines[0]).strip()
            body_lines = lines[1:]
        else:
            body_lines = lines

        block_text = "\n".join(body_lines).strip()
        if not block_text:
            continue

        # Split at numbered clause boundaries if needed or for numbered clauses
        clause_blocks = re.split(r"\n(?=\d+\.\d+\s+|\d+\.\d+\.\d+\s+)", block_text)
        for cb in clause_blocks:
            cb_str = cb.strip()
            if not cb_str:
                continue

            m = re.match(r"^(\d+\.\d+(?:\.\d+)?)", cb_str)
            clause_ref = m.group(1) if m else ""

            chunks.append({
                "doc_id": doc_id,
                "doc_title": title,
                "heading": heading,
                "clause_ref": clause_ref,
                "content": cb_str,
            })

    return doc_metadata, chunks


def store_document_and_chunks(db: Session, doc_metadata: Dict[str, Any], chunks: List[Dict[str, Any]], file_path: str = "") -> models.EvidenceDocument:
    """Persists document metadata, chunks, and updates FTS index."""
    doc_id = doc_metadata["doc_id"]

    # Delete existing doc with same doc_id if present
    existing_doc = db.scalars(select(models.EvidenceDocument).filter_by(doc_id=doc_id)).first()
    if existing_doc:
        db.delete(existing_doc)

    # Delete existing chunks with same doc_id
    existing_chunks = db.scalars(select(models.EvidenceChunk).filter_by(doc_id=doc_id)).all()
    for ch in existing_chunks:
        db.delete(ch)

    try:
        db.execute(text("DELETE FROM evidence_chunks_fts WHERE doc_id = :doc_id"), {"doc_id": doc_id})
    except Exception:
        pass

    db.commit()

    # Create new document record
    doc_rec = models.EvidenceDocument(
        doc_id=doc_metadata["doc_id"],
        title=doc_metadata["title"],
        owner=doc_metadata.get("owner"),
        version=doc_metadata.get("version"),
        effective_date=doc_metadata.get("effective_date"),
        next_review=doc_metadata.get("next_review"),
        file_path=file_path,
        file_size=doc_metadata.get("file_size", 0),
        file_type=doc_metadata.get("file_type", "md"),
        status="active",
    )
    db.add(doc_rec)
    db.flush()

    for ch in chunks:
        chunk_rec = models.EvidenceChunk(
            doc_id=doc_id,
            doc_title=doc_metadata["title"],
            heading=ch.get("heading", ""),
            clause_ref=ch.get("clause_ref", ""),
            content=ch["content"],
        )
        db.add(chunk_rec)
        db.flush()

        try:
            db.execute(
                text(
                    "INSERT INTO evidence_chunks_fts(doc_id, doc_title, heading, clause_ref, content) "
                    "VALUES(:doc_id, :doc_title, :heading, :clause_ref, :content)"
                ),
                {
                    "doc_id": doc_id,
                    "doc_title": doc_metadata["title"],
                    "heading": ch.get("heading", ""),
                    "clause_ref": ch.get("clause_ref", ""),
                    "content": ch["content"],
                }
            )
        except Exception as e:
            print(f"FTS insert error for chunk: {e}")

    db.commit()
    db.refresh(doc_rec)
    return doc_rec


def search_evidence_chunks(db: Session, query: str, k: int = 6) -> List[Dict[str, Any]]:
    """
    Searches evidence chunks using SQLite FTS5 with BM25 + 0.15 heading boost,
    falling back to pure-Python IDF token overlap if FTS5 is unavailable. Never 500s.
    """
    if not query or not query.strip():
        return []

    q_terms = [t.lower() for t in re.findall(r"\w+", query)]
    if not q_terms:
        return []

    # Buyer questions are mostly boilerplate ("Does your organization have a written
    # ... approved by senior management?"). Matching those terms with OR lets BM25 rank
    # any short, dense chunk above the clause that actually answers the question, so
    # strip them before building the MATCH pattern. Keep the raw terms if nothing survives.
    content_terms = [t for t in q_terms if t not in RETRIEVAL_STOP_WORDS and len(t) > 2]
    q_terms = content_terms or q_terms

    # Attempt FTS5 Search
    try:
        # Sanitize query for FTS5
        fts_pattern = " OR ".join([f'"{t}"' for t in q_terms])
        sql = text(
            "SELECT doc_id, doc_title, heading, clause_ref, content, bm25(evidence_chunks_fts) as rank "
            "FROM evidence_chunks_fts WHERE evidence_chunks_fts MATCH :pattern ORDER BY rank ASC LIMIT 50"
        )
        rows = db.execute(sql, {"pattern": fts_pattern}).fetchall()

        results = []
        for row in rows:
            doc_id, doc_title, heading, clause_ref, content, bm25_val = row
            # Negative BM25 score in SQLite: lower is better, so abs(bm25) is positive relevance
            raw_score = abs(float(bm25_val))
            boost = 0.15 if any(t in (heading or "").lower() for t in q_terms) else 0.0
            final_score = raw_score + boost

            results.append({
                "doc_id": doc_id,
                "doc_title": doc_title,
                "heading": heading,
                "clause_ref": clause_ref,
                "content": content,
                "score": round(final_score, 4),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        if results:
            return results[:k]

    except Exception as fts_err:
        print(f"FTS5 search failed/fallback triggered: {fts_err}")

    # Fallback to Pure-Python IDF Scorer
    try:
        chunks = db.scalars(select(models.EvidenceChunk)).all()
        if not chunks:
            return []

        N = len(chunks)
        df = {}
        for term in q_terms:
            df[term] = sum(
                1 for ch in chunks if term in ch.content.lower() or term in (ch.heading or "").lower()
            )

        scored = []
        for ch in chunks:
            text_haystack = f"{ch.content} {ch.heading} {ch.doc_title}".lower()
            score = 0.0
            for term in q_terms:
                tf = text_haystack.count(term)
                if tf > 0:
                    idf = math.log((N + 1) / (df[term] + 1)) + 1.0
                    score += tf * idf

            if any(t in (ch.heading or "").lower() for t in q_terms):
                score += 0.15

            if score > 0:
                scored.append({
                    "doc_id": ch.doc_id,
                    "doc_title": ch.doc_title,
                    "heading": ch.heading,
                    "clause_ref": ch.clause_ref,
                    "content": ch.content,
                    "score": round(score, 4),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    except Exception as err:
        print(f"Fallback search error: {err}")
        return []
