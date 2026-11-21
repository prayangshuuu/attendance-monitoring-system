import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from tests.factories import make_course, make_enrollment, make_user


@pytest.mark.asyncio
async def test_create_course_endpoint(
    client: AsyncClient,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    # 1. Unauthenticated -> 401
    payload = {
        "code": "CS101",
        "title": "Intro to Computer Science",
        "teacher_id": str(teacher_user.id),
        "min_attendance_pct": 80.0,
    }
    res = await client.post("/api/v1/courses", json=payload)
    assert res.status_code == 401

    # 2. Student -> 403
    res = await client.post("/api/v1/courses", headers=auth_headers(student_user), json=payload)
    assert res.status_code == 403

    # 3. Teacher -> 403
    res = await client.post("/api/v1/courses", headers=auth_headers(teacher_user), json=payload)
    assert res.status_code == 403

    # 4. Admin -> 201 Created
    res = await client.post("/api/v1/courses", headers=auth_headers(admin_user), json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["code"] == "CS101"
    assert data["title"] == "Intro to Computer Science"
    assert data["teacher_id"] == str(teacher_user.id)
    assert data["teacher"]["id"] == str(teacher_user.id)
    assert data["teacher"]["email"] == teacher_user.email
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data

    # 5. Duplicate code -> 409
    res_dup = await client.post("/api/v1/courses", headers=auth_headers(admin_user), json=payload)
    assert res_dup.status_code == 409

    # 6. Invalid teacher -> 400
    invalid_teacher_payload = {
        "code": "CS102",
        "title": "Data Structures",
        "teacher_id": str(student_user.id),  # student is not teacher
    }
    res_bad = await client.post(
        "/api/v1/courses", headers=auth_headers(admin_user), json=invalid_teacher_payload
    )
    assert res_bad.status_code == 400


@pytest.mark.asyncio
async def test_list_courses_endpoint(
    client: AsyncClient,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    t2 = await make_user(db_session, role=UserRole.TEACHER)
    c1 = await make_course(db_session, teacher=teacher_user, code="LST_C1", title="Algorithms")
    await make_course(db_session, teacher=t2, code="LST_C2", title="Databases")
    await make_enrollment(db_session, course=c1, student=student_user)

    # 1. Admin lists all
    res_admin = await client.get("/api/v1/courses", headers=auth_headers(admin_user))
    assert res_admin.status_code == 200
    admin_data = res_admin.json()
    admin_codes = [c["code"] for c in admin_data["items"]]
    assert "LST_C1" in admin_codes
    assert "LST_C2" in admin_codes

    # 2. Teacher sees only own
    res_teacher = await client.get("/api/v1/courses", headers=auth_headers(teacher_user))
    assert res_teacher.status_code == 200
    t_data = res_teacher.json()
    t_codes = [c["code"] for c in t_data["items"]]
    assert "LST_C1" in t_codes
    assert "LST_C2" not in t_codes

    # 3. Student sees only enrolled
    res_student = await client.get("/api/v1/courses", headers=auth_headers(student_user))
    assert res_student.status_code == 200
    s_data = res_student.json()
    s_codes = [c["code"] for c in s_data["items"]]
    assert "LST_C1" in s_codes
    assert "LST_C2" not in s_codes

    # 4. Filters & search
    res_search = await client.get("/api/v1/courses?search=Data", headers=auth_headers(admin_user))
    assert res_search.status_code == 200
    search_data = res_search.json()
    assert any(c["code"] == "LST_C2" for c in search_data["items"])
    assert not any(c["code"] == "LST_C1" for c in search_data["items"])


@pytest.mark.asyncio
async def test_get_course_by_id_endpoint(
    client: AsyncClient,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    t2 = await make_user(db_session, role=UserRole.TEACHER)
    course = await make_course(db_session, teacher=teacher_user, code="GET_C1")
    other_student = await make_user(db_session, role=UserRole.STUDENT)
    await make_enrollment(db_session, course=course, student=student_user)

    # 1. Admin -> 200
    res = await client.get(f"/api/v1/courses/{course.id}", headers=auth_headers(admin_user))
    assert res.status_code == 200
    assert res.json()["code"] == "GET_C1"

    # 2. Course Teacher -> 200
    res = await client.get(f"/api/v1/courses/{course.id}", headers=auth_headers(teacher_user))
    assert res.status_code == 200

    # 3. Enrolled Student -> 200
    res = await client.get(f"/api/v1/courses/{course.id}", headers=auth_headers(student_user))
    assert res.status_code == 200

    # 4. Other Teacher -> 403
    res = await client.get(f"/api/v1/courses/{course.id}", headers=auth_headers(t2))
    assert res.status_code == 403

    # 5. Non-enrolled Student -> 403
    res = await client.get(f"/api/v1/courses/{course.id}", headers=auth_headers(other_student))
    assert res.status_code == 403

    # 6. Non-existent -> 404
    res = await client.get(f"/api/v1/courses/{uuid.uuid4()}", headers=auth_headers(admin_user))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_update_course_endpoint(
    client: AsyncClient,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user, code="PATCH_C1")

    update_payload = {"title": "Updated Course Title", "min_attendance_pct": 85.0}

    # 1. Student -> 403
    res = await client.patch(
        f"/api/v1/courses/{course.id}",
        headers=auth_headers(student_user),
        json=update_payload,
    )
    assert res.status_code == 403

    # 2. Teacher -> 403
    res = await client.patch(
        f"/api/v1/courses/{course.id}",
        headers=auth_headers(teacher_user),
        json=update_payload,
    )
    assert res.status_code == 403

    # 3. Admin -> 200
    res = await client.patch(
        f"/api/v1/courses/{course.id}",
        headers=auth_headers(admin_user),
        json=update_payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Updated Course Title"
    assert data["min_attendance_pct"] == 85.0


@pytest.mark.asyncio
async def test_delete_course_endpoint(
    client: AsyncClient,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user, code="DEL_C1", is_active=True)

    # 1. Student -> 403
    res = await client.delete(f"/api/v1/courses/{course.id}", headers=auth_headers(student_user))
    assert res.status_code == 403

    # 2. Teacher -> 403
    res = await client.delete(f"/api/v1/courses/{course.id}", headers=auth_headers(teacher_user))
    assert res.status_code == 403

    # 3. Admin -> 204
    res = await client.delete(f"/api/v1/courses/{course.id}", headers=auth_headers(admin_user))
    assert res.status_code == 204

    # Verify deactivated
    res_get = await client.get(f"/api/v1/courses/{course.id}", headers=auth_headers(admin_user))
    assert res_get.status_code == 200
    assert res_get.json()["is_active"] is False


@pytest.mark.asyncio
async def test_enrollments_endpoints(
    client: AsyncClient,
    admin_user: User,
    teacher_user: User,
    student_user: User,
    db_session: AsyncSession,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    course = await make_course(db_session, teacher=teacher_user, code="ENR_C1")
    s2 = await make_user(db_session, role=UserRole.STUDENT)
    other_teacher = await make_user(db_session, role=UserRole.TEACHER)

    # 1. Other teacher cannot enroll -> 403
    res = await client.post(
        f"/api/v1/courses/{course.id}/enrollments",
        headers=auth_headers(other_teacher),
        json={"student_ids": [str(student_user.id)]},
    )
    assert res.status_code == 403

    # 2. Teacher enrolls student_user and s2 -> 200
    res = await client.post(
        f"/api/v1/courses/{course.id}/enrollments",
        headers=auth_headers(teacher_user),
        json={"student_ids": [str(student_user.id), str(s2.id)]},
    )
    assert res.status_code in (200, 201)
    data = res.json()
    assert str(student_user.id) in data["enrolled"]
    assert str(s2.id) in data["enrolled"]
    assert len(data["skipped"]) == 0

    # 3. Admin enrolls student_user again -> student_user skipped
    res_skip = await client.post(
        f"/api/v1/courses/{course.id}/enrollments",
        headers=auth_headers(admin_user),
        json={"student_ids": [str(student_user.id)]},
    )
    assert res_skip.status_code in (200, 201)
    skip_data = res_skip.json()
    assert str(student_user.id) in skip_data["skipped"]
    assert len(skip_data["enrolled"]) == 0

    # 4. List enrolled students
    # Student cannot list -> 403
    res_s_list = await client.get(
        f"/api/v1/courses/{course.id}/students", headers=auth_headers(student_user)
    )
    assert res_s_list.status_code == 403

    # Teacher can list -> 200
    res_t_list = await client.get(
        f"/api/v1/courses/{course.id}/students", headers=auth_headers(teacher_user)
    )
    assert res_t_list.status_code == 200
    students_data = res_t_list.json()
    assert students_data["total"] == 2
    student_ids = [s["id"] for s in students_data["items"]]
    assert str(student_user.id) in student_ids
    assert str(s2.id) in student_ids

    # 5. Unenroll student
    # Other teacher cannot unenroll -> 403
    res_un_denied = await client.delete(
        f"/api/v1/courses/{course.id}/enrollments/{student_user.id}",
        headers=auth_headers(other_teacher),
    )
    assert res_un_denied.status_code == 403

    # Course teacher can unenroll -> 204
    res_un = await client.delete(
        f"/api/v1/courses/{course.id}/enrollments/{student_user.id}",
        headers=auth_headers(teacher_user),
    )
    assert res_un.status_code == 204

    # Check list now has total 1
    res_t_list2 = await client.get(
        f"/api/v1/courses/{course.id}/students", headers=auth_headers(teacher_user)
    )
    assert res_t_list2.json()["total"] == 1

    # Unenroll again -> 404
    res_un_again = await client.delete(
        f"/api/v1/courses/{course.id}/enrollments/{student_user.id}",
        headers=auth_headers(teacher_user),
    )
    assert res_un_again.status_code == 404
