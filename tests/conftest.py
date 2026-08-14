import pytest
from pathlib import Path
from sqlalchemy import text
from fastapi.testclient import TestClient

from config import Settings, validate_and_bootstrap_storage
from database import Base, create_sqlite_engine, sessionmaker
import models
from main import app


@pytest.fixture
def temp_db_path(tmp_path):
    """Fixture providing a temporary SQLite database file path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "test_compliance.db"


@pytest.fixture
def temp_db_engine(temp_db_path):
    """Fixture providing a SQLite SQLAlchemy engine attached to a temporary file."""
    engine = create_sqlite_engine(temp_db_path)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def temp_db_session(temp_db_engine):
    """Fixture providing a scoped session connected to the temporary engine."""
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=temp_db_engine
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def temp_app_settings(tmp_path, monkeypatch):
    """Fixture configuring isolated temporary paths for FastAPI app startup."""
    app_dir = tmp_path / "app_root"
    data_dir = app_dir / "data"
    uploads_dir = app_dir / "uploads"
    exports_dir = app_dir / "exports"
    db_path = data_dir / "app_test.db"

    monkeypatch.setenv("COMPLIANCE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("COMPLIANCE_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("COMPLIANCE_EXPORTS_DIR", str(exports_dir))
    monkeypatch.setenv("COMPLIANCE_SQLITE_DB_PATH", str(db_path))

    return {
        "data_dir": data_dir,
        "uploads_dir": uploads_dir,
        "exports_dir": exports_dir,
        "db_path": db_path,
    }


@pytest.fixture
def seeded_evidence_document(temp_db_session):
    """Fixture seeding an evidence document into the test session."""
    doc = models.EvidenceDocument(
        doc_id="ALS-POL-001",
        title="Information Security Policy v2.1",
        file_path="/tmp/uploads/evidence/sec_policy.pdf",
        file_size=1048576,
        file_type="pdf",
        status="active",
    )
    temp_db_session.add(doc)
    temp_db_session.commit()
    temp_db_session.refresh(doc)
    return doc


@pytest.fixture
def seeded_run_graph(temp_db_session, seeded_evidence_document):
    """Fixture seeding a full representative questionnaire run graph into the test session."""
    run = models.QuestionnaireRun(
        name="Enterprise Security Assessment 2026",
        status="review_ready",
        sandbox_id="sbx_test_seeded",
        elapsed_seconds=4.5,
    )
    temp_db_session.add(run)
    temp_db_session.flush()

    q_file = models.QuestionnaireFile(
        run_id=run.id,
        filename="Security_Questionnaire_2026.xlsx",
        file_path="/tmp/uploads/questionnaires/q1.xlsx",
        file_size=204800,
        file_type="xlsx",
    )
    temp_db_session.add(q_file)

    job = models.JobStatus(
        run_id=run.id,
        job_type="generation",
        status="completed",
        progress_pct=100.0,
    )
    temp_db_session.add(job)

    q1 = models.RunQuestion(
        run_id=run.id,
        question_ref="SEC-01",
        section="Access Control",
        question_text="Is multi-factor authentication enforced for all administrative access?",
        answer_type="yes_no",
        row_index=5,
        sheet_name="Access Security",
    )
    temp_db_session.add(q1)
    temp_db_session.flush()

    ans1 = models.RunAnswer(
        run_id=run.id,
        question_id=q1.id,
        evidence_status="supported",
        confidence=0.92,
        citation_document=seeded_evidence_document.title,
        citation_clause="Clause 4.2.1 - Administrative MFA Policy",
        quote="MFA is mandatorily enforced on all administrative portals and SSH access.",
        draft_answer="Yes, MFA is mandatorily enforced on all administrative portals and SSH access.",
        reviewed_answer="Yes, MFA is mandatorily enforced on all administrative portals and SSH access.",
        reviewer_edited=False,
    )
    temp_db_session.add(ans1)

    q2 = models.RunQuestion(
        run_id=run.id,
        question_ref="SEC-02",
        section="Data Loss Prevention",
        question_text="Describe real-time data loss prevention monitoring for customer data.",
        answer_type="free_text",
        row_index=6,
        sheet_name="Access Security",
    )
    temp_db_session.add(q2)
    temp_db_session.flush()

    ans2 = models.RunAnswer(
        run_id=run.id,
        question_id=q2.id,
        evidence_status="gap",
        confidence=0.0,
        unsupported_reason="No explicit DLP monitoring clause found in current approved evidence library.",
        closes_gap_with="Upload Data Loss Prevention Policy v1.0 to Evidence Library.",
        reviewer_edited=True,
    )
    temp_db_session.add(ans2)

    gap = models.GapRecord(
        run_id=run.id,
        question_id=q2.id,
        gap_reason="Missing DLP policy document.",
        closes_gap_with="Upload Data Loss Prevention Policy v1.0 to Evidence Library.",
        suggested_action="Upload Data Loss Prevention Policy v1.0 to Evidence Library.",
    )
    temp_db_session.add(gap)

    approval = models.RunApproval(
        run_id=run.id,
        approved_by="Jane Doe (Security Lead)",
        status="pending",
    )
    temp_db_session.add(approval)

    export_rec = models.ExportRecord(
        run_id=run.id,
        file_path="/tmp/exports/Security_Questionnaire_2026_COMPLETED.xlsx",
        export_format="xlsx",
    )
    temp_db_session.add(export_rec)

    metric = models.ReviewerMetric(
        run_id=run.id,
        answers_reviewed=2,
        answers_edited=1,
        time_spent_seconds=180,
    )
    temp_db_session.add(metric)

    evt = models.SandboxEvent(
        run_id=run.id,
        event_type="sandbox.created",
        details_json='{"sandbox_id": "sbx_test_seeded"}',
    )
    temp_db_session.add(evt)

    temp_db_session.commit()
    temp_db_session.refresh(run)
    return run
