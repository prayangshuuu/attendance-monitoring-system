from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationAppError
from app.models.enrollment import Enrollment
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.course_repository import CourseRepository, course_repository
from app.repositories.enrollment_repository import EnrollmentRepository, enrollment_repository
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.common import Page
from app.schemas.enrollment import EnrollmentResult
from app.schemas.user import UserRead
from app.services.permissions import ensure_can_manage_course


class EnrollmentService:
    def __init__(
        self,
        enrollment_repo: EnrollmentRepository | None = None,
        course_repo: CourseRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.enrollment_repo = enrollment_repo or enrollment_repository
        self.course_repo = course_repo or course_repository
        self.user_repo = user_repo or user_repository

    async def enroll_students(
        self,
        session: AsyncSession,
        course_id: UUID,
        student_ids: list[UUID],
        current_user: User,
    ) -> EnrollmentResult:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError(
                f"Course with id '{course_id}' not found",
                code="COURSE_NOT_FOUND",
            )

        ensure_can_manage_course(current_user, course)

        if not student_ids:
            return EnrollmentResult(enrolled=[], skipped=[])

        unique_student_ids = list(dict.fromkeys(student_ids))

        users_stmt = select(User).where(User.id.in_(unique_student_ids))
        result = await session.execute(users_stmt)
        found_users = {u.id: u for u in result.scalars().all()}

        for s_id in unique_student_ids:
            u = found_users.get(s_id)
            if u is None:
                raise ValidationAppError(
                    f"User with id '{s_id}' not found",
                    code="USER_NOT_FOUND",
                )
            if not u.is_active:
                raise ValidationAppError(
                    f"User with id '{s_id}' is inactive",
                    code="USER_INACTIVE",
                )
            if u.role != UserRole.STUDENT:
                raise ValidationAppError(
                    f"User with id '{s_id}' is not a student",
                    code="INVALID_ROLE",
                )

        already_enrolled = await self.enrollment_repo.get_enrolled_student_ids(
            session, course_id, unique_student_ids
        )

        enrolled: list[UUID] = []
        skipped: list[UUID] = []
        new_enrollments: list[Enrollment] = []

        for s_id in unique_student_ids:
            if s_id in already_enrolled:
                skipped.append(s_id)
            else:
                enrolled.append(s_id)
                new_enrollments.append(Enrollment(course_id=course_id, student_id=s_id))

        if new_enrollments:
            await self.enrollment_repo.create_enrollments(session, new_enrollments)
            await session.commit()

        return EnrollmentResult(enrolled=enrolled, skipped=skipped)

    async def unenroll(
        self,
        session: AsyncSession,
        course_id: UUID,
        student_id: UUID,
        current_user: User,
    ) -> None:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError(
                f"Course with id '{course_id}' not found",
                code="COURSE_NOT_FOUND",
            )

        ensure_can_manage_course(current_user, course)

        enrollment = await self.enrollment_repo.get_enrollment(session, course_id, student_id)
        if enrollment is None:
            raise NotFoundError(
                f"Student with id '{student_id}' is not enrolled in course '{course_id}'",
                code="ENROLLMENT_NOT_FOUND",
            )

        await self.enrollment_repo.delete_enrollment(session, course_id, student_id)
        await session.commit()

    async def list_course_students(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
        page: int = 1,
        size: int = 20,
    ) -> Page[UserRead]:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError(
                f"Course with id '{course_id}' not found",
                code="COURSE_NOT_FOUND",
            )

        ensure_can_manage_course(current_user, course)

        students, total = await self.enrollment_repo.list_course_students(
            session=session,
            course_id=course_id,
            page=page,
            size=size,
        )
        student_reads = [UserRead.model_validate(s) for s in students]
        return Page[UserRead](
            items=student_reads,
            total=total,
            page=page,
            size=size,
        )


enrollment_service = EnrollmentService()
