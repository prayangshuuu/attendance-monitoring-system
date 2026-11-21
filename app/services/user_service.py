from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.core.security import hash_password, verify_password
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.common import Page
from app.schemas.user import PasswordChange, UserCreate, UserRead, UserUpdate


class UserService:
    def __init__(self, user_repo: UserRepository | None = None) -> None:
        self.user_repo = user_repo or user_repository

    async def create_user(
        self,
        session: AsyncSession,
        data: UserCreate,
    ) -> User:
        existing_email = await self.user_repo.get_by_email(session, data.email)
        if existing_email is not None:
            raise ConflictError(
                f"User with email '{data.email}' already exists",
                code="USER_EMAIL_EXISTS",
            )

        if data.student_id is not None:
            existing_student_id = await self.user_repo.get_by_student_id(session, data.student_id)
            if existing_student_id is not None:
                raise ConflictError(
                    f"User with student_id '{data.student_id}' already exists",
                    code="STUDENT_ID_EXISTS",
                )

        user = User(
            email=data.email,
            full_name=data.full_name,
            role=data.role.value if isinstance(data.role, UserRole) else str(data.role),
            student_id=data.student_id,
            hashed_password=hash_password(data.password),
            is_active=True,
        )
        await self.user_repo.create(session, user)
        await session.commit()
        await session.refresh(user)
        return user

    async def list_users(
        self,
        session: AsyncSession,
        page: int = 1,
        size: int = 20,
        role: str | UserRole | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> Page[UserRead]:
        users, total = await self.user_repo.list(
            session=session,
            page=page,
            size=size,
            role=role,
            is_active=is_active,
            search=search,
        )
        user_reads = [UserRead.model_validate(u) for u in users]
        return Page[UserRead](
            items=user_reads,
            total=total,
            page=page,
            size=size,
        )

    async def get_user_by_id(
        self,
        session: AsyncSession,
        user_id: UUID | str,
    ) -> User:
        user = await self.user_repo.get_by_id(session, user_id)
        if user is None:
            raise NotFoundError(
                f"User with id '{user_id}' not found",
                code="USER_NOT_FOUND",
            )
        return user

    async def update_user(
        self,
        session: AsyncSession,
        user_id: UUID | str,
        data: UserUpdate,
    ) -> User:
        user = await self.get_user_by_id(session, user_id)

        target_role = (
            data.role.value
            if isinstance(data.role, UserRole)
            else (str(data.role) if data.role is not None else user.role)
        )

        if "student_id" in data.model_fields_set:
            target_student_id = data.student_id
        else:
            target_student_id = user.student_id

        if target_role == UserRole.STUDENT.value:
            if not target_student_id:
                raise ValidationAppError(
                    "student_id is required for student role",
                    code="STUDENT_ID_REQUIRED",
                )
        else:
            if "student_id" in data.model_fields_set and data.student_id is not None:
                raise ValidationAppError(
                    f"student_id must be None for {target_role} role",
                    code="INVALID_STUDENT_ID",
                )
            if data.role is not None and data.role != UserRole.STUDENT:
                target_student_id = None

        if data.email is not None:
            normalized_email = data.email.lower().strip()
            if normalized_email != user.email.lower().strip():
                existing = await self.user_repo.get_by_email(session, normalized_email)
                if existing is not None and existing.id != user.id:
                    raise ConflictError(
                        f"User with email '{data.email}' already exists",
                        code="USER_EMAIL_EXISTS",
                    )
                user.email = normalized_email

        if target_student_id is not None:
            normalized_stu_id = target_student_id.strip()
            if (
                user.student_id is None
                or normalized_stu_id.lower() != user.student_id.lower().strip()
            ):
                existing_stu = await self.user_repo.get_by_student_id(session, normalized_stu_id)
                if existing_stu is not None and existing_stu.id != user.id:
                    raise ConflictError(
                        f"User with student_id '{target_student_id}' already exists",
                        code="STUDENT_ID_EXISTS",
                    )
            user.student_id = normalized_stu_id
        else:
            user.student_id = None

        if data.full_name is not None:
            user.full_name = data.full_name.strip()

        if data.role is not None:
            user.role = target_role

        if data.is_active is not None:
            user.is_active = data.is_active

        await self.user_repo.update(session, user)
        await session.commit()
        await session.refresh(user)
        return user

    async def deactivate_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        admin_id: UUID,
    ) -> User:
        if user_id == admin_id:
            raise ValidationAppError(
                "Admin cannot deactivate themselves",
                code="SELF_DEACTIVATION_FORBIDDEN",
            )
        user = await self.get_user_by_id(session, user_id)
        user.is_active = False
        await self.user_repo.update(session, user)
        await session.commit()
        await session.refresh(user)
        return user

    async def change_password(
        self,
        session: AsyncSession,
        user_id: UUID,
        data: PasswordChange,
    ) -> None:
        user = await self.get_user_by_id(session, user_id)
        if not verify_password(data.old_password, user.hashed_password):
            raise ValidationAppError(
                "Current password is incorrect",
                code="INVALID_PASSWORD",
            )
        user.hashed_password = hash_password(data.new_password)
        await self.user_repo.update(session, user)
        await session.commit()


user_service = UserService()
