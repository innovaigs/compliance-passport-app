"""
SQLAlchemy ORM Data Models for Compliance Passport.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


def generate_uuid() -> str:
    """Generates a string UUID4 for primary keys."""
    return str(uuid.uuid4())


def current_utc_time() -> datetime:
    """Returns current UTC timestamp."""
    return datetime.now(timezone.utc)


class EvidenceDocument(Base):
    """Stores source compliance evidence files (policies, SOC2 reports, etc.)."""
    __tablename__ = "evidence_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    doc_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    effective_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    next_review: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False, default="md")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, onupdate=current_utc_time, nullable=False)

    __table_args__ = (
        Index("idx_evidence_doc_id", "doc_id"),
        Index("idx_evidence_status_created", "status", "created_at"),
    )


class EvidenceChunk(Base):
    """Chunks of evidence documents used for semantic answer retrieval."""
    __tablename__ = "evidence_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    doc_id: Mapped[str] = mapped_column(String(100), nullable=False)
    doc_title: Mapped[str] = mapped_column(String(255), nullable=False)
    heading: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    clause_ref: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    __table_args__ = (
        Index("idx_chunk_doc_id", "doc_id"),
    )


class QuestionnaireRun(Base):
    """Represents a buyer questionnaire processing run."""
    __tablename__ = "questionnaire_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sandbox_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, onupdate=current_utc_time, nullable=False)

    files: Mapped[List["QuestionnaireFile"]] = relationship("QuestionnaireFile", back_populates="run", cascade="all, delete-orphan")
    jobs: Mapped[List["JobStatus"]] = relationship("JobStatus", back_populates="run", cascade="all, delete-orphan")
    questions: Mapped[List["RunQuestion"]] = relationship("RunQuestion", back_populates="run", cascade="all, delete-orphan")
    answers: Mapped[List["RunAnswer"]] = relationship("RunAnswer", back_populates="run", cascade="all, delete-orphan")
    gaps: Mapped[List["GapRecord"]] = relationship("GapRecord", back_populates="run", cascade="all, delete-orphan")
    approval: Mapped[Optional["RunApproval"]] = relationship("RunApproval", back_populates="run", uselist=False, cascade="all, delete-orphan")
    exports: Mapped[List["ExportRecord"]] = relationship("ExportRecord", back_populates="run", cascade="all, delete-orphan")
    metrics: Mapped[List["ReviewerMetric"]] = relationship("ReviewerMetric", back_populates="run", cascade="all, delete-orphan")
    sandbox_events: Mapped[List["SandboxEvent"]] = relationship("SandboxEvent", back_populates="run", cascade="all, delete-orphan")
    generated_artifacts: Mapped[List["GeneratedArtifact"]] = relationship("GeneratedArtifact", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_run_status", "status"),
    )


class QuestionnaireFile(Base):
    """Source questionnaire file uploaded by user."""
    __tablename__ = "questionnaire_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="files")

    __table_args__ = (
        Index("idx_qfile_run_id", "run_id"),
    )


class JobStatus(Base):
    """Tracks background job status."""
    __tablename__ = "job_statuses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, onupdate=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="jobs")


class RunQuestion(Base):
    """Extracted question from a questionnaire."""
    __tablename__ = "run_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    question_ref: Mapped[str] = mapped_column(String(50), nullable=False)
    section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(50), nullable=False, default="free_text")
    row_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    answer_col: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    evidence_col: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="questions")
    answer: Mapped[Optional["RunAnswer"]] = relationship("RunAnswer", back_populates="question", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_rquestion_run_id", "run_id"),
    )


class RunAnswer(Base):
    """Generated or reviewed answer for a question."""
    __tablename__ = "run_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_questions.id"), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(50), nullable=False, default="gap")
    # Two columns, never both set. The model reports its confidence that a gap IS
    # a gap; storing that in the same column as confidence in an answer meant a
    # refused question could carry 1.0 and read as a confident answer.
    answer_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gap_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verification_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    citation_document: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    citation_clause: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewer_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    unsupported_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    closes_gap_with: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, onupdate=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="answers")
    question: Mapped["RunQuestion"] = relationship("RunQuestion", back_populates="answer")
    citations: Mapped[List["AnswerCitation"]] = relationship(
        "AnswerCitation", back_populates="answer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_ranswer_run_id", "run_id"),
        Index("idx_ranswer_question_id", "question_id"),
        # Structural, not conventional: a refused question cannot carry answer
        # confidence and an answered one cannot carry gap confidence.
        CheckConstraint(
            "(evidence_status = 'gap' AND answer_confidence IS NULL) OR "
            "(evidence_status <> 'gap' AND gap_confidence IS NULL)",
            name="ck_run_answers_one_confidence",
        ),
    )


class GapRecord(Base):
    """Tracks unanswered questions and missing policies."""
    __tablename__ = "gap_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_questions.id"), nullable=False)
    gap_reason: Mapped[str] = mapped_column(Text, nullable=False)
    closes_gap_with: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="gaps")


class RunApproval(Base):
    """Approval metadata for a run."""
    __tablename__ = "run_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="approval")


class ExportRecord(Base):
    """Tracks exported questionnaire files."""
    __tablename__ = "export_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    export_format: Mapped[str] = mapped_column(String(50), nullable=False, default="xlsx")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="exports")


class ReviewerMetric(Base):
    """Telemetry metrics for reviewer actions."""
    __tablename__ = "reviewer_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="metrics")


class SandboxEvent(Base):
    """Daytona sandbox audit events."""
    __tablename__ = "sandbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="sandbox_events")

    __table_args__ = (
        Index("idx_sevent_run_id", "run_id"),
    )


class GeneratedArtifact(Base):
    """
    A model-authored program, stored in full alongside what it actually did.

    Exists so the review screen can show the real generated source rather than a
    representative sample, and so an auditor can be handed the exact program that
    read a customer's file. The 200-character preview on the codegen SandboxEvent
    is a log line; this is the artifact.
    """
    __tablename__ = "generated_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)  # probe | parser | writer
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)  # model | builtin_probe | fallback_template
    source: Mapped[str] = mapped_column(Text, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sandbox_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    run: Mapped["QuestionnaireRun"] = relationship("QuestionnaireRun", back_populates="generated_artifacts")

    __table_args__ = (
        Index("idx_gartifact_run_id", "run_id"),
    )


class AnswerCitation(Base):
    """
    One citation on an answer, with the result of checking it against the source.

    Replaces the three loose strings on RunAnswer. Carries a real foreign key to
    the chunk the quote was actually found in, so the evidence chain can be
    reconstructed after the fact — which was impossible before.
    """
    __tablename__ = "answer_citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("questionnaire_runs.id"), nullable=False)
    answer_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_answers.id"), nullable=False)
    # Null only when the quote resolved to no chunk at all.
    chunk_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    doc_id: Mapped[str] = mapped_column(String(100), nullable=False)
    clause_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quote_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    clause_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolution: Mapped[str] = mapped_column(String(30), nullable=False, default="unresolved")
    actual_clause_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=current_utc_time, nullable=False)

    answer: Mapped["RunAnswer"] = relationship("RunAnswer", back_populates="citations")

    __table_args__ = (
        Index("idx_acitation_answer_id", "answer_id"),
        Index("idx_acitation_run_id", "run_id"),
    )
