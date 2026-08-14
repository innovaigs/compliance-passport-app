import pytest
from sqlalchemy import inspect, select
import models
from database import create_sqlite_engine, Base, sessionmaker


def test_tables_and_indexes_exist(temp_db_engine):
    """Verify that all 10 core tables and their indexes exist in SQLite schema."""
    inspector = inspect(temp_db_engine)
    table_names = set(inspector.get_table_names())

    expected_tables = {
        "evidence_documents",
        "questionnaire_runs",
        "questionnaire_files",
        "job_statuses",
        "run_questions",
        "run_answers",
        "gap_records",
        "run_approvals",
        "export_records",
        "reviewer_metrics",
    }
    assert expected_tables.issubset(table_names)

    # Verify key indexes
    evidence_indexes = [idx["name"] for idx in inspector.get_indexes("evidence_documents")]
    assert "idx_evidence_status_created" in evidence_indexes

    run_indexes = [idx["name"] for idx in inspector.get_indexes("questionnaire_runs")]
    assert "idx_run_status" in run_indexes

    answer_indexes = [idx["name"] for idx in inspector.get_indexes("run_answers")]
    assert "idx_answer_run_id" in answer_indexes


def test_seeded_run_graph_persistence(temp_db_path, temp_db_session, seeded_run_graph):
    """Verify that a complex run graph persists and reloads correctly after reconnecting."""
    run_id = seeded_run_graph.id

    # Create a fresh engine and session connecting to the SAME SQLite file
    new_engine = create_sqlite_engine(temp_db_path)
    NewSessionLocal = sessionmaker(bind=new_engine)
    new_session = NewSessionLocal()

    reloaded_run = new_session.query(models.QuestionnaireRun).filter_by(id=run_id).first()
    assert reloaded_run is not None
    assert reloaded_run.name == "Enterprise Security Assessment 2026"
    assert len(reloaded_run.questions) == 2
    assert len(reloaded_run.answers) == 2
    assert len(reloaded_run.gaps) == 1
    assert reloaded_run.approval is not None
    assert reloaded_run.approval.approved_by == "Jane Doe (Security Lead)"
    assert len(reloaded_run.exports) == 1
    assert len(reloaded_run.metrics) == 1

    new_session.close()
    new_engine.dispose()


def test_run_answer_fields(temp_db_session, seeded_run_graph):
    """Verify RunAnswer fields: evidence_status, citations, reviewed_answer, reviewer_edited, unsupported_reason."""
    answers = temp_db_session.scalars(
        select(models.RunAnswer).filter_by(run_id=seeded_run_graph.id)
    ).all()
    assert len(answers) == 2

    supported_ans = next(a for a in answers if a.evidence_status == "supported")
    assert supported_ans.citation_document == "Information Security Policy v2.1"
    assert supported_ans.citation_clause == "Clause 4.2.1 - Administrative MFA Policy"
    assert supported_ans.reviewer_edited is False

    gap_ans = next(a for a in answers if a.evidence_status == "gap")
    assert gap_ans.unsupported_reason is not None
    assert gap_ans.reviewer_edited is True
