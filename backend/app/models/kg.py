"""Knowledge graph database models for HybridRAG.

Stores entities, relationships, and extraction state per document so the
graph can be rebuilt incrementally and queried alongside vector retrieval.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship as orm_relationship

from app.database import Base


class KgEntity(Base):
    """A named entity extracted from document text (company, person, metric...)."""
    __tablename__ = "kg_entities"
    __table_args__ = (
        UniqueConstraint("document_id", "name_normalized", name="uq_kg_entity_doc_name"),
        Index("ix_kg_entities_document_id", "document_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    name_normalized = Column(String(255), nullable=False)
    entity_type = Column(String(64), nullable=False, default="other")
    description = Column(Text, nullable=True)
    pages = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    outgoing = orm_relationship("KgRelationship", foreign_keys="KgRelationship.subject_id", back_populates="subject", cascade="all, delete-orphan")
    incoming = orm_relationship("KgRelationship", foreign_keys="KgRelationship.object_id", back_populates="object_entity", cascade="all, delete-orphan")

    document = orm_relationship("Document", backref="kg_entities")


class KgRelationship(Base):
    """A directed relationship between two entities, forming the KG edges."""
    __tablename__ = "kg_relationships"
    __table_args__ = (
        UniqueConstraint("subject_id", "relationship", "object_id", name="uq_kg_rel"),
        Index("ix_kg_relationships_subject_id", "subject_id"),
        Index("ix_kg_relationships_object_id", "object_id"),
        Index("ix_kg_relationships_document_id", "document_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False)
    object_id = Column(Integer, ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False)
    relationship = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True, default=1.0)
    page_number = Column(Integer, nullable=True)
    context = Column(Text, nullable=True)

    subject = orm_relationship("KgEntity", foreign_keys=[subject_id], back_populates="outgoing")
    object_entity = orm_relationship("KgEntity", foreign_keys=[object_id], back_populates="incoming")


class KgExtractionState(Base):
    """Tracks per-document KG extraction progress so rebuilds are incremental."""
    __tablename__ = "kg_extraction_state"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_kg_extraction_doc"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    chunks_processed = Column(Integer, nullable=False, default=0)
    total_chunks = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
