from collections.abc import AsyncGenerator, Callable

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CurrentUser, TeacherOrAdmin
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from tests.factories import make_user

role_test_router = APIRouter(prefix="/test-roles")


@role_test_router.get("/admin-only")
async def admin_only_endpoint(admin: AdminUser) -> dict[str, str]:
    return {"message": f"Hello Admin {admin.full_name}"}


@role_test_router.get("/teacher-or-admin")
async def teacher_or_admin_endpoint(user: TeacherOrAdmin) -> dict[str, str]:
    return {"message": f"Hello {user.role}"}


@role_test_router.get("/current-user-only")
async def current_user_endpoint(user: CurrentUser) -> dict[str, str]:
    return {"id": str(user.id)}


@pytest.fixture
def role_app(app: FastAPI) -> FastAPI:
    app.include_router(role_test_router)
    return app


@pytest.fixture
async def role_client(
    role_app: FastAPI,
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    role_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=role_app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_student_hitting_admin_only_route_forbidden(
    role_client: AsyncClient,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    response = await role_client.get(
        "/test-roles/admin-only",
        headers=auth_headers(student_user),
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == "PERMISSION_DENIED"
    assert "Operation not permitted" in data["error"]["message"]


@pytest.mark.asyncio
async def test_teacher_hitting_admin_only_route_forbidden(
    role_client: AsyncClient,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    response = await role_client.get(
        "/test-roles/admin-only",
        headers=auth_headers(teacher_user),
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_admin_hitting_admin_only_route_allowed(
    role_client: AsyncClient,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    response = await role_client.get(
        "/test-roles/admin-only",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert f"Hello Admin {admin_user.full_name}" in data["message"]


@pytest.mark.asyncio
async def test_teacher_or_admin_route_permissions(
    role_client: AsyncClient,
    student_user: User,
    teacher_user: User,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    # Student -> 403
    resp_student = await role_client.get(
        "/test-roles/teacher-or-admin",
        headers=auth_headers(student_user),
    )
    assert resp_student.status_code == 403
    assert resp_student.json()["error"]["code"] == "PERMISSION_DENIED"

    # Teacher -> 200
    resp_teacher = await role_client.get(
        "/test-roles/teacher-or-admin",
        headers=auth_headers(teacher_user),
    )
    assert resp_teacher.status_code == 200
    assert resp_teacher.json()["message"] == f"Hello {UserRole.TEACHER}"

    # Admin -> 200
    resp_admin = await role_client.get(
        "/test-roles/teacher-or-admin",
        headers=auth_headers(admin_user),
    )
    assert resp_admin.status_code == 200
    assert resp_admin.json()["message"] == f"Hello {UserRole.ADMIN}"


@pytest.mark.asyncio
async def test_inactive_user_rejected_by_current_user_dependency(
    role_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    inactive_user = await make_user(db_session, is_active=False)
    response = await role_client.get(
        "/test-roles/current-user-only",
        headers=auth_headers(inactive_user),
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_role_updated_in_db_immediately_effective_with_old_token(
    role_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    user = await make_user(db_session, role=UserRole.STUDENT)
    student_headers = auth_headers(user)

    resp_before = await role_client.get(
        "/test-roles/teacher-or-admin",
        headers=student_headers,
    )
    assert resp_before.status_code == 403

    user.role = UserRole.TEACHER.value
    user.student_id = None
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    resp_after = await role_client.get(
        "/test-roles/teacher-or-admin",
        headers=student_headers,
    )
    assert resp_after.status_code == 200
    assert resp_after.json()["message"] == f"Hello {UserRole.TEACHER.value}"


@pytest.mark.asyncio
async def test_openapi_swagger_security_scheme_exists(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "components" in schema
    assert "securitySchemes" in schema["components"]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
