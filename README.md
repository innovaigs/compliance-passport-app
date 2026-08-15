# Compliance Passport

**Answer it once, answer it everywhere.**

Compliance Passport turns a supplier's approved policy library into evidence-grounded answers to enterprise buyer questionnaires. It ingests whatever oddly-shaped `.xlsx` or `.csv` a buyer sends, extracts the questions, drafts an answer for each one citing the exact policy clause it came from, **checks that the quoted text is actually in that clause**, and writes the results back into the buyer's original file.

Where the evidence does not support a claim, it does not compose one. It files a gap and tells you which document would close it.

---

## The problem

Every enterprise buyer sends a differently-shaped supplier risk assessment — Coupa, Avetta, EcoVadis, Sedex, Ariba, or a spreadsheet somebody built in-house. A supplier answers the same underlying questions over and over in someone else's format. A single questionnaire takes **15–40 hours** of a compliance lead's time: hunting through policy documents, copying clauses into cells, chasing down whether the company can actually claim a given certification, and reformatting all of it to the buyer's template.

Nothing carries over to the next buyer, because the next buyer's spreadsheet looks nothing like the last one. And the answers drift between buyers, which is itself an audit exposure nobody watches for.

The insight this project is built on: **a questionnaire is not a form, it is a query against evidence.** Structure the policies once with clause-level provenance, and any buyer's questionnaire — in any format — becomes a read against that graph.

---

## What it does

```
  buyer's file                                             filled buyer's file
  (.xlsx / .csv)                                           (same template, completed)
       │                                                             ▲
       ▼                                                             │
  ┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
  │   parser    │──▶│   retrieval  │──▶│    answer     │──▶│    writer    │
  │  generated  │   │  BM25 over   │   │  drafted from │   │  generated   │
  │  per file,  │   │  your policy │   │  your clauses │   │  per file,   │
  │  sandboxed  │   │  clauses     │   │  + verified   │   │  sandboxed   │
  └─────────────┘   └──────────────┘   └───────────────┘   └──────────────┘
                                               │
                                               ▼
                                        ┌──────────────┐
                                        │ gap register │  what you cannot
                                        │              │  yet claim
                                        └──────────────┘
```

1. **Upload your policy library.** Markdown documents with YAML frontmatter are parsed, chunked by heading and numbered clause, and indexed for retrieval.
2. **Upload a buyer's questionnaire.** The system inspects the real file layout and writes a Python parser specifically for it, which runs in a disposable container.
3. **Draft answers.** Each question retrieves the top clauses from your library; the model drafts an answer using only that text.
4. **Verify.** Every citation is checked against the source clause. Quotes that aren't there get flagged and the answer downgraded.
5. **Review and export.** The filled file is the buyer's own template, completed.

---

## The refusal behaviour, and why it matters

The answer engine is built to **admit gaps rather than fill them**. A procurement auditor may later test any answer against reality, so an unsupported claim is far more damaging than an acknowledged absence.

If the evidence library holds no ISO 27001 certificate, a question asking for one returns `status: "gap"` with an **empty answer string** and a `closes_gap_with` remediation step — never a hedge, never a plausible-sounding paragraph.

Four mechanisms enforce this, in order:

1. **Explicit claim guardrails** — certifications and policies the organisation does not hold are refused before retrieval runs. *(Currently a hardcoded substring list; see Known limitations.)*
2. **Relevance floor** — if no retrieved chunk contains any key term from the question, the engine returns `gap` without ever calling the model.
3. **Document-level citation validation** — any citation naming a document that was not retrieved is dropped. If no citation survives, the answer is downgraded to `gap` and the text discarded.
4. **Clause and quote verification** — each surviving citation is checked against the text of the clause it names. If the quoted text is not there, the answer stops presenting as fully supported.

### Measured on the reference library

Against the seven Altura seed policies (106 clauses) across three real questionnaires:

| | |
|---|---|
| Answers with a citation | 77 |
| **Quotes found verbatim in the clause cited** | **72 (94%)** |
| Quote real, clause reference wrong | 1 |
| Quote not found in the cited document | 4 |
| Questions refused outright as gaps | 52 of 129 (40%) |

That ~6% failure rate is the reason step 4 exists. The failures are not obvious — they read correctly and are thematically right. One example, preserved in `tests/fixtures/citation_regression_cases.json`:

> "Altura Language Services LLC prohibits all forms of modern slavery, human trafficking, forced labour, bonded labour, indentured la…"

Fluent, accurate in substance, and **not in the document**. It is a paraphrase the model presented as a quotation. Verification catches it, the answer is downgraded to `partial`, and the reviewer sees:

> The quoted text was not found in ALS-POL-001. The wording may be a paraphrase rather than a quotation — check the source clause before submitting.

The run header reports **"N of M quotes verified against source"** straight from the database, so the number is auditable rather than asserted.

And the 40% gap rate is not a failure mode — it is the honest state of that supplier's evidence, written down for the first time. The UI surfaces it as a "What you cannot yet claim" register, grouped by the document that would close each gap.

---

## How it works

A single FastAPI process serves both the JSON API and the built React SPA.

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.11+), async lifespan bootstrap, in-process background workers |
| Storage | SQLite in WAL mode, FTS5 virtual table for BM25 clause retrieval, Alembic migrations |
| Frontend | React 18 + Vite, served as static assets by the same process |
| Model | Anthropic Claude (`claude-sonnet-4-5-20250929` by default) |
| Execution | Daytona cloud sandboxes — a disposable container per phase |

### The three agents

| Agent | Runs | Does |
|---|---|---|
| **parser** | in sandbox | A fixed probe dumps the real sheet layout, then the model writes a Python parser against *that* structure. The program runs in the sandbox and prints a JSON array of questions. On failure its output is fed back for up to 3 attempts, then a labelled built-in template takes over — recorded as `parser.fallback_template` so the audit trail never implies generation that did not happen. |
| **answer** | in-process | Retrieves the top 6 policy clauses by BM25, applies the four guardrails above, drafts an answer with clause citations, verifies them, and calibrates displayed confidence against retrieval strength. No generated code is involved, so nothing here is sandboxed. |
| **writer** | in sandbox | Generates a Python program that opens the buyer's *original* file and writes answers and evidence references into the correct columns, preserving section banners and formatting. |

### Where the sandbox boundary sits

**Model-authored code never executes in the application process.** Every generated program goes through `sandbox.process.code_run` in a Daytona container. There is no `exec`, `eval`, `subprocess`, or dynamic import on model output anywhere in this codebase. `ast.parse` is used to validate that a candidate is a Python program before it is sent to the sandbox — parsing does not execute.

The full source of every executed program is stored and shown in the review screen, alongside its stdout and exit code, labelled by origin (`model`, `builtin_probe`, or `fallback_template`) so the audit trail distinguishes what the model wrote from what shipped with the product.

If Daytona is unavailable the run **fails** rather than silently degrading. A mock mode exists for local development but must be explicitly enabled, marks its run `degraded`, executes nothing, and cannot produce an export file.

### Cross-buyer consistency

`GET /api/consistency` compares answers given to different buyers for lexically similar questions and classifies each pair as `CONTRADICTION`, `DRIFT`, `CONSISTENT`, or `NOT_CHECKED`.

If the classifier is unavailable, pairs come back `NOT_CHECKED` — never a guessed verdict. An earlier build inferred contradictions from disjoint clause numbers, which manufactured high-severity compliance findings indistinguishable from real ones. The all-clear panel only appears when every candidate pair actually received a verdict.

---

## Quickstart

Requires Python 3.11+ and Node 18+.

```bash
git clone https://github.com/innovaigs/compliance-passport-app.git
cd compliance-passport-app
cp .env.example .env          # then fill in your keys
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
(cd frontend && npm install && npm run build)
```

Start the server:

```bash
set -a; source .env; set +a
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. On first boot the seven `ALS-` seed policies are ingested automatically into 106 searchable clauses.

### Configuration

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Answer drafting and parser/writer code generation |
| `DAYTONA_API_KEY` | yes | Provisions the sandbox each run executes inside |
| `ANTHROPIC_MODEL` | no | Defaults to `claude-sonnet-4-5-20250929` |
| `COMPLIANCE_ALLOW_MOCK_SANDBOX` | no | Set to `1` to start without Daytona. Runs are marked `degraded`, execute nothing, and cannot export |

Secrets are read from the process environment and raise on absence. `.env` only fills in what the environment does not supply — **the process environment always wins**, so a stale file cannot silently override a rotated key. Startup logs which source each secret came from, by name only:

```
[SECRETS] ANTHROPIC_API_KEY   <- process environment
[SECRETS] DAYTONA_API_KEY     <- .env file  (a value in the process environment would take precedence)
```

### Database

Schema is managed by Alembic and migrations run automatically at startup. To run them by hand:

```bash
alembic upgrade head
```

A database created before migrations existed is adopted only after its schema is verified, table by table and column by column, against the baseline revision. A mismatch raises with the diff printed and the app refuses to start rather than declaring a drifted database current.

---

## API

| Method + path | Purpose |
|---|---|
| `GET /api/health` | Liveness. Touches nothing else |
| `POST /api/documents` | Upload policy documents; parse frontmatter, chunk, index |
| `GET /api/documents` | List the evidence library |
| `GET /api/evidence/search?q=&k=` | BM25 clause search |
| `POST /api/answer` | Single-question RAG with full guardrails |
| `POST /api/runs` | Upload a questionnaire; starts sandboxed ingest |
| `GET /api/runs` | List runs |
| `GET /api/runs/{id}` | Run detail: questions, answers, citations with verdicts, `quote_verification`, sandbox events |
| `POST /api/runs/{id}/answer-all` | Batch-draft every answer |
| `GET /api/runs/{id}/artifacts` | Full source of every program executed for the run |
| `POST /api/runs/{id}/export` | Fill the buyer's original file in a sandbox |
| `GET /api/runs/{id}/export-file` | Download the completed file |
| `GET /api/consistency` | Cross-buyer contradiction and drift report |

---

## Seed data

`seed/policies/` holds seven documents for a fictional language-services company, Altura Language Services — anti-trafficking, labour standards, anti-bribery, whistleblowing, supplier code of conduct, a risk register, and a training register. 106 clauses total.

`seed/questionnaires/` holds three deliberately dissimilar files, each exercising a different part of the ingest path:

| File | Shape | Tests |
|---|---|---|
| `halberd_staffing_supplier_risk_assessment_FY26.xlsx` | Single sheet, `SR-n.n` refs, 53 questions | The baseline path, and the refusal behaviour — `SR-6.1` and `SR-6.2` ask for an information security policy and ISO 27001 / SOC 2, neither of which the library holds, so both must return `gap` |
| `meridian_health_vendor_questionnaire_v3.csv` | CSV, comment preamble, `VQ-nnn` refs | A non-Excel format, with leading `#` comment rows the parser has to skip |
| `northwind_logistics_tier1_supplier_assurance.xlsx` | Three sheets, `NW-x-nn` refs, 51 questions | Multi-sheet selection — questions live on `Assurance Questions`, and `Read First` and `Definitions` must be ignored. Answers go in columns E/F, not D/E |

---

## Repository layout

```
main.py                 FastAPI app: routes, background workers, sandbox wrapper
answer_engine.py        Retrieval, guardrails, drafting, Anthropic client
citation_verify.py      Pure verification logic — no I/O, no settings
evidence_service.py     Document parsing, clause chunking, FTS5 search
daytona_service.py      Sandbox lifecycle and the generated-code workflows
models.py               SQLAlchemy ORM models
database.py             Engine, session, migration bootstrap
schema_check.py         Structural schema comparison for safe adoption
secret_source.py        Tracks whether each secret came from env or .env
migrations/             Alembic revisions
frontend/src/           React SPA
seed/                   Reference policy library and questionnaires
tests/fixtures/         Preserved real citation failures
scripts/                Backfill and verification utilities
```

---

## Tests

```bash
source .venv/bin/activate && python3 -m pytest tests/test_citation_verify.py -q
```

`tests/test_citation_verify.py` (15 tests) is the trustworthy suite. Its negative cases are **real failures produced by the model against the seed library**, preserved in `tests/fixtures/citation_regression_cases.json` with expectations derived by independently searching the evidence chunks — not by running the code under test. Failures at that rate are hard to manufacture, so they are kept as a permanent fixture.

The older suites in `tests/` are **broken and should not be trusted**: fixtures pass model fields that no longer exist, `test_batch3` asserts a response shape the endpoint no longer returns, and `test_batch2` calls the live Anthropic API. They are being reconciled. A green run there is not a signal.

The project's standing engineering rules — including why a passing self-written test suite is not acceptance evidence — are in [CLAUDE.md](CLAUDE.md).

---

## Known limitations

An honest list. These are real and currently unaddressed.

**Security — not yet built**

- **No authentication.** Every endpoint is unauthenticated. Anyone who can reach the host can read the full policy library, every answer, and the complete gap register — which is a written admission of everything the company cannot prove.
- **Upload filenames are not sanitised.** The upload handlers join a client-supplied filename onto a directory path without stripping traversal segments, so a crafted filename can write outside the intended directory.
- **No tenant isolation.** There is no `org_id` anywhere in the schema. Two customers on one deployment would share an evidence library. Run one deployment per customer until this lands.
- **Prompt injection is unmitigated.** The questionnaire's cell contents are dumped verbatim into the parser code-generation prompt, and the generated program runs with network access. A crafted spreadsheet is an untrusted input reaching codegen.

**Product — not yet built**

- **Reviewer edits are not persisted.** Editing an answer in the review screen updates React state only; there is no PATCH endpoint. The export reads from the database, so a reviewer's correction never reaches the file sent to the buyer. The human-in-the-loop review is currently presentational.
- **Guardrails are hardcoded substring matches** containing one organisation's compliance posture. A customer who *does* hold ISO 27001 would be told by the product that they do not. This is the hard blocker on a first real customer; it needs a per-tenant claims registry.
- **No answer history.** Answers are mutated in place, and exports record a file path rather than which answer versions were sent to which buyer on which date. Answer memory, drift detection, and continuous assurance all depend on that record.

**Engineering**

- **A new SQLAlchemy engine is created per request and per worker thread.** The module-level engine and session factory are unused. A 53-question run builds roughly 55 engines.
- **The run detail endpoint is N+1**, and the client polls it every 1.5s.
- **Background work does not survive a restart.** Jobs are in-memory; a run interrupted mid-flight stays in its status forever.
- **`main.py` is monolithic** at ~1,350 lines, holding routes, workers, the sandbox wrapper, and embedded code-generation templates.

**Measured behaviour**

- **Code generation varies run to run.** Against a questionnaire using an unfamiliar ref prefix, 4 of 5 runs extracted exactly the right question set; the fifth included a header row its generated skip-guard missed. The retry loop and labelled template fallback bound the failure, but output is not deterministic. Caching a verified parser per file fingerprint is the durable fix.
- **BM25 ranking is noisy on boilerplate-heavy questions.** Stop words are stripped before matching, which fixes most cases, but short dense clauses can still outrank the clause that actually answers a question.
- **The writer gets one attempt.** The parser retries three times against its own traceback; the writer has no repair loop, so a malformed program means a failed export.
- **Answer generation has no offline path.** If the Anthropic API is unreachable, or a response omits the mandated `confidence` field, the run fails. There is deliberately no locally-composed fallback — an earlier build emitted one at a fixed 0.85 confidence with `status: "answered"`, which is exactly the fabrication this product exists to prevent.

---

## Design principles

Every rule in [CLAUDE.md](CLAUDE.md) exists because this codebase violated it once, in a way that produced convincing output while the real path was dead. The short version:

- **No silent fallbacks.** If a dependency is unreachable, raise. Never substitute a template, a default confidence, or placeholder bytes for a real result.
- **A raise nobody can see is a silent failure.** The error has to reach a human, verified in the context where it actually fires.
- **Verify with real data, and test the negative case.** A guard is not verified until you have broken something on purpose and watched it catch it.
- **Never claim more than the code does.** If a capability is partially implemented, say which part.
- **Migrations are code.** An autogenerated migration is a proposal, not an instruction.

---

## Status

Working prototype, moving toward a first paying customer. The answer path, retrieval, sandboxed code generation, citation verification, and the gap register all work against real data. Authentication, tenancy, reviewer persistence, and a per-tenant claims registry do not exist yet and are the gating work — see Known limitations for the full list.

Built on **SoftwareForge**, with model-authored code executed in **Daytona** sandboxes.
