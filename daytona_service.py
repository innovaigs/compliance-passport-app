"""
daytona_service.py — the sandboxed execution layer for Compliance Passport.

Why this exists (this is the 30% "Built on Daytona" story, say it out loud in the demo):

Every buyer sends a differently-shaped file. There is no universal parser. So the
agent WRITES a parser for each file it has never seen, and that generated Python
is executed inside a disposable Daytona sandbox — never on our host, never in our
API process. Same for the writer that fills the buyer's original file back in.

Three properties we get for free and that an enterprise buyer actually cares about:
  1. Untrusted-code isolation. Model-authored code touching customer documents
     runs in a container that is destroyed at the end of the request.
  2. Tenant isolation. One sandbox per run means Supplier A's policy library is
     never in the same filesystem as Supplier B's.
  3. Reproducibility. The demo runs from a snapshot, so it is byte-identical to
     what was built.

Requires:  pip install daytona
Env:       DAYTONA_API_KEY
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from daytona import Daytona


# --------------------------------------------------------------------------
# Sandbox lifecycle
# --------------------------------------------------------------------------

class SandboxRun:
    """One questionnaire run == one sandbox. Context manager so it always dies."""

    def __init__(self, label: str = "compliance-passport", keep_alive: bool = False,
                 on_event: Optional[Callable[[dict], None]] = None,
                 on_artifact: Optional[Callable[[dict], None]] = None):
        self.label = label
        self.keep_alive = keep_alive
        self.on_event = on_event
        # Receives the full source of every program executed here, with its
        # stdout and exit code. The codegen event carries only a preview.
        self.on_artifact = on_artifact
        self._daytona = Daytona()          # reads DAYTONA_API_KEY
        self.sandbox = None
        self.log: list[dict[str, Any]] = []   # audit trail for the UI

    def __enter__(self) -> "SandboxRun":
        self.sandbox = self._daytona.create()
        self._note("sandbox.created", {"sandbox_id": getattr(self.sandbox, "id", "?")})
        # Anything the generated code might import. Keep this list short — it is
        # part of the trust boundary.
        self.exec_shell("pip install --quiet openpyxl pandas python-docx pypdf")
        return self

    def __exit__(self, *exc) -> None:
        if self.sandbox and not self.keep_alive:
            try:
                self.sandbox.delete()
                self._note("sandbox.destroyed", {})
            except Exception as e:                      # never fail a run on cleanup
                self._note("sandbox.destroy_failed", {"error": str(e)})

    # -- primitives ---------------------------------------------------------

    def exec_shell(self, cmd: str) -> str:
        r = self.sandbox.process.exec(cmd)
        self._note("shell", {"cmd": cmd, "exit": getattr(r, "exit_code", None)})
        return getattr(r, "result", "") or ""

    PHASE_BY_PURPOSE = {
        "probe-structure": "probe",
        "parse-questionnaire": "parser",
        "parse-questionnaire-retry": "parser",
        "write-answers": "writer",
        "fill-questionnaire": "writer",
    }

    def run_generated_code(self, code: str, purpose: str, attempt: int = 1,
                           origin: str = "model", model: Optional[str] = None) -> str:
        """Execute model-authored Python inside the sandbox and return its stdout."""
        r = self.sandbox.process.code_run(code)
        out = getattr(r, "result", "") or ""
        exit_code = getattr(r, "exit_code", None)
        phase = self.PHASE_BY_PURPOSE.get(purpose, "parser")
        agent = phase if phase in ("parser", "writer") else None

        self._note("codegen.executed", {
            "purpose": purpose,
            "lines": len(code.splitlines()),
            "exit": exit_code,
            "stdout_preview": out[:400],
            "agent": agent,
        })

        if self.on_artifact:
            try:
                self.on_artifact({
                    "phase": phase,
                    "attempt": attempt,
                    "origin": origin,
                    "source": code,
                    "line_count": len(code.splitlines()),
                    "stdout": out,
                    "exit_code": exit_code,
                    "model": model,
                    "sandbox_id": getattr(self.sandbox, "id", None),
                })
            except Exception as e:
                print(f"SandboxRun artifact callback error: {e}")

        return out

    def put(self, local_path: str, remote_path: str) -> None:
        self.sandbox.fs.upload_file(local_path, remote_path)
        self._note("fs.upload", {"remote": remote_path})

    def put_bytes(self, data: bytes, remote_path: str) -> None:
        self.sandbox.fs.upload_file(data, remote_path)
        self._note("fs.upload", {"remote": remote_path, "bytes": len(data)})

    def get_bytes(self, remote_path: str) -> bytes:
        self._note("fs.download", {"remote": remote_path})
        return self.sandbox.fs.download_file(remote_path)

    def note(self, event: str, data: dict) -> None:
        """Public hook so callers can record audit events on the active run."""
        self._note(event, data)

    def _note(self, event: str, data: dict) -> None:
        entry = {"event": event, **data}
        self.log.append(entry)
        if self.on_event:
            try:
                self.on_event(entry)
            except Exception as e:
                print(f"SandboxRun event listener exception: {e}")


# --------------------------------------------------------------------------
# High-level workflows
# --------------------------------------------------------------------------

PROBE_CODE = """import openpyxl, json, os, glob, csv

path = glob.glob('/tmp/in/*')[0]
ext = os.path.splitext(path)[1].lower()
out = {"file": os.path.basename(path), "ext": ext, "sheets": []}

if ext in ('.xlsx', '.xlsm', '.xls'):
    wb = openpyxl.load_workbook(path, data_only=True)
    for name in wb.sheetnames:
        ws = wb[name]
        head = []
        for r in range(1, min(ws.max_row, 30) + 1):
            row = []
            for c in range(1, min(ws.max_column, 10) + 1):
                v = ws.cell(row=r, column=c).value
                row.append(str(v)[:60] if v is not None else None)
            head.append(row)
        out["sheets"].append({
            "name": name, "max_row": ws.max_row, "max_col": ws.max_column, "first_30_rows": head,
        })
else:
    with open(path, encoding='utf-8-sig') as f:
        rows = [r[:10] for r in list(csv.reader(f))[:30]]
    out["sheets"].append({"name": "csv", "first_30_rows": rows})

print(json.dumps(out))"""


PROMPT_PARSER = """You are an expert Python engineer.
Write a self-contained Python script to inspect and parse the questionnaire file located at `/tmp/in/{filename}`.

This is the ACTUAL structure of the file, produced by running a probe against it.
Write your parser against THIS layout — do not guess at column positions:

{structure}

The script MUST print a valid JSON array of objects to stdout.
Each object in the JSON array MUST have the following fields:
  - "ref": string (the question identifier EXACTLY as it appears in the file, e.g. "SR-1.1", "NW-A-01", "VND.A.01". Only invent a "Q-01" style ref if the file genuinely has no identifier column.)
  - "section": string (section heading name e.g. "Governance")
  - "question": string (exact question text)
  - "answer_type": string ("free_text", "yes_no", or "choice")
  - "row": integer (1-based row index in source file)
  - "sheet": string (worksheet name)
  - "answer_col": string (column letter where answer should be written e.g. "D")
  - "evidence_col": string (column letter where evidence citation should be written e.g. "E")

Return ONLY executable Python code enclosed in ```python ``` codeblock or plain Python text. Do not include markdown commentary outside code blocks."""

PROMPT_WRITER = """You are an expert Python engineer.
Write a self-contained Python script to fill the original questionnaire file at `/tmp/in/{filename}` using answers in `/tmp/in/answers.json`.

`/tmp/in/answers.json` is a JSON **array** of objects (not an object/dict). Each element has exactly these keys:
  - "row": integer, 1-based row index in the source file
  - "sheet": string, worksheet name
  - "answer_col": string, column letter to write the answer into, e.g. "D"
  - "evidence_col": string, column letter to write the evidence reference into, e.g. "E"
  - "answer": string, may be empty
  - "evidence": string, may be empty

Iterate the array directly. Do not call .items() on it.
Write each element's "answer" into (sheet, row, answer_col) and its "evidence" into (sheet, row, evidence_col).
Leave every other cell exactly as it is, preserving section header rows and formatting.

Save the completed file to `/tmp/out/{stem}_COMPLETED{ext}` and create /tmp/out if needed.
The script MUST print "OK /tmp/out/{stem}_COMPLETED{ext}" to stdout upon completion.

Return ONLY executable Python code enclosed in ```python ``` codeblock or plain Python text."""


def extract_python_code(raw_text: str) -> str:
    """Extracts raw python code from markdown triple backticks if present."""
    match = re.search(r"```python\s*(.*?)\s*```", raw_text, re.DOTALL)
    if match:
        return match.group(1)
    match_gen = re.search(r"```\s*(.*?)\s*```", raw_text, re.DOTALL)
    if match_gen:
        return match_gen.group(1)
    return raw_text.strip()


MAX_CODEGEN_ATTEMPTS = 3


def _extract_questions(stdout: str) -> Optional[list[dict[str, Any]]]:
    """Returns the parsed JSON array from generated-program stdout, or None."""
    match = re.search(r"\[.*\]", stdout, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def ingest_questionnaire(
    run_sbx: SandboxRun,
    local_path: str,
    llm_callable: Callable[[str], str],
    fallback_code: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Ingests an untrusted questionnaire file using a disposable Daytona sandbox:
    1. Uploads file to /tmp/in/{filename}
    2. Probes structure & generates Python parser via LLM
    3. Executes generated Python inside sandbox container
    4. Parses stdout JSON array into questions list

    A generated program that crashes is repaired by feeding its traceback back to
    the model, up to MAX_CODEGEN_ATTEMPTS. Only after that does it fall back to
    fallback_code, and that fallback is always recorded in the audit trail.
    """
    filename = Path(local_path).name
    remote_in = f"/tmp/in/{filename}"

    run_sbx.exec_shell("mkdir -p /tmp/in /tmp/out")
    run_sbx.put(local_path, remote_in)

    probe_out = run_sbx.run_generated_code(
        PROBE_CODE, purpose="probe-structure", origin="builtin_probe"
    )
    probe_match = re.search(r"\{.*\}", probe_out, re.DOTALL)
    structure = probe_match.group(0) if probe_match else "(probe failed; infer the layout defensively)"
    run_sbx.note("probe.structure", {"bytes": len(structure)})

    base_prompt = PROMPT_PARSER.format(filename=filename, structure=structure[:6000])
    prompt = base_prompt
    last_stdout = ""

    for attempt in range(1, MAX_CODEGEN_ATTEMPTS + 1):
        code = extract_python_code(llm_callable(prompt))
        # Use the canonical purpose labels so run_generated_code can tag the agent.
        purpose = "parse-questionnaire" if attempt == 1 else "parse-questionnaire-retry"
        stdout = run_sbx.run_generated_code(
            code, purpose=purpose, attempt=attempt, origin="model"
        )
        questions = _extract_questions(stdout)

        if questions:
            return questions

        last_stdout = stdout
        run_sbx.note("codegen.attempt_failed", {
            "attempt": attempt,
            "purpose": "parser",
            "stdout_preview": stdout[:400],
        })
        prompt = (
            f"{base_prompt}\n\n"
            f"Your previous program failed. Its full output was:\n{stdout[:1500]}\n\n"
            "Fix the defect and return the corrected complete program. "
            "Use only documented openpyxl APIs."
        )

    if fallback_code:
        run_sbx.note("parser.fallback_template", {
            "purpose": "parser",
            "reason": f"generated program failed {MAX_CODEGEN_ATTEMPTS} attempts",
        })
        stdout = run_sbx.run_generated_code(
            fallback_code,
            purpose="parse-questionnaire-retry",
            attempt=MAX_CODEGEN_ATTEMPTS + 1,
            origin="fallback_template",
        )
        questions = _extract_questions(stdout)
        if questions:
            return questions
        last_stdout = stdout

    raise ValueError(
        f"Sandbox parser execution failed: no json array in output. Output: {last_stdout[:300]}"
    )


def export_filled(
    run_sbx: SandboxRun,
    remote_path: str,
    answers_payload: list[dict[str, Any]],
    llm_callable: Callable[[str], str],
) -> bytes:
    """
    Fills an original questionnaire file inside a Daytona sandbox:
    1. Writes answers.json to /tmp/in/answers.json
    2. Generates Python writer via LLM
    3. Executes generated writer inside sandbox container
    4. Downloads filled file bytes from sandbox /tmp/out/
    """
    filename = Path(remote_path).name
    stem = Path(remote_path).stem
    ext = Path(remote_path).suffix

    answers_json_bytes = json.dumps(answers_payload, ensure_ascii=False).encode("utf-8")
    run_sbx.put_bytes(answers_json_bytes, "/tmp/in/answers.json")

    prompt = PROMPT_WRITER.format(filename=filename, stem=stem, ext=ext)
    raw_code = llm_callable(prompt)
    code = extract_python_code(raw_code)

    stdout = run_sbx.run_generated_code(code, purpose="write-answers")

    remote_out = f"/tmp/out/{stem}_COMPLETED{ext}"
    return run_sbx.get_bytes(remote_out)
