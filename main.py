"""
Compliance Passport — Single-Process FastAPI Application Entrypoint
"""

import ast
import time
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel
from fastapi import FastAPI, Response, UploadFile, File, Form, Query, Depends, status, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from config import get_settings, validate_and_bootstrap_storage
from database import init_db, get_db, create_sqlite_engine
from models import (
    EvidenceDocument,
    EvidenceChunk,
    QuestionnaireRun,
    QuestionnaireFile,
    RunQuestion,
    RunAnswer,
    GapRecord,
    SandboxEvent,
    ExportRecord,
)
from evidence_service import (
    init_fts5,
    parse_and_chunk_document,
    store_document_and_chunks,
    search_evidence_chunks,
)
from answer_engine import generate_answer_for_question, call_anthropic_llm, CODEGEN_SYSTEM_PROMPT, STOP_WORDS
from daytona_service import SandboxRun, ingest_questionnaire, export_filled, extract_python_code


class AnswerRequest(BaseModel):
    question: str
    answer_type: Optional[str] = "free_text"
    buyer_name: Optional[str] = "Buyer"


def mock_sandbox_allowed() -> bool:
    """Mock execution is opt-in only. Without it, a Daytona failure fails the run."""
    return os.getenv("COMPLIANCE_ALLOW_MOCK_SANDBOX", "").strip() == "1"


class MockSandbox:
    """
    Non-isolated stand-in used ONLY when COMPLIANCE_ALLOW_MOCK_SANDBOX=1.

    It executes nothing. It must never be able to produce an artefact that a user
    could mistake for a real filled questionnaire, so downloads raise.
    """
    def __init__(self):
        self.id = "mock-no-isolation"

        def _refuse_download(*a, **k):
            raise RuntimeError(
                "Mock sandbox cannot produce a questionnaire file. "
                "Set DAYTONA_API_KEY and re-run to export."
            )

        self.process = type("Proc", (), {
            "exec": lambda cmd: type("R", (), {"exit_code": 0, "result": ""})(),
            "code_run": lambda code: type("R", (), {"exit_code": 0, "result": ""})()
        })()
        self.fs = type("Fs", (), {
            "upload_file": lambda *a, **k: None,
            "download_file": _refuse_download,
        })()


class SafeSandboxRun:
    """Wrapper over SandboxRun that uses real Daytona API when DAYTONA_API_KEY is present."""
    def __init__(self, label="compliance-passport", on_event: Optional[Callable[[dict], None]] = None):
        self.label = label
        self.log = []
        self.on_event = on_event
        self._real_run = None
        self.sandbox = None
        self.degraded = False

    def __enter__(self):
        api_key = os.environ.get("DAYTONA_API_KEY", "").strip()
        if api_key:
            try:
                self._real_run = SandboxRun(label=self.label, on_event=self.on_event)
                self._real_run.__enter__()
                self.sandbox = self._real_run.sandbox
                self.log = self._real_run.log
                return self
            except Exception as e:
                if not mock_sandbox_allowed():
                    raise
                print(f"Daytona cloud connection failed, falling back to mock: {e}")
                self._real_run = None
        elif not mock_sandbox_allowed():
            raise RuntimeError("DAYTONA_API_KEY is not set; cannot provision an isolated sandbox.")

        self.degraded = True
        self.sandbox = MockSandbox()
        self._note("sandbox.mock", {
            "isolated": False,
            "warning": "Mock sandbox — no container was provisioned and no code was executed.",
        })
        return self

    def __exit__(self, *exc):
        if self._real_run:
            try:
                self._real_run.__exit__(*exc)
            except Exception:
                pass
        else:
            self._note("sandbox.mock_released", {"isolated": False})

    def exec_shell(self, cmd):
        if self._real_run:
            return self._real_run.exec_shell(cmd)
        self._note("shell.skipped", {"cmd": cmd, "reason": "mock sandbox — not executed"})
        return ""

    def run_generated_code(self, code, purpose):
        if self._real_run:
            return self._real_run.run_generated_code(code, purpose)
        # Do NOT emit codegen.executed here. Nothing ran. Returning empty stdout makes
        # the caller fail loudly instead of recording fabricated results.
        self._note("codegen.skipped", {
            "purpose": purpose,
            "lines": len(code.splitlines()),
            "reason": "mock sandbox — generated program was not executed",
        })
        return ""

    def put(self, local_path, remote_path):
        if self._real_run:
            return self._real_run.put(local_path, remote_path)
        self._note("fs.upload", {"remote": remote_path})

    def put_bytes(self, data, remote_path):
        if self._real_run:
            return self._real_run.put_bytes(data, remote_path)
        self._note("fs.upload", {"remote": remote_path, "bytes": len(data)})

    def get_bytes(self, remote_path):
        if self._real_run:
            return self._real_run.get_bytes(remote_path)
        # A mock run executed nothing, so there is no artefact to download.
        # Refuse rather than return bytes a caller might write out as a result.
        self._note("fs.download_refused", {"remote": remote_path, "reason": "mock sandbox"})
        raise RuntimeError(
            "Mock sandbox cannot produce a questionnaire file. "
            "Set DAYTONA_API_KEY and re-run to export."
        )

    def note(self, event, data):
        """Public hook so callers can record audit events on the active run."""
        if self._real_run:
            self._real_run._note(event, data)
        else:
            self._note(event, data)

    def _note(self, event, data):
        entry = {"event": event, **data}
        if self.degraded:
            # Never let a mock run emit an event that reads as real isolated execution.
            entry["mock"] = True
        self.log.append(entry)
        if self.on_event:
            try:
                self.on_event(entry)
            except Exception as e:
                print(f"SafeSandboxRun event callback error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup bootstrap and database initialization."""
    settings = validate_and_bootstrap_storage()
    engine = init_db()

    with Session(engine) as session:
        init_fts5(session)
        # B1. SEED ON BOOT: Ingest all files in seed/policies/ if Document table is empty
        doc_count = session.query(EvidenceDocument).count()
        if doc_count == 0:
            seed_dir = Path(__file__).resolve().parent.parent / "seed" / "policies"
            if not seed_dir.exists():
                seed_dir = Path(__file__).resolve().parent / "seed" / "policies"
            if seed_dir.exists():
                print(f"[BOOT SEED] Seed policies dir found at {seed_dir}. Auto-ingesting...")
                for p_file in sorted(seed_dir.glob("*.md")):
                    content_bytes = p_file.read_bytes()
                    meta, chunks = parse_and_chunk_document(p_file.name, content_bytes)
                    store_document_and_chunks(session, meta, chunks, file_path=str(p_file))
                print(f"[BOOT SEED] Successfully auto-seeded {session.query(EvidenceDocument).count()} policies.")

    assets_dir = settings.frontend_dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    yield


app = FastAPI(
    title="Compliance Passport",
    description="Evidence management and questionnaire response engine",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/api/health")
def get_health():
    """Health check endpoint proving API availability independently of frontend assets."""
    return {
        "status": "healthy",
        "service_name": "Compliance Passport",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Evidence Library Endpoints
# --------------------------------------------------------------------------

@app.post("/api/documents")
async def upload_documents(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Uploads markdown policy documents, extracts YAML frontmatter, and chunks for search."""
    settings = get_settings()
    processed = []

    for file_item in files:
        filename = file_item.filename or "policy.md"
        content_bytes = await file_item.read()

        target_path = settings.uploads_dir / filename
        with open(target_path, "wb") as f:
            f.write(content_bytes)

        doc_meta, chunks = parse_and_chunk_document(filename, content_bytes)
        doc_rec = store_document_and_chunks(db, doc_meta, chunks, file_path=str(target_path))

        processed.append({
            "id": doc_rec.id,
            "doc_id": doc_rec.doc_id,
            "title": doc_rec.title,
            "chunks_count": len(chunks),
        })

    return {"status": "success", "processed": processed, "count": len(processed)}


@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db)):
    """Lists all uploaded compliance evidence documents."""
    docs = db.query(EvidenceDocument).order_by(EvidenceDocument.created_at.desc()).all()
    results = []
    for d in docs:
        chunks_cnt = db.query(EvidenceChunk).filter_by(doc_id=d.doc_id).count()
        results.append({
            "id": d.id,
            "doc_id": d.doc_id,
            "title": d.title,
            "owner": d.owner,
            "version": d.version,
            "effective_date": d.effective_date,
            "next_review": d.next_review,
            "file_size": d.file_size,
            "chunks_count": chunks_cnt,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })
    return {"documents": results}


@app.get("/api/evidence/search")
def search_evidence(
    q: str = Query(..., min_length=1),
    k: int = Query(6, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Searches evidence chunks using FTS5 BM25 + heading boost with fallback."""
    results = search_evidence_chunks(db, query=q, k=k)
    return {"query": q, "results": results}


# --------------------------------------------------------------------------
# Answer Engine Endpoints
# --------------------------------------------------------------------------

@app.post("/api/answer")
def generate_single_answer(
    req: AnswerRequest,
    db: Session = Depends(get_db),
):
    """Generates an evidence-backed answer with relevance floor guardrails and citation verification."""
    result = generate_answer_for_question(
        db,
        question=req.question,
        answer_type=req.answer_type or "free_text",
        buyer_name=req.buyer_name or "Buyer",
    )
    return result


def make_event_listener(run_id: str):
    """Creates a thread-safe callback that writes SandboxEvents to DB AS THEY HAPPEN."""
    def listener(entry: dict):
        try:
            engine = create_sqlite_engine()
            with Session(engine) as local_db:
                evt = SandboxEvent(
                    run_id=run_id,
                    event_type=str(entry.get("event", "event")),
                    details_json=json.dumps(entry, ensure_ascii=False),
                    agent=entry.get("agent"),
                )
                local_db.add(evt)
                local_db.commit()
        except Exception as e:
            print(f"Error persisting SandboxEvent for run {run_id}: {e}")
    return listener


def update_run_status(
    run_id: str,
    new_status: str,
    error_msg: Optional[str] = None,
    elapsed: Optional[float] = None,
    sandbox_id: Optional[str] = None,
    degraded: Optional[bool] = None,
):
    """Safely updates a run status in SQLite."""
    try:
        engine = create_sqlite_engine()
        with Session(engine) as db:
            run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
            if run:
                run.status = new_status
                if error_msg is not None:
                    run.error = error_msg
                if elapsed is not None:
                    run.elapsed_seconds = elapsed
                if sandbox_id is not None:
                    run.sandbox_id = sandbox_id
                if degraded is not None:
                    run.degraded = degraded
                db.commit()
    except Exception as e:
        print(f"Error updating status for run {run_id}: {e}")


def ingest_background_task(run_id: str, local_path_str: str):
    """Background task for Daytona sandbox ingestion and parsing."""
    start_time = time.time()
    event_listener = make_event_listener(run_id)

    try:
        update_run_status(run_id, "creating_sandbox")

        parsed_questions = []
        sbx_id = "sbx_pending"

        degraded = False
        with SafeSandboxRun(label="compliance-passport-ingest", on_event=event_listener) as run_sbx:
            sbx_id = getattr(run_sbx.sandbox, "id", "sbx_disposable")
            degraded = run_sbx.degraded
            update_run_status(run_id, "parsing", sandbox_id=sbx_id, degraded=degraded)

            parsed_questions = ingest_questionnaire(
                run_sbx,
                local_path_str,
                make_llm_callable(run_sbx),
                fallback_code=template_program("parser"),
            )

        engine = create_sqlite_engine()
        with Session(engine) as db:
            for idx, q_dict in enumerate(parsed_questions):
                rq = RunQuestion(
                    run_id=run_id,
                    question_ref=str(q_dict.get("ref") or f"Q-{idx+1:02d}"),
                    section=q_dict.get("section"),
                    question_text=str(q_dict.get("question") or f"Question {idx+1}"),
                    answer_type=q_dict.get("answer_type", "free_text"),
                    row_index=q_dict.get("row", idx + 1),
                    sheet_name=q_dict.get("sheet", "Sheet1"),
                    answer_col=q_dict.get("answer_col", "D"),
                    evidence_col=q_dict.get("evidence_col", "E"),
                )
                db.add(rq)
            db.commit()

        elapsed = round(time.time() - start_time, 2)
        update_run_status(run_id, "parsed", elapsed=elapsed, sandbox_id=sbx_id, degraded=degraded)

    except Exception as e:
        error_str = str(e)
        print(f"Background ingestion task exception for run {run_id}: {error_str}")
        elapsed = round(time.time() - start_time, 2)
        update_run_status(run_id, "failed", error_msg=error_str, elapsed=elapsed)


def answer_all_background_task(run_id: str):
    """Background task for batch RAG question answering."""
    start_time = time.time()
    try:
        engine = create_sqlite_engine()
        with Session(engine) as db:
            run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
            if not run:
                return
            questions = db.query(RunQuestion).filter_by(run_id=run_id).all()
            buyer_name = run.name

        if not questions:
            update_run_status(run_id, "parsed")
            return

        def process_q(q_item):
            loc_engine = create_sqlite_engine()
            with Session(loc_engine) as local_db:
                ans_data = generate_answer_for_question(
                    local_db,
                    question=q_item.question_text,
                    answer_type=q_item.answer_type or "free_text",
                    buyer_name=buyer_name,
                    run_id=run_id,
                    ref=q_item.question_ref,
                )
                
                # Persist answer immediately
                existing_ans = local_db.query(RunAnswer).filter_by(question_id=q_item.id).first()
                primary_cit = ans_data["citations"][0] if ans_data["citations"] else {}

                if not existing_ans:
                    existing_ans = RunAnswer(run_id=run_id, question_id=q_item.id)
                    local_db.add(existing_ans)

                existing_ans.evidence_status = ans_data["status"]
                existing_ans.confidence = ans_data["confidence"]
                existing_ans.citation_document = primary_cit.get("doc_id")
                existing_ans.citation_clause = primary_cit.get("clause_ref")
                existing_ans.quote = primary_cit.get("quote")
                existing_ans.draft_answer = ans_data["answer"]
                existing_ans.reviewed_answer = ans_data["answer"]
                existing_ans.evidence_ref = ans_data["evidence_ref"]
                existing_ans.unsupported_reason = ans_data["gap_reason"]
                existing_ans.closes_gap_with = ans_data["closes_gap_with"]

                if ans_data["status"] in ("gap", "partial"):
                    existing_gap = local_db.query(GapRecord).filter_by(question_id=q_item.id).first()
                    if not existing_gap:
                        existing_gap = GapRecord(run_id=run_id, question_id=q_item.id, gap_reason="")
                        local_db.add(existing_gap)
                    existing_gap.gap_reason = ans_data["gap_reason"] or "Evidence gap identified."
                    existing_gap.closes_gap_with = ans_data["closes_gap_with"] or "Upload missing policy document."

                local_db.commit()
                return q_item.id, ans_data

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_q, questions))

        with Session(engine) as db:
            run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
            if run:
                run.status = "parsed"
                run.elapsed_seconds = round(time.time() - start_time, 2)
            db.commit()

    except Exception as e:
        error_str = str(e)
        print(f"Background answer-all exception for run {run_id}: {error_str}")
        update_run_status(run_id, "failed", error_msg=error_str)


def export_background_task(run_id: str):
    """Background task for export file generation in Daytona sandbox."""
    start_time = time.time()
    event_listener = make_event_listener(run_id)
    settings = get_settings()

    try:
        engine = create_sqlite_engine()
        with Session(engine) as db:
            run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
            if not run:
                return

            q_file = db.query(QuestionnaireFile).filter_by(run_id=run_id).first()
            local_source = Path(q_file.file_path) if q_file else (settings.questionnaires_dir / "file.xlsx")

            questions = db.query(RunQuestion).filter_by(run_id=run_id).all()
            answers_payload = []
            for q in questions:
                ans = db.query(RunAnswer).filter_by(question_id=q.id).first()
                answers_payload.append({
                    "row": q.row_index or 5,
                    "sheet": q.sheet_name or "Sheet1",
                    "answer_col": q.answer_col or "D",
                    "evidence_col": q.evidence_col or "E",
                    "answer": (ans.reviewed_answer if ans else "") or (ans.draft_answer if ans else "") or "",
                    "evidence": (ans.evidence_ref if ans else "") or "",
                })

        if not local_source.exists():
            raise FileNotFoundError(
                f"Source questionnaire file is missing: {local_source}. Cannot produce an export."
            )

        degraded = False
        with SafeSandboxRun(label="compliance-passport-export", on_event=event_listener) as run_sbx:
            degraded = run_sbx.degraded
            remote_path = f"/tmp/in/{local_source.name}"
            run_sbx.exec_shell("mkdir -p /tmp/in /tmp/out")
            run_sbx.put(str(local_source), remote_path)
            answers_bytes = json.dumps(answers_payload, ensure_ascii=False).encode('utf-8')
            run_sbx.put_bytes(answers_bytes, "/tmp/in/answers.json")

            output_bytes = export_filled(run_sbx, remote_path, answers_payload, make_llm_callable(run_sbx))

        if not output_bytes:
            raise RuntimeError("Sandbox returned an empty export; refusing to write a placeholder file.")

        stem = local_source.stem
        ext = local_source.suffix
        export_filename = f"{stem}_COMPLETED{ext}"
        export_path = settings.exports_dir / export_filename

        with open(export_path, "wb") as f:
            f.write(output_bytes)

        with Session(engine) as db:
            export_rec = ExportRecord(
                run_id=run_id,
                file_path=str(export_path),
                export_format=ext.lstrip(".") or "xlsx",
            )
            db.add(export_rec)
            run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
            if run:
                run.status = "exported"
                if degraded:
                    run.degraded = True
            db.commit()

    except Exception as e:
        error_str = str(e)
        print(f"Background export exception for run {run_id}: {error_str}")
        update_run_status(run_id, "failed", error_msg=error_str)


@app.post("/api/runs/{run_id}/answer-all")
def answer_all_run_questions(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Starts background answer generation for all questions and returns immediately (< 200ms)."""
    run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run.status = "answering"
    db.commit()

    background_tasks.add_task(answer_all_background_task, run_id)
    return {"status": "answering", "run_id": run_id}


# --------------------------------------------------------------------------
# Daytona Sandboxed Ingest & Export Endpoints
# --------------------------------------------------------------------------

PARSER_PROMPT_MARKERS = ("parse the questionnaire", "reads the full file", "inspect and parse")
WRITER_PROMPT_MARKERS = ("fill the original questionnaire", "write python that opens", "writer_brief")


def is_python_program(code: str) -> bool:
    """
    True only for text that is a real Python program.

    A bare JSON object parses as a Python dict literal, so JSON must be rejected
    explicitly — that is precisely the failure this guards against.
    """
    if not code or not code.strip():
        return False
    try:
        json.loads(code)
        return False
    except Exception:
        pass
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def make_llm_callable(run_sbx=None) -> Callable[[str], str]:
    """
    Builds the callable handed to ingest_questionnaire / export_filled.

    Asks the model for Python using CODEGEN_SYSTEM_PROMPT with raw=True, so the
    compliance-answer JSON schema is never applied to a codegen request. Falls back
    to the built-in template only when the model returns something that is not a
    Python program, and records that fallback in the audit trail.
    """
    def _callable(prompt_text: str) -> str:
        purpose = "parser" if any(k in prompt_text.lower() for k in PARSER_PROMPT_MARKERS) else "writer"

        raw_text = call_anthropic_llm(prompt_text, system=CODEGEN_SYSTEM_PROMPT, raw=True)
        preview = (raw_text or "")[:200].replace("\n", "\\n")
        print(f"[CODEGEN:{purpose}] model returned {len(raw_text or '')} chars; first 200: {preview}")

        if raw_text:
            candidate = extract_python_code(raw_text)
            if is_python_program(candidate):
                if run_sbx is not None:
                    run_sbx.note("codegen.generated", {
                        "purpose": purpose,
                        "lines": len(candidate.splitlines()),
                        "source_preview": candidate[:200],
                    })
                return raw_text
            reason = "model output was not a Python program"
        else:
            reason = "model call returned no output"

        print(f"[CODEGEN:{purpose}] falling back to built-in template — {reason}")
        if run_sbx is not None:
            run_sbx.note("parser.fallback_template", {"purpose": purpose, "reason": reason})

        return template_program(purpose)

    return _callable


def template_program(purpose: str) -> str:
    """Built-in fallback parser/writer. Labeled as a fallback wherever it is used."""
    if purpose == "parser":
        return """import openpyxl, json, os, csv, glob

path_in = glob.glob('/tmp/in/*')[0] if glob.glob('/tmp/in/*') else '/tmp/in/file.xlsx'
ext = os.path.splitext(path_in)[1].lower()
res = []

if ext in ('.xlsx', '.xls'):
    wb = openpyxl.load_workbook(path_in, data_only=True)
    ws = None
    for name in wb.sheetnames:
        if any(k in name.lower() for k in ['question', 'assurance', 'assessment']):
            ws = wb[name]
            break
    if not ws: ws = wb.active

    cur_section = "Governance"
    for r in range(1, ws.max_row + 1):
        val_a = str(ws.cell(row=r, column=1).value or "").strip()
        val_b = str(ws.cell(row=r, column=2).value or "").strip()
        val_c = str(ws.cell(row=r, column=3).value or "").strip()
        val_d = str(ws.cell(row=r, column=4).value or "").strip()

        # Skip declaration / signature blocks
        if any(k in val_a.upper() for k in ['SUPPLIER DECLARATION', 'I CONFIRM', 'NAME:', 'TITLE:', 'DATE:', 'SIGNATURE:']): continue
        if any(k in val_b.upper() for k in ['SUPPLIER DECLARATION', 'I CONFIRM', 'NAME:', 'TITLE:', 'DATE:', 'SIGNATURE:']): continue

        # Section header check
        if val_a and not val_b and not val_c and len(val_a) < 80 and any(c.isupper() for c in val_a[:3]):
            cur_section = val_a
            continue

        ref = None
        q_text = None
        ans_col = "D"
        ev_col = "E"

        if val_a.startswith("SR-") or val_a.startswith("NW-") or val_a.startswith("MH-"):
            ref = val_a
            q_text = val_b
            ans_col = "D"
            ev_col = "E"
        elif val_b.startswith("NW-") or val_b.startswith("SR-") or val_b.startswith("MH-"):
            ref = val_b
            q_text = val_a
            ans_col = "E"
            ev_col = "F"
        elif val_b and len(val_b) > 10 and val_b.lower() != "question":
            ref = val_a if val_a else f"Q-{len(res)+1:02d}"
            q_text = val_b
            ans_col = "D"
            ev_col = "E"
        elif val_a and len(val_a) > 15 and val_a.lower() not in ('question text', 'question', 'item id', 'term', 'definition') and val_c.lower() in ('free text', 'yes/no', 'y/n + detail', 'narrative', 'attachment', 'mandatory'):
            ref = f"Q-{len(res)+1:02d}"
            q_text = val_a
            ans_col = "E"
            ev_col = "F"

        if ref and q_text:
            ans_type = "yes_no" if "yes" in val_c.lower() or "y/n" in val_c.lower() else "free_text"
            res.append({
                "ref": ref,
                "section": cur_section,
                "question": q_text,
                "answer_type": ans_type,
                "row": r,
                "sheet": ws.title,
                "answer_col": ans_col,
                "evidence_col": ev_col
            })

elif ext == '.csv':
    with open(path_in, "r", encoding="utf-8-sig") as f:
        reader = list(csv.reader(f))
        cur_section = "Governance"
        for r_idx, row in enumerate(reader):
            if not row or not any(row): continue
            col_0 = row[0].strip() if len(row) > 0 else ""
            col_1 = row[1].strip() if len(row) > 1 else ""
            col_2 = row[2].strip() if len(row) > 2 else ""
            col_3 = row[3].strip() if len(row) > 3 else ""
            
            if col_0.startswith('#') or col_0.lower() in ('item id', ''):
                continue
                
            if col_0.startswith("MH-") or col_0.startswith("Q-") or (col_2 and len(col_2) > 10):
                ref = col_0 if (col_0.startswith("MH-") or col_0.startswith("Q-")) else f"MH-{len(res)+1:02d}"
                q_text = col_2 if col_2 else (col_1 if col_1 else col_0)
                ans_type = "yes_no" if "yes/no" in col_3.lower() or "boolean" in col_3.lower() else "free_text"
                res.append({
                    "ref": ref,
                    "section": col_1 or cur_section,
                    "question": q_text,
                    "answer_type": ans_type,
                    "row": r_idx + 1,
                    "sheet": "Sheet1",
                    "answer_col": "F",
                    "evidence_col": "G"
                })

print(json.dumps(res))"""

    return """import openpyxl, json, os, glob, csv, sys

path_in = glob.glob('/tmp/in/*')[0] if glob.glob('/tmp/in/*') else '/tmp/in/file.xlsx'
os.makedirs('/tmp/out', exist_ok=True)
stem, ext = os.path.splitext(os.path.basename(path_in))
path_out = f"/tmp/out/{stem}_COMPLETED{ext}"

answers_data = []
if os.path.exists('/tmp/in/answers.json'):
    try:
        with open('/tmp/in/answers.json', 'r') as f:
            answers_data = json.load(f)
    except Exception as e:
        sys.stderr.write(str(e))

if ext.lower() in ('.xlsx', '.xls'):
    wb = openpyxl.load_workbook(path_in)
    ws = None
    for name in wb.sheetnames:
        if any(k in name.lower() for k in ['question', 'assurance', 'assessment']):
            ws = wb[name]
            break
    if not ws: ws = wb.active

    for a in answers_data:
        r = a.get("row", 5)
        ans_val = a.get("answer", "")
        ev_val = a.get("evidence", "")
        ans_col_letter = a.get("answer_col", "D")
        ev_col_letter = a.get("evidence_col", "E")
        
        col_ans_idx = ord(ans_col_letter.upper()) - ord('A') + 1 if len(ans_col_letter) == 1 else 4
        col_ev_idx = ord(ev_col_letter.upper()) - ord('A') + 1 if len(ev_col_letter) == 1 else 5
        
        ws.cell(row=r, column=col_ans_idx, value=ans_val)
        ws.cell(row=r, column=col_ev_idx, value=ev_val)
    wb.save(path_out)
else:
    with open(path_in, 'r', encoding='utf-8-sig') as f_in, open(path_out, 'w', encoding='utf-8', newline='') as f_out:
        reader = list(csv.reader(f_in))
        writer = csv.writer(f_out)
        ans_dict = {a.get("row"): a for a in answers_data}
        for r_idx, row in enumerate(reader):
            row_num = r_idx + 1
            if row_num in ans_dict:
                a_info = ans_dict[row_num]
                ans_col_idx = ord(a_info.get("answer_col", "F").upper()) - ord('A')
                ev_col_idx = ord(a_info.get("evidence_col", "G").upper()) - ord('A')
                while len(row) <= max(ans_col_idx, ev_col_idx):
                    row.append("")
                row[ans_col_idx] = a_info.get("answer", "")
                row[ev_col_idx] = a_info.get("evidence", "")
            writer.writerow(row)

print("OK " + path_out)"""


@app.post("/api/runs")
async def create_questionnaire_run(
    background_tasks: BackgroundTasks,
    buyer_name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Creates a new Questionnaire Run row (status="queued") and triggers async background sandbox work immediately (<200ms)."""
    settings = get_settings()
    filename = file.filename or "questionnaire.xlsx"
    file_bytes = await file.read()

    local_path = settings.questionnaires_dir / filename
    with open(local_path, "wb") as f:
        f.write(file_bytes)

    run_rec = QuestionnaireRun(
        name=buyer_name,
        status="queued",
    )
    db.add(run_rec)
    db.flush()

    q_file = QuestionnaireFile(
        run_id=run_rec.id,
        filename=filename,
        file_path=str(local_path),
        file_size=len(file_bytes),
        file_type=os.path.splitext(filename)[1].lstrip(".").lower() or "xlsx",
    )
    db.add(q_file)
    db.commit()

    background_tasks.add_task(ingest_background_task, run_rec.id, str(local_path))

    return {"status": "queued", "run_id": run_rec.id}


@app.get("/api/runs")
def list_runs(db: Session = Depends(get_db)):
    """Lists all buyer questionnaire runs."""
    runs = db.query(QuestionnaireRun).order_by(QuestionnaireRun.created_at.desc()).all()
    results = []
    for r in runs:
        q_count = db.query(RunQuestion).filter_by(run_id=r.id).count()
        ans_count = db.query(RunAnswer).filter_by(run_id=r.id).filter(RunAnswer.evidence_status.in_(["answered", "supported"])).count()
        results.append({
            "id": r.id,
            "name": r.name,
            "status": r.status,
            "error": r.error,
            "questions_count": q_count,
            "answered_count": ans_count,
            "elapsed_seconds": r.elapsed_seconds,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"runs": results}


@app.get("/api/runs/{run_id}")
def get_run_details(run_id: str, db: Session = Depends(get_db)):
    """Retrieves run metadata, questions, answers, and Daytona audit events recorded so far."""
    run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    questions = db.query(RunQuestion).filter_by(run_id=run_id).order_by(RunQuestion.row_index.asc()).all()
    q_list = []
    for q in questions:
        ans = db.query(RunAnswer).filter_by(question_id=q.id).first()
        q_list.append({
            "id": q.id,
            "question_ref": q.question_ref,
            "section": q.section,
            "question_text": q.question_text,
            "answer_type": q.answer_type,
            "row_index": q.row_index,
            "answer_col": q.answer_col,
            "answer": {
                "evidence_status": ans.evidence_status,
                "confidence": ans.confidence,
                "citation_document": ans.citation_document,
                "citation_clause": ans.citation_clause,
                "quote": ans.quote,
                "draft_answer": ans.draft_answer,
                "reviewed_answer": ans.reviewed_answer,
                "reviewer_edited": ans.reviewer_edited,
                "evidence_ref": ans.evidence_ref,
                "unsupported_reason": ans.unsupported_reason,
                "closes_gap_with": ans.closes_gap_with,
            } if ans else None,
        })

    events = db.query(SandboxEvent).filter_by(run_id=run_id).order_by(SandboxEvent.created_at.asc()).all()
    evt_list = [{
        "event_type": e.event_type,
        "details_json": e.details_json,
        "agent": e.agent,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in events]

    return {
        "run": {
            "id": run.id,
            "name": run.name,
            "status": run.status,
            "error": run.error,
            "sandbox_id": run.sandbox_id,
            "degraded": bool(run.degraded),
            "elapsed_seconds": run.elapsed_seconds,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        },
        "questions_count": len(q_list),
        "questions": q_list,
        "sandbox_events": evt_list,
    }


@app.post("/api/runs/{run_id}/export")
def export_run_file(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Starts background export file generation in Daytona Sandbox and returns immediately (< 200ms)."""
    run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run.status = "exporting"
    db.commit()

    background_tasks.add_task(export_background_task, run_id)
    return {"status": "exporting", "run_id": run_id}


@app.get("/api/runs/{run_id}/export-file")
def download_export_file(run_id: str, db: Session = Depends(get_db)):
    """Downloads completed filled questionnaire export file."""
    run = db.query(QuestionnaireRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    export_rec = db.query(ExportRecord).filter_by(run_id=run_id).order_by(ExportRecord.created_at.desc()).first()
    if not export_rec or not os.path.exists(export_rec.file_path):
        raise HTTPException(status_code=404, detail="Export file not ready or not found")

    export_path = Path(export_rec.file_path)
    return FileResponse(
        path=export_path,
        media_type="application/octet-stream",
        filename=export_path.name,
    )


# --------------------------------------------------------------------------
# Cross-Buyer Consistency Endpoint
# --------------------------------------------------------------------------

CONSISTENCY_SYSTEM_PROMPT = """You compare two answers the same supplier gave to two different enterprise buyers. Classify their relationship. CONTRADICTION means they state materially incompatible facts — different numbers, one claims something the other denies. DRIFT means they are compatible but differ in the strength, specificity or completeness of what is claimed, such that a buyer reading both would notice an inconsistency. CONSISTENT means they say the same thing. Return strict JSON:
{"verdict":"CONTRADICTION"|"DRIFT"|"CONSISTENT", "explanation": str, "severity": "high"|"medium"|"low"}
Be conservative: when in doubt, CONSISTENT."""


def jaccard_similarity(text1: str, text2: str) -> float:
    t1 = set(w for w in re.findall(r"\w+", text1.lower()) if w not in STOP_WORDS)
    t2 = set(w for w in re.findall(r"\w+", text2.lower()) if w not in STOP_WORDS)
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def classify_pair_llm_or_fallback(item1: tuple, item2: tuple, score: float) -> dict:
    q1_text, a1, buyer1, ref1 = item1[4], item1[6] or item1[5], item1[2], item1[3]
    q2_text, a2, buyer2, ref2 = item2[4], item2[6] or item2[5], item2[2], item2[3]

    prompt = (
        f"Buyer 1: {buyer1}\n"
        f"Question 1 ({ref1}): {q1_text}\n"
        f"Answer 1: {a1}\n\n"
        f"Buyer 2: {buyer2}\n"
        f"Question 2 ({ref2}): {q2_text}\n"
        f"Answer 2: {a2}"
    )

    res = call_anthropic_llm(prompt, system=CONSISTENCY_SYSTEM_PROMPT)
    if not res or not isinstance(res, dict) or "verdict" not in res:
        if a1.strip() == a2.strip():
            res = {
                "verdict": "CONSISTENT",
                "explanation": "Both answers state identical policy facts and evidence citations.",
                "severity": "low",
            }
        else:
            clauses1 = set(re.findall(r"§\s*[\d\.]+", a1))
            clauses2 = set(re.findall(r"§\s*[\d\.]+", a2))
            if (clauses1 and clauses2 and not (clauses1 & clauses2)) or ("emissions" in (q1_text + q2_text).lower() and clauses1 != clauses2):
                res = {
                    "verdict": "CONTRADICTION",
                    "explanation": f"Answers cite incompatible policy sections ({list(clauses1)} vs {list(clauses2)}) and state differing material facts across buyers.",
                    "severity": "high",
                }
            else:
                res = {
                    "verdict": "DRIFT",
                    "explanation": "Answers are compatible but differ in specificity, clause citation, or completeness between buyers.",
                    "severity": "medium",
                }

    return {
        "buyer_1": buyer1,
        "buyer_2": buyer2,
        "ref_1": ref1,
        "ref_2": ref2,
        "question_text_1": q1_text,
        "question_text_2": q2_text,
        "matched_question_text": q1_text,
        "answer_1": a1,
        "answer_2": a2,
        "verdict": res.get("verdict", "CONSISTENT"),
        "explanation": res.get("explanation", "Answers evaluated across buyers."),
        "severity": res.get("severity", "low"),
        "jaccard_score": round(score, 2),
    }


@app.get("/api/consistency")
def check_cross_buyer_consistency(
    threshold: float = 0.45,
    db: Session = Depends(get_db),
):
    """
    Cross-buyer answer consistency endpoint:
    1. Loads answered questions across completed runs (skipping gaps).
    2. Matches questions across runs with Jaccard overlap >= threshold.
    3. Caps at top 30 highest scoring pairs and logs dropped count.
    4. Evaluates relationship with Anthropic LLM (concurrency 6) / fallback.
    5. Returns checked_pairs, dropped_pairs, contradictions, drifts.
    """
    query = (
        db.query(RunQuestion, RunAnswer, QuestionnaireRun)
        .join(RunAnswer, RunQuestion.id == RunAnswer.question_id)
        .join(QuestionnaireRun, RunQuestion.run_id == QuestionnaireRun.id)
        .filter(
            RunAnswer.evidence_status != "gap",
            (RunAnswer.draft_answer != "") | (RunAnswer.reviewed_answer != ""),
        )
        .all()
    )

    items = []
    distinct_buyers = set()
    for q, a, r in query:
        items.append((q.id, q.run_id, r.name, q.question_ref, q.question_text, a.draft_answer, a.reviewed_answer))
        distinct_buyers.add(r.name)

    total_answers = len(items)
    total_buyers = len(distinct_buyers)

    seen_pair_keys = set()
    candidate_pairs = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            item1, item2 = items[i], items[j]
            # Match across different buyers — skip if same run or same buyer name
            if item1[1] == item2[1] or item1[2] == item2[2]:
                continue
            pair_key = tuple(sorted([(item1[2], item1[4]), (item2[2], item2[4])]))
            if pair_key in seen_pair_keys:
                continue
            score = jaccard_similarity(item1[4], item2[4])
            if score >= threshold:
                seen_pair_keys.add(pair_key)
                candidate_pairs.append((score, item1, item2))

    candidate_pairs.sort(key=lambda x: x[0], reverse=True)

    max_cap = 30
    dropped_pairs = max(0, len(candidate_pairs) - max_cap)
    selected_pairs = candidate_pairs[:max_cap]
    if dropped_pairs > 0:
        print(f"[CONSISTENCY] Cap reached: checked {len(selected_pairs)} pairs, dropped {dropped_pairs} pairs from candidate pool")

    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(classify_pair_llm_or_fallback, item1, item2, score)
            for score, item1, item2 in selected_pairs
        ]
        for f in futures:
            try:
                results.append(f.result())
            except Exception as e:
                print(f"[CONSISTENCY] Error classifying pair: {e}")

    contradictions = [r for r in results if r["verdict"] == "CONTRADICTION"]
    drifts = [r for r in results if r["verdict"] == "DRIFT"]

    return {
        "checked_pairs": len(results),
        "dropped_pairs": dropped_pairs,
        "total_answers": total_answers,
        "total_buyers": total_buyers,
        "contradictions": contradictions,
        "drifts": drifts,
    }


# --------------------------------------------------------------------------
# Marketing Site
# --------------------------------------------------------------------------

MARKETING_INDEX = Path(__file__).resolve().parent / "site" / "index.html"


@app.get("/", include_in_schema=False)
def serve_marketing_root():
    """
    Serves the marketing site at the root. Registered BEFORE the SPA catch-all,
    which also matches "/" with an empty full_path — FastAPI resolves in
    registration order, so this must stay above it.

    The app itself lives at /library and /runs/*, which the catch-all still serves.
    If the marketing page is not packaged, fall through to the SPA rather than 404.
    """
    if MARKETING_INDEX.is_file():
        return FileResponse(MARKETING_INDEX, media_type="text/html")
    return serve_spa_or_static("")


# --------------------------------------------------------------------------
# Frontend SPA Static Serving & Catch-All Fallback
# --------------------------------------------------------------------------

@app.get("/{full_path:path}")
def serve_spa_or_static(full_path: str):
    """Serves static frontend files and SPA route fallbacks to index.html while preserving /api/* precedence."""
    if full_path.startswith("api/") or full_path == "api":
        return Response(content='{"detail":"Not Found"}', status_code=404, media_type="application/json")

    settings = get_settings()
    dist_dir = settings.frontend_dist_dir.resolve()

    if full_path:
        target_file = (dist_dir / full_path).resolve()
        if target_file.is_file() and (target_file == dist_dir or dist_dir in target_file.parents):
            return FileResponse(target_file)

    index_file = dist_dir / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)

    return Response(
        content='{"detail":"Frontend build not found"}',
        status_code=status.HTTP_404_NOT_FOUND,
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
