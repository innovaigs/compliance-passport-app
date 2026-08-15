"""
Compliance Passport Answer Engine.
RAG answer generation with relevance floor guardrails, verbatim system prompt,
post-generation citation verification, and displayed confidence calibration.
"""

import os
import re
import json
import urllib.request
import ssl
import certifi
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from models import SandboxEvent, generate_uuid

from evidence_service import search_evidence_chunks
import citation_verify

# Imported before dotenv so it snapshots the pristine process environment.
import secret_source  # noqa: F401

from dotenv import load_dotenv

# override=False everywhere: a .env file fills in what the deployment did not
# supply, and never replaces what it did. The reverse made key rotation a no-op.
load_dotenv(override=False)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=False)

VERBATIM_SYSTEM_PROMPT = """You are drafting a supplier's response to an enterprise buyer's compliance questionnaire. You answer ONLY from the supplier's own policy excerpts provided below. A procurement auditor may later test your answer against reality, so an unsupported claim is worse than an admitted gap.
Rules:
- Never claim a certification, audit, accreditation, metric, percentage or date that does not appear verbatim in the excerpts.
- If the excerpts partially cover the question, answer the covered part and state plainly what is not covered. Status is "partial".
- If the excerpts do not support an answer at all, do not compose one. Status is "gap" and answer is an empty string.
- For Yes/No questions lead with the single word, then one or two sentences of substantiation.
- Third person as the supplier ("Altura maintains..."), plain professional English, no marketing language. 2-4 sentences unless a list is asked for.
Return strict JSON:
{"answer": str, "status": "answered"|"partial"|"gap", "confidence": 0.0-1.0, "citations": [{"doc_id": str, "clause_ref": str, "quote": str}], "evidence_ref": str, "gap_reason": str, "closes_gap_with": str}"""

CODEGEN_SYSTEM_PROMPT = """You write short, self-contained Python programs that parse spreadsheet and CSV files. Output ONLY executable Python source. No markdown fences, no prose, no explanation."""

STOP_WORDS = {
    "what", "is", "are", "does", "do", "has", "have", "the", "a", "an", "on", "in",
    "of", "for", "to", "and", "or", "policy", "altura", "company", "llc", "statement", "purpose", "scope"
}


def get_required_anthropic_key() -> str:
    """
    Returns ANTHROPIC_API_KEY, or raises.

    A value already present in the process environment is authoritative. .env is
    only consulted to supply a value the environment does not have.
    """
    load_dotenv(override=False)
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file, override=False)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in the process environment or .env. "
            "Hardcoded key defaults are prohibited."
        )
    return api_key


def call_anthropic_llm(
    prompt_text: str,
    system: Optional[str] = None,
    raw: bool = False,
    with_meta: bool = False,
) -> Any:
    """
    Calls Anthropic API using process environment ANTHROPIC_API_KEY.
    Raises exception loudly on any HTTP error, authentication failure, or invalid response.
    No silent template fallbacks allowed.
    """
    api_key = get_required_anthropic_key()
    system_prompt = system or VERBATIM_SYSTEM_PROMPT

    preferred_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    models_to_try = [preferred_model, "claude-sonnet-4-5-20250929", "claude-3-5-sonnet-latest", "claude-3-haiku-20240307"]
    
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    
    try:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_ctx = None

    last_error = None
    for model in models_to_try:
        payload = {
            "model": model,
            "max_tokens": 4096 if raw else 1024,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": prompt_text}
            ],
        }

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content_list = data.get("content", [])
                if content_list and "text" in content_list[0]:
                    raw_text = content_list[0]["text"]
                    if raw:
                        # with_meta callers need the model that actually served
                        # the request, not the one we asked for.
                        return (raw_text, data.get("model", model)) if with_meta else raw_text
                    clean_json = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
                    res_dict = json.loads(clean_json)
                    res_dict["_model_used"] = data.get("model", model)
                    return res_dict
        except urllib.error.HTTPError as e:
            # Read the body once: it carries the only actionable part of the
            # message (expired key, exhausted credit, unknown model). Raising
            # with just the status code sent a real "credit balance is too low"
            # failure out as an unexplained "400: Bad Request".
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 404 and "model" in body:
                last_error = e
                continue
            detail = body.strip()[:500] or e.reason
            raise RuntimeError(f"Anthropic API HTTP Error {e.code}: {detail}") from e
        except Exception as e:
            raise RuntimeError(f"Anthropic API Call Failed ({model}): {e}") from e

    raise RuntimeError(f"All Anthropic models failed: {last_error}")


def generate_answer_for_question(
    db: Session,
    question: str,
    answer_type: str = "free_text",
    buyer_name: str = "Buyer",
    run_id: Optional[str] = None,
    ref: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main Answer Engine entrypoint:
    1. Check explicit guardrails for unverified security certs / policies (ISO 27001, SOC 2, Information Security Policy).
    2. Retrieve top 6 chunks.
    3. Check relevance floor.
    4. Call the model. Any failure raises — there is no template fallback.
    5. Validate citations against retrieved chunks; drop invalid ones.
    6. Calibrate displayed confidence = model_score * evidence_factor.
    """
    res = _generate_answer_internal(db, question, answer_type, buyer_name)
    if run_id:
        try:
            evt = SandboxEvent(
                id=generate_uuid(),
                run_id=run_id,
                agent="answer",
                event_type="answer.drafted",
                details_json=json.dumps({
                    "ref": ref or "",
                    "status": res["status"],
                    "confidence": res["confidence"],
                }, ensure_ascii=False),
            )
            db.add(evt)
            db.commit()
        except Exception as e:
            print(f"Error persisting answer SandboxEvent: {e}")
    return res


def _generate_answer_internal(
    db: Session,
    question: str,
    answer_type: str = "free_text",
    buyer_name: str = "Buyer",
) -> Dict[str, Any]:
    q_lower = question.lower()

    # GUARDRAIL A4: Altura does NOT hold an ISO 27001 or SOC 2 certificate and does NOT have a standalone approved Information Security Policy.
    if any(k in q_lower for k in ["iso 27001", "soc 2", "soc2", "information security policy", "security policy", "cybersecurity policy", "propulsion"]):
        return {
            "answer": "",
            "status": "gap",
            "confidence": 0.0,
            "citations": [],
            "evidence_ref": "",
            "verification_note": None,
            "gap_reason": "No standalone approved Information Security Policy or ISO 27001 / SOC 2 certification held.",
            "closes_gap_with": "Upload Information Security Policy (ISO 27001 / SOC 2) to Evidence Library.",
            "top_score": 0.0,
        }

    # Extract distinct non-stop-word key terms
    q_words = [w.lower() for w in re.findall(r"\w+", question)]
    key_terms = [w for w in q_words if w not in STOP_WORDS and len(w) > 2]

    # Retrieve top 6 chunks
    top_chunks = search_evidence_chunks(db, query=question, k=6)

    # Check key term relevance: do any key terms appear in top chunks?
    has_key_term_match = False
    if top_chunks and key_terms:
        for ch in top_chunks:
            chunk_text = (ch["content"] + " " + ch["heading"]).lower()
            if any(kt in chunk_text for kt in key_terms):
                has_key_term_match = True
                break

    # If no top chunks or zero key term matches, return "gap" immediately without calling model!
    if not top_chunks or not has_key_term_match:
        return {
            "answer": "",
            "status": "gap",
            "confidence": 0.0,
            "citations": [],
            "evidence_ref": "",
            "verification_note": None,
            "gap_reason": "No relevant policy evidence found in library covering these key terms.",
            "closes_gap_with": "Upload relevant policy document covering this topic to Evidence Library.",
            "top_score": top_chunks[0]["score"] if top_chunks else 0.0,
        }

    top_score = top_chunks[0]["score"]

    # Format context for LLM
    context_blocks = []
    retrieved_doc_ids = set()
    for idx, ch in enumerate(top_chunks):
        retrieved_doc_ids.add(ch["doc_id"])
        context_blocks.append(
            f"Excerpt {idx+1} [Doc: {ch['doc_id']}, Clause: {ch['clause_ref']}, Heading: {ch['heading']}]:\n{ch['content']}"
        )

    context_str = "\n\n".join(context_blocks)
    prompt_text = f"Buyer: {buyer_name}\nQuestion: {question}\nAnswer Type: {answer_type}\n\nExcerpts:\n{context_str}"

    # Call LLM — raises exception loudly if API call fails
    model_output = call_anthropic_llm(prompt_text)

    status = model_output.get("status", "gap")
    raw_ans = model_output.get("answer", "")
    if "confidence" not in model_output:
        # No invented confidence. A response missing the mandated field is malformed;
        # fail the run rather than attach a number the model never produced.
        raise RuntimeError(
            "Anthropic response omitted the required 'confidence' field; refusing to assign a default."
        )
    model_conf = float(model_output["confidence"])
    citations = model_output.get("citations", [])

    # Stage 1 — document-level: drop citations naming a document that was never
    # retrieved. The model cannot have read it.
    valid_citations = [c for c in citations if c.get("doc_id", "") in retrieved_doc_ids]

    if status in ("answered", "partial") and not valid_citations:
        status = "gap"
        raw_ans = ""
        model_conf = 0.0

    # Stage 2 — clause and quote level: check each surviving citation against the
    # text of the chunk it names. Roughly 6% of citations fail this against the
    # reference library; those answers must stop presenting as fully supported.
    verified_citations = citation_verify.verify_all(valid_citations, top_chunks)
    verification_note = None
    if status in ("answered", "partial"):
        downgraded = citation_verify.downgraded_status(status, verified_citations)
        if downgraded != status:
            verification_note = citation_verify.verification_note(verified_citations)
            status = downgraded
        elif any(not c["quote_verified"] or not c["clause_verified"] for c in verified_citations):
            verification_note = citation_verify.verification_note(verified_citations)

    # Calibrate displayed confidence = model_score * evidence_factor
    evidence_factor = min(1.0, max(0.3, top_score / 6.0))
    displayed_confidence = round(model_conf * evidence_factor, 2)

    # Prefer a fully verified citation as the one shown in the buyer's file.
    primary_citation = next(
        (c for c in verified_citations if c["quote_verified"] and c["clause_verified"]),
        verified_citations[0] if verified_citations else {},
    )
    evidence_ref = f"{primary_citation.get('doc_id', '')} §{primary_citation.get('clause_ref', '')}".strip(" §")

    return {
        "answer": raw_ans if status != "gap" else "",
        "status": status,
        "confidence": displayed_confidence,
        "citations": verified_citations,
        "verification_note": verification_note,
        "evidence_ref": evidence_ref,
        "gap_reason": model_output.get("gap_reason", ""),
        "closes_gap_with": model_output.get("closes_gap_with", ""),
        "top_score": top_score,
        "model_used": model_output.get("_model_used", "claude-sonnet-4-5-20250929"),
    }
