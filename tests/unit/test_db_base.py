from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class DummyModel(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "dummy_models"

    name: Mapped[str] = mapped_column()


def test_base_metadata_naming_conventions() -> None:
    convention = Base.metadata.naming_convention
    assert convention["ix"] == "%(column_0_label)s_idx"
    assert convention["uq"] == "%(table_name)s_%(column_0_name)s_key"
    assert convention["ck"] == "%(table_name)s_%(constraint_name)s_check"
    assert convention["fk"] == "%(table_name)s_%(column_0_name)s_fkey"
    assert convention["pk"] == "%(table_name)s_pkey"


def test_uuid_and_timestamp_mixins() -> None:
    assert "created_at" in DummyModel.__table__.columns
    assert "updated_at" in DummyModel.__table__.columns
    assert "id" in DummyModel.__table__.columns
    assert DummyModel.__table__.c.id.primary_key
    assert DummyModel.__table__.c.id.default is not None
    assert getattr(DummyModel.__table__.c.id.default.arg, "__name__", "") == "uuid4"
    assert DummyModel.__table__.c.created_at.server_default is not None
    assert DummyModel.__table__.c.updated_at.server_default is not None
