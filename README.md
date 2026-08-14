# Compliance Passport

**Answer it once, answer it everywhere.**

Compliance Passport turns a supplier's approved policy library into evidence-grounded answers to enterprise buyer questionnaires. It ingests whatever oddly-shaped `.xlsx` or `.csv` a buyer sends, extracts the questions, drafts an answer for each one citing the exact policy clause it came from, and writes those answers back into the buyer's original file. Where the evidence does not support a claim, it refuses to answer and tells you what document would close the gap.

---

## The problem

Every enterprise buyer sends a differently-shaped supplier risk assessment, and a supplier answers the same underlying questions over and over in someone else's format. A single questionnaire takes **15–40 hours** of a compliance lead's time: hunting through policy documents, copying clauses into cells, chasing down whether the company can actually claim a given certification, and reformatting all of it to the buyer's template. Nothing carries over to the next buyer, because the next buyer's spreadsheet looks nothing like the last one.

## The refusal behaviour, and why it matters

The answer engine is built to **admit gaps rather than fill them**. A procurement auditor may later test any answer against reality, so an unsupported claim is far more damaging than an acknowledged absence.

Concretely: if the evidence library holds no ISO 27001 certificate, a question asking for one returns `status: "gap"` with an **empty answer string** and a `closes_gap_with` remediation step — never a hedge, never a plausible-sounding paragraph. Three mechanisms enforce this:

1. **Relevance floor** — if no retrieved chunk contains any key term from the question, the engine returns `gap` without ever calling the model.
2. **Explicit claim guardrails** — certifications and policies the organisation does not hold are refused before retrieval runs.
3. **Post-generation citation validation** — any citation naming a document that was not in the retrieved set is dropped, and if no citation survives, the answer is downgraded to `gap` and the text discarded.

The UI surfaces these as a "What you cannot yet claim" gap register, so the gaps are the deliverable, not a failure mode.

---

## Architecture

A single FastAPI process serves both the JSON API and the built React SPA.

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.11+), async lifespan bootstrap, background task workers |
| Storage | SQLite in WAL mode, with an FTS5 virtual table for BM25 clause retrieval |
| Frontend | React 18 + Vite, served as static assets by the same process |
| Model | Anthropic Claude (`claude-sonnet-4-5-20250929` by default) |
| Execution | Daytona cloud sandboxes — one disposable container per run |

Built on **SoftwareForge**, with work orders tracked through Forge's MCP integration, and all model-authored code executed in **Daytona sandboxes**.

### Why sandboxes

There is no universal questionnaire parser, so the system writes one per file — and model-authored code that touches customer documents must never run in the API process. Each run provisions a disposable Daytona container, executes the generated program there, and destroys it. That gives untrusted-code isolation, per-run tenant isolation, and a reproducible audit trail. Every sandbox action is recorded as a `SandboxEvent` and streamed to the review screen as a live terminal.

If Daytona is unavailable the run **fails** rather than silently degrading. A mock mode exists for local development but must be explicitly enabled, marks its run `degraded`, executes nothing, and cannot produce an export file.

### The three agents

| Agent | Runs | Does |
|---|---|---|
| **parser** | in sandbox | A fixed probe dumps the real sheet layout, then the model writes a Python parser against *that* structure. The program runs in the sandbox and prints a JSON array of questions. On failure its traceback is fed back for up to 3 attempts, then a labelled built-in template takes over — recorded as `parser.fallback_template` so the audit trail never implies generation that did not happen. |
| **answer** | in-process | Retrieves the top 6 policy chunks by BM25, applies the guardrails above, drafts an answer with exact clause citations, and calibrates displayed confidence against retrieval strength. |
| **writer** | in sandbox | Generates a Python program that opens the buyer's *original* file and writes answers and evidence references into the correct columns, preserving section banners and formatting. |

---

## Setup

Requires Python 3.11+ and Node 18+.

```bash
git clone <this-repo> && cd compliance-passport
cp .env.example .env          # then fill in your keys
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
(cd frontend && npm install && npm run build)
```

Load the environment and start the server:

```bash
set -a; source .env; set +a
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. On first boot the seven `ALS-` seed policies are ingested automatically into 106 searchable chunks.

Both `ANTHROPIC_API_KEY` and `DAYTONA_API_KEY` are read from the process environment and raise on absence. There are no fallback key literals anywhere in this repo.

---

## Seed questionnaires

Three deliberately dissimilar files live in `seed/questionnaires/`, each exercising a different part of the ingest path.

| File | Shape | Tests |
|---|---|---|
| `halberd_staffing_supplier_risk_assessment_FY26.xlsx` | Single sheet, `SR-n.n` refs, 53 questions | The baseline path, and the refusal behaviour — `SR-6.1` and `SR-6.2` ask for an information security policy and ISO 27001 / SOC 2, neither of which the library holds, so both must return `gap`. |
| `meridian_health_vendor_questionnaire_v3.csv` | CSV, comment preamble, `VQ-nnn` refs | A non-Excel format entirely, with leading `#` comment rows the parser has to skip. |
| `northwind_logistics_tier1_supplier_assurance.xlsx` | Three sheets, `NW-x-nn` refs, 51 questions | Multi-sheet selection — questions live on `Assurance Questions`, and the `Read First` and `Definitions` sheets must be ignored. Answers go in columns E/F, not D/E. |

---

## Known limitations

Honest list. These are real and currently unaddressed.

**Not yet built**

- **Reviewer edits are not persisted.** Editing an answer in the review screen updates React state only; there is no PATCH endpoint, so an edit is lost on the next poll and the export writes the original draft. The human-in-the-loop review is currently presentational.
- **No authentication.** Every endpoint is unauthenticated. Anyone who can reach the host can read the full policy library, every answer, and the complete gap register.
- **Upload filenames are not sanitised.** The upload handlers join a client-supplied filename onto a directory path without stripping traversal segments, so a crafted filename can write outside the intended directory.
- **A new SQLAlchemy engine is created per request and per worker thread.** The module-level engine and session factory are unused. A 53-question run builds roughly 55 engines.
- **The run detail endpoint is N+1.** One answer lookup per question, polled by the client every 1.5s — about 54 queries per second-and-a-half per open tab.
- **`main.py` is monolithic** at ~900 lines, holding routes, background workers, the sandbox wrapper, and embedded code-generation templates.
- **Guardrails are hardcoded substring matches** rather than a declarative registry of what the organisation does and does not hold. `"ISO27001"` without a space bypasses them, and `"security policy"` over-matches legitimate answerable questions.

**Measured behaviour**

- **Code generation varies run to run.** Against a questionnaire using a ref prefix the system had never seen, 4 of 5 runs extracted exactly the right question set; the fifth returned one extra row, having included a header row its generated skip-guard missed. The retry loop and labelled template fallback bound the failure, but output is not deterministic. Caching a verified parser per buyer file-fingerprint is the durable fix.
- **BM25 ranking is noisy on boilerplate-heavy questions.** Stop words are stripped from the query before matching, which fixes most cases, but short dense clauses can still outrank the clause that actually answers a question. The correct clause is generally within the top 6 the model sees, so this degrades the deterministic fallback more than the model path.
- **The offline fallback answer is over-confident.** When the Anthropic API is unreachable, the engine emits a deterministically composed answer at a fixed 0.85 confidence with `status: "answered"`, built from the single top-ranked chunk. It should degrade to `gap` or a clearly-marked degraded state instead. Verify `ANTHROPIC_API_KEY` is live before trusting a batch run.

---

## Tests

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q
```

Some tests in `tests/test_models.py` and the batch suites currently fail against the present schema; they predate the most recent model changes and are being reconciled.
