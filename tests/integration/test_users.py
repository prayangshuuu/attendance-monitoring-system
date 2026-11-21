import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from tests.factories import make_user


@pytest.mark.asyncio
async def test_create_user_student_success(
    client: AsyncClient,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    headers = auth_headers(admin_user)
    payload = {
        "email": "NewStudent@Example.Com",
        "full_name": "New Student",
        "role": "student",
        "password": "Password123",
        "student_id": "STU_INT_1",
    }
    response = await client.post("/api/v1/users", headers=headers, json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newstudent@example.com"
    assert data["full_name"] == "New Student"
    assert data["role"] == "student"
    assert data["student_id"] == "STU_INT_1"
    assert data["is_active"] is True
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_create_user_teacher_success(
    client: AsyncClient,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    headers = auth_headers(admin_user)
    payload = {
        "email": "teacher_create@example.com",
        "full_name": "Teacher Two",
        "role": "teacher",
        "password": "Password123",
    }
    response = await client.post("/api/v1/users", headers=headers, json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "teacher"
    assert data["student_id"] is None


@pytest.mark.asyncio
async def test_create_user_duplicate_email(
    client: AsyncClient,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    headers = auth_headers(admin_user)
    payload = {
        "email": admin_user.email,
        "full_name": "Duplicate Admin Email",
        "role": "teacher",
        "password": "Password123",
    }
    response = await client.post("/api/v1/users", headers=headers, json=payload)
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "USER_EMAIL_EXISTS"


@pytest.mark.asyncio
async def test_create_user_duplicate_student_id(
    client: AsyncClient,
    admin_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    headers = auth_headers(admin_user)
    payload = {
        "email": "diff_stu@example.com",
        "full_name": "Duplicate Student ID",
        "role": "student",
        "password": "Password123",
        "student_id": student_user.student_id,
    }
    response = await client.post("/api/v1/users", headers=headers, json=payload)
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "STUDENT_ID_EXISTS"


@pytest.mark.asyncio
async def test_create_user_invalid_rules(
    client: AsyncClient,
    admin_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    headers = auth_headers(admin_user)

    # Student without student_id
    res1 = await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "nostu@example.com",
            "full_name": "No Stu ID",
            "role": "student",
            "password": "Password123",
        },
    )
    assert res1.status_code == 422

    # Teacher with student_id
    res2 = await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "teacherwithstu@example.com",
            "full_name": "Teacher Stu",
            "role": "teacher",
            "password": "Password123",
            "student_id": "STU_FORBIDDEN",
        },
    )
    assert res2.status_code == 422

    # Weak password
    res3 = await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "weakpwd@example.com",
            "full_name": "Weak Pwd",
            "role": "student",
            "password": "weak",
            "student_id": "STU_WEAK",
        },
    )
    assert res3.status_code == 422


@pytest.mark.asyncio
async def test_create_user_forbidden_and_unauthenticated(
    client: AsyncClient,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    payload = {
        "email": "forbidden_create@example.com",
        "full_name": "Forbidden",
        "role": "student",
        "password": "Password123",
        "student_id": "STU_FORBID",
    }
    # Student -> 403
    res_forbidden = await client.post(
        "/api/v1/users",
        headers=auth_headers(student_user),
        json=payload,
    )
    assert res_forbidden.status_code == 403

    # Unauthenticated -> 401
    res_unauth = await client.post("/api/v1/users", json=payload)
    assert res_unauth.status_code == 401


@pytest.mark.asyncio
async def test_list_users_pagination_and_filters(
    client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    headers = auth_headers(admin_user)
    # Seed known users
    u_stu = await make_user(
        db_session,
        email="filter_stu@example.com",
        full_name="Filter Student",
        role=UserRole.STUDENT,
        student_id="STU_FILTER_1",
        is_active=True,
    )
    u_tea = await make_user(
        db_session,
        email="filter_tea@example.com",
        full_name="Filter Teacher",
        role=UserRole.TEACHER,
        is_active=False,
    )

    # List default
    res = await client.get("/api/v1/users", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 2

    # Pagination: size=2, page=1
    res_p1 = await client.get("/api/v1/users?page=1&size=2", headers=headers)
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert len(data_p1["items"]) == 2
    assert data_p1["page"] == 1
    assert data_p1["size"] == 2

    # Filter by role
    res_role = await client.get("/api/v1/users?role=student", headers=headers)
    assert res_role.status_code == 200
    items_role = res_role.json()["items"]
    assert all(u["role"] == "student" for u in items_role)
    assert any(u["email"] == u_stu.email for u in items_role)

    # Filter by is_active=false
    res_inactive = await client.get("/api/v1/users?is_active=false", headers=headers)
    assert res_inactive.status_code == 200
    items_inactive = res_inactive.json()["items"]
    assert any(u["email"] == u_tea.email for u in items_inactive)

    # Search by full_name
    res_search = await client.get("/api/v1/users?search=Filter Student", headers=headers)
    assert res_search.status_code == 200
    items_search = res_search.json()["items"]
    assert any(u["email"] == u_stu.email for u in items_search)

    # Search by student_id
    res_search_id = await client.get("/api/v1/users?search=STU_FILTER_1", headers=headers)
    assert res_search_id.status_code == 200
    assert any(u["student_id"] == "STU_FILTER_1" for u in res_search_id.json()["items"])


@pytest.mark.asyncio
async def test_list_users_forbidden(
    client: AsyncClient,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    res = await client.get("/api/v1/users", headers=auth_headers(student_user))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_user_by_id(
    client: AsyncClient,
    admin_user: User,
    student_user: User,
    teacher_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    # Student reads self -> 200
    res_self = await client.get(
        f"/api/v1/users/{student_user.id}",
        headers=auth_headers(student_user),
    )
    assert res_self.status_code == 200
    assert res_self.json()["id"] == str(student_user.id)

    # Student reads another user -> 403
    res_other = await client.get(
        f"/api/v1/users/{teacher_user.id}",
        headers=auth_headers(student_user),
    )
    assert res_other.status_code == 403

    # Admin reads any user -> 200
    res_admin = await client.get(
        f"/api/v1/users/{student_user.id}",
        headers=auth_headers(admin_user),
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["id"] == str(student_user.id)

    # Unknown ID by admin -> 404
    res_404 = await client.get(
        f"/api/v1/users/{uuid.uuid4()}",
        headers=auth_headers(admin_user),
    )
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_patch_user(
    client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    user = await make_user(
        db_session,
        email="patch_target@example.com",
        full_name="Patch Before",
        role=UserRole.STUDENT,
        student_id="STU_PATCH_1",
    )
    headers = auth_headers(admin_user)

    # Update full_name and is_active
    res = await client.patch(
        f"/api/v1/users/{user.id}",
        headers=headers,
        json={"full_name": "Patch After", "is_active": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["full_name"] == "Patch After"
    assert data["is_active"] is False

    # Update role to teacher (clears student_id)
    res_role = await client.patch(
        f"/api/v1/users/{user.id}",
        headers=headers,
        json={"role": "teacher"},
    )
    assert res_role.status_code == 200
    assert res_role.json()["role"] == "teacher"
    assert res_role.json()["student_id"] is None

    # Unknown ID -> 404
    res_404 = await client.patch(
        f"/api/v1/users/{uuid.uuid4()}",
        headers=headers,
        json={"full_name": "Ghost"},
    )
    assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_soft_delete(
    client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    target = await make_user(
        db_session,
        email="deactivate_target@example.com",
        password="TargetPassword123",
        role=UserRole.STUDENT,
    )
    headers = auth_headers(admin_user)

    # Soft delete -> 204
    res = await client.delete(f"/api/v1/users/{target.id}", headers=headers)
    assert res.status_code == 204

    # Deactivated user cannot log in
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": target.email, "password": "TargetPassword123"},
    )
    assert login_res.status_code == 401
    assert login_res.json()["error"]["code"] == "USER_INACTIVE"

    # Admin self-deactivation -> 400
    self_res = await client.delete(f"/api/v1/users/{admin_user.id}", headers=headers)
    assert self_res.status_code == 400
    assert self_res.json()["error"]["code"] == "SELF_DEACTIVATION_FORBIDDEN"


@pytest.mark.asyncio
async def test_change_password(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    user = await make_user(
        db_session,
        email="pwchange@example.com",
        password="OldPassword123",
    )
    headers = auth_headers(user)

    # Wrong old password -> 400
    wrong_res = await client.post(
        "/api/v1/users/me/password",
        headers=headers,
        json={"old_password": "WrongPassword123", "new_password": "NewPassword123"},
    )
    assert wrong_res.status_code == 400
    assert wrong_res.json()["error"]["code"] == "INVALID_PASSWORD"

    # Correct password change -> 204
    success_res = await client.post(
        "/api/v1/users/me/password",
        headers=headers,
        json={"old_password": "OldPassword123", "new_password": "NewPassword123"},
    )
    assert success_res.status_code == 204

    # Login with new password -> 200
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "NewPassword123"},
    )
    assert login_res.status_code == 200
