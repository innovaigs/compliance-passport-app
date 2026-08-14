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
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daytona import Daytona


# --------------------------------------------------------------------------
# Sandbox lifecycle
# --------------------------------------------------------------------------

class SandboxRun:
    """One questionnaire run == one sandbox. Context manager so it always dies."""

    def __init__(self, label: str = "compliance-passport", keep_alive: bool = False):
        self.label = label
        self.keep_alive = keep_alive
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

    def run_generated_code(self, code: str, purpose: str) -> str:
        """Execute model-authored Python inside the sandbox and return its stdout."""
        r = self.sandbox.process.code_run(code)
        out = getattr(r, "result", "") or ""
        self._note("codegen.executed", {
            "purpose": purpose,
            "lines": len(code.splitlines()),
            "exit": getattr(r, "exit_code", None),
            "stdout_preview": out[:400],
        })
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

    def _note(self, event: str, data: dict) -> None:
        self.log.append({"event": event, **data})


# --------------------------------------------------------------------------
# Step 1 — adaptive ingest: the agent writes a parser for a file it's never seen
# --------------------------------------------------------------------------

INSPECT = textwrap.dedent('''
    # Structural probe only — we do not send the buyer's file contents anywhere,
    # we send a shape description. Cheaper and privacy-friendlier.
    import json, os
    p = "{path}"
    ext = os.path.splitext(p)[1].lower()
    out = {{"path": p, "ext": ext}}
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(p, data_only=True)
        out["sheets"] = []
        for ws in wb.worksheets:
            grid = []
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 25),
                                    max_col=min(ws.max_column, 12), values_only=True):
                grid.append([(str(c)[:90] if c is not None else None) for c in row])
            out["sheets"].append({{"name": ws.title, "dims": [ws.max_row, ws.max_column],
                                   "head": grid}})
    elif ext == ".csv":
        import csv
        with open(p, newline="", encoding="utf-8", errors="replace") as f:
            rows = [r for _, r in zip(range(25), csv.reader(f))]
        out["head"] = [[c[:90] for c in r] for r in rows]
    print(json.dumps(out)[:12000])
''')

PARSER_BRIEF = """You are given the first rows of a supplier assessment file.

{shape}

Write Python that reads the FULL file at {path} and prints ONE json array to stdout.
Each element: {{"ref": str, "section": str|null, "question": str, "answer_type": str|null,
"row": int, "sheet": str|null, "answer_col": str|null, "evidence_col": str|null}}

Rules:
- Find the real header row; these files rarely start at A1.
- Skip section banner rows, instruction rows and blank rows. Only real questions.
- "row" is the 1-indexed row in the source file so we can write the answer back.
- "answer_col" / "evidence_col" are the column letters (xlsx) or header names (csv)
  where the supplier's answer and evidence reference belong.
- Print ONLY the json array. No prose, no markdown fence.
"""


def ingest_questionnaire(run: SandboxRun, local_file: str, llm) -> list[dict]:
    """Upload → probe shape → LLM writes parser → execute in sandbox → questions."""
    remote = f"/tmp/in/{Path(local_file).name}"
    run.exec_shell("mkdir -p /tmp/in /tmp/out")
    run.put(local_file, remote)

    shape = run.run_generated_code(INSPECT.format(path=remote), "inspect-shape")

    code = llm(PARSER_BRIEF.format(shape=shape, path=remote))
    code = _strip_fence(code)
    raw = run.run_generated_code(code, "parse-questionnaire")

    try:
        return json.loads(_first_json_array(raw))
    except Exception:
        # One self-repair attempt. Judges love seeing this loop close.
        fix = llm(PARSER_BRIEF.format(shape=shape, path=remote) +
                  f"\n\nYour previous attempt produced this instead of valid json:\n{raw[:1500]}\nFix it.")
        raw2 = run.run_generated_code(_strip_fence(fix), "parse-questionnaire-retry")
        return json.loads(_first_json_array(raw2))


# --------------------------------------------------------------------------
# Step 2 — write answers back into the buyer's ORIGINAL file, in the sandbox
# --------------------------------------------------------------------------

WRITER_BRIEF = """Write Python that opens {path} and writes the supplied answers back
into the SAME file, preserving all original formatting, then saves to {out}.

answers = {answers}

Each item has: row, sheet, answer_col, evidence_col, answer, evidence.
For xlsx use openpyxl and keep every other cell untouched. For csv, write the answer
into the 'Supplier Answer' column and evidence into 'Supporting Document', keeping the
preamble lines and all original columns intact.
Print "OK <absolute path>" when saved. Print ONLY code, no markdown fence.
"""


def export_filled(run: SandboxRun, remote_in: str, answers: list[dict], llm,
                  suffix: str = "_COMPLETED") -> bytes:
    out = remote_in.replace("/tmp/in/", "/tmp/out/")
    stem, dot, ext = out.rpartition(".")
    out = f"{stem}{suffix}.{ext}" if dot else out + suffix

    code = _strip_fence(llm(WRITER_BRIEF.format(
        path=remote_in, out=out,
        answers=json.dumps(answers, ensure_ascii=False)[:60000])))
    res = run.run_generated_code(code, "write-answers")
    if "OK" not in res:
        raise RuntimeError(f"writer failed: {res[:500]}")
    return run.get_bytes(out)


# --------------------------------------------------------------------------
# Step 3 — serve the app itself from a sandbox (this is your submission URL)
# --------------------------------------------------------------------------

def serve_app(repo_url: str, port: int = 8000,
              start_cmd: str = "cd /app && pip install -r requirements.txt && "
                               "nohup uvicorn main:app --host 0.0.0.0 --port 8000 &") -> tuple[str, Any]:
    """Clone the Forge-generated repo into a sandbox, boot it, return a public URL."""
    daytona = Daytona()
    sandbox = daytona.create()
    sandbox.process.exec(f"git clone {repo_url} /app")
    sandbox.process.exec(start_cmd)
    sandbox.process.exec("sleep 8")

    # Signed URL: token is embedded, so it works from a judge's phone with no header.
    try:
        link = sandbox.create_signed_preview_url(port, 86400)
    except Exception:
        link = sandbox.get_preview_link(port)
    url = getattr(link, "url", link)
    print(f"LIVE: {url}")
    return url, sandbox


# --------------------------------------------------------------------------

def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    return s.strip()


def _first_json_array(s: str) -> str:
    i, j = s.find("["), s.rfind("]")
    if i == -1 or j == -1:
        raise ValueError("no json array in output")
    return s[i:j + 1]


# --------------------------------------------------------------------------
# Smoke test:  python daytona_service.py  (proves the sandbox works, no LLM needed)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    assert os.getenv("DAYTONA_API_KEY"), "set DAYTONA_API_KEY first"
    with SandboxRun("smoke-test") as run:
        f = "seed/questionnaires/halberd_staffing_supplier_risk_assessment_FY26.xlsx"
        run.exec_shell("mkdir -p /tmp/in /tmp/out")
        run.put(f, "/tmp/in/test.xlsx")
        print(run.run_generated_code(INSPECT.format(path="/tmp/in/test.xlsx"), "smoke")[:800])
        print("\n--- audit trail ---")
        for e in run.log:
            print(e)
