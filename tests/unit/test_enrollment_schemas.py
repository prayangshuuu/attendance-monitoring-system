from datetime import UTC, datetime
from uuid import uuid4

from app.models.enums import UserRole
from app.schemas.enrollment import EnrollmentCreate, EnrollmentRead, EnrollmentResult
from app.schemas.user import UserRead


def test_enrollment_schemas() -> None:
    s1 = uuid4()
    s2 = uuid4()
    create = EnrollmentCreate(student_ids=[s1, s2])
    assert create.student_ids == [s1, s2]

    result = EnrollmentResult(enrolled=[s1], skipped=[s2])
    assert result.enrolled == [s1]
    assert result.skipped == [s2]

    now = datetime.now(UTC)
    c_id = uuid4()
    read = EnrollmentRead(
        id=uuid4(),
        course_id=c_id,
        student_id=s1,
        enrolled_at=now,
        student=UserRead(
            id=s1,
            email="s1@example.com",
            full_name="Student One",
            role=UserRole.STUDENT,
            student_id="STU1",
            is_active=True,
            created_at=now,
            updated_at=now,
        ),
    )
    assert read.course_id == c_id
    assert read.student_id == s1
    assert read.student is not None
    assert read.student.full_name == "Student One"
