from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.course_repository import CourseRepository, course_repository
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.common import Page
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate
from app.services.permissions import ensure_can_view_course


class CourseService:
    def __init__(
        self,
        course_repo: CourseRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.course_repo = course_repo or course_repository
        self.user_repo = user_repo or user_repository

    async def create_course(
        self,
        session: AsyncSession,
        data: CourseCreate,
    ) -> Course:
        teacher = await self.user_repo.get_by_id(session, data.teacher_id)
        if teacher is None or not teacher.is_active or teacher.role != UserRole.TEACHER:
            raise ValidationAppError(
                "Teacher does not exist, is inactive, or does not have teacher role",
                code="INVALID_TEACHER",
            )

        normalized_code = data.code.strip().upper()
        existing = await self.course_repo.get_by_code(session, normalized_code)
        if existing is not None:
            raise ConflictError(
                f"Course with code '{normalized_code}' already exists",
                code="COURSE_CODE_EXISTS",
            )

        course = Course(
            code=normalized_code,
            title=data.title.strip(),
            teacher_id=data.teacher_id,
            min_attendance_pct=data.min_attendance_pct,
            is_active=data.is_active,
        )
        await self.course_repo.create(session, course)
        await session.commit()
        loaded = await self.course_repo.get_by_id(session, course.id)
        return loaded if loaded is not None else course

    async def get_course(
        self,
        session: AsyncSession,
        course_id: UUID,
        current_user: User,
    ) -> Course:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError(
                f"Course with id '{course_id}' not found",
                code="COURSE_NOT_FOUND",
            )

        is_enrolled = False
        if current_user.role == UserRole.STUDENT:
            stmt = select(func.count(Enrollment.id)).where(
                Enrollment.course_id == course.id,
                Enrollment.student_id == current_user.id,
            )
            count_result = await session.execute(stmt)
            is_enrolled = count_result.scalar_one() > 0

        ensure_can_view_course(current_user, course, is_enrolled=is_enrolled)
        return course

    async def list_courses(
        self,
        session: AsyncSession,
        current_user: User,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> Page[CourseRead]:
        courses, total = await self.course_repo.list(
            session=session,
            user=current_user,
            page=page,
            size=size,
            search=search,
            is_active=is_active,
        )
        course_reads = [CourseRead.model_validate(c) for c in courses]
        return Page[CourseRead](
            items=course_reads,
            total=total,
            page=page,
            size=size,
        )

    async def update_course(
        self,
        session: AsyncSession,
        course_id: UUID,
        data: CourseUpdate,
    ) -> Course:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError(
                f"Course with id '{course_id}' not found",
                code="COURSE_NOT_FOUND",
            )

        update_dict = data.model_dump(exclude_unset=True)
        if "teacher_id" in update_dict and update_dict["teacher_id"] is not None:
            new_teacher = await self.user_repo.get_by_id(session, update_dict["teacher_id"])
            if (
                new_teacher is None
                or not new_teacher.is_active
                or new_teacher.role != UserRole.TEACHER
            ):
                raise ValidationAppError(
                    "Teacher does not exist, is inactive, or does not have teacher role",
                    code="INVALID_TEACHER",
                )
            course.teacher_id = update_dict["teacher_id"]

        if "code" in update_dict and update_dict["code"] is not None:
            normalized_code = update_dict["code"].strip().upper()
            existing = await self.course_repo.get_by_code(session, normalized_code)
            if existing is not None and existing.id != course.id:
                raise ConflictError(
                    f"Course with code '{normalized_code}' already exists",
                    code="COURSE_CODE_EXISTS",
                )
            course.code = normalized_code

        if "title" in update_dict and update_dict["title"] is not None:
            course.title = update_dict["title"].strip()

        if "min_attendance_pct" in update_dict and update_dict["min_attendance_pct"] is not None:
            course.min_attendance_pct = update_dict["min_attendance_pct"]

        if "is_active" in update_dict and update_dict["is_active"] is not None:
            course.is_active = update_dict["is_active"]

        await self.course_repo.update(session, course)
        await session.commit()
        loaded = await self.course_repo.get_by_id(session, course.id)
        return loaded if loaded is not None else course

    async def deactivate_course(
        self,
        session: AsyncSession,
        course_id: UUID,
    ) -> None:
        course = await self.course_repo.get_by_id(session, course_id)
        if course is None:
            raise NotFoundError(
                f"Course with id '{course_id}' not found",
                code="COURSE_NOT_FOUND",
            )
        course.is_active = False
        await self.course_repo.update(session, course)
        await session.commit()


course_service = CourseService()
