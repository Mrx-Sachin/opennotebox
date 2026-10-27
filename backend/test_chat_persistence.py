"""Conversation JSON-column round-trip test.

Guards a real bug class: `Conversation.document_ids` and `page_scope` are JSON
columns, and mutating them in place (rather than reassigning) leaves SQLAlchemy
unaware anything changed, so the update silently never reaches the database.

Runs against a throwaway SQLite file rather than the application's Postgres:
the models themselves are backend-agnostic, and this keeps the test hermetic
(no service container, no shared state, no risk of a failed run leaving rows
behind in a real database). The previous version of this file called
`_sync_init_db()` against the live database, which both no longer exists
(schema creation moved to Alembic) and was never safe to run from a test.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Conversation, User


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_conversation_json_columns_round_trip(session, tmp_path):
    user = User(username="test_persistent_user", password_hash="pw")
    session.add(user)
    session.commit()

    conv = Conversation(user_id=user.id, title="Test Chat Persistence")
    session.add(conv)
    session.commit()

    conv.document_ids = [42]
    conv.page_scope = {"all": True}
    session.commit()
    conv_id = conv.id

    # Re-read through a completely fresh session so the assertions cannot be
    # satisfied by the identity map still holding the in-memory object.
    session.expunge_all()
    fetched = session.query(Conversation).filter(Conversation.id == conv_id).first()

    assert fetched is not None
    assert fetched.document_ids == [42]
    assert fetched.page_scope == {"all": True}
