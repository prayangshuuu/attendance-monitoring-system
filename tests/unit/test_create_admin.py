from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_password
from app.models.enums import UserRole
from scripts.create_admin import bootstrap_admin


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_and_is_idempotent(db_session: AsyncSession) -> None:
    admin = await bootstrap_admin(db_session)
    assert admin is not None
    assert admin.email == settings.FIRST_ADMIN_EMAIL.lower()
    assert admin.role == UserRole.ADMIN
    assert admin.is_active is True
    assert verify_password(settings.FIRST_ADMIN_PASSWORD, admin.hashed_password) is True

    # Second call returns existing admin
    admin2 = await bootstrap_admin(db_session)
    assert admin2 is not None
    assert admin2.id == admin.id


@pytest.mark.asyncio
async def test_bootstrap_admin_skips_in_testing_without_session() -> None:
    res = await bootstrap_admin(None)
    assert res is None


@pytest.mark.asyncio
async def test_bootstrap_admin_handles_exception_gracefully() -> None:
    with patch("scripts.create_admin.settings") as mock_settings:
        mock_settings.ENVIRONMENT = "production"
        with patch("scripts.create_admin.async_session_maker") as mock_maker:
            mock_maker.side_effect = Exception("DB offline")
            res = await bootstrap_admin(None)
            assert res is None
