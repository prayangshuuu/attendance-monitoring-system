import csv
import io
from typing import Any
from uuid import UUID

from pydantic import EmailStr, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.core.logging import audit_logger, user_id_ctx_var
from app.core.security import hash_password, verify_password
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import UserRepository, user_repository
from app.schemas.common import Page
from app.schemas.user import PASSWORD_REGEX, PasswordChange, UserCreate, UserRead, UserUpdate


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
        try:
            await self.user_repo.create(session, user)
            await session.commit()
            await session.refresh(user)
        except IntegrityError:
            await session.rollback()
            raise ConflictError(
                f"User with email '{data.email}' or student_id '{data.student_id}' already exists",
                code="USER_EXISTS",
            ) from None
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
        actor_id: UUID | str | None = None,
    ) -> User:
        user = await self.get_user_by_id(session, user_id)
        old_role = user.role

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

        try:
            await self.user_repo.update(session, user)
            await session.commit()
            await session.refresh(user)
        except IntegrityError:
            await session.rollback()
            raise ConflictError(
                "User conflict on update (email or student_id already taken)",
                code="USER_CONFLICT",
            ) from None

        if data.role is not None and old_role != target_role:
            effective_actor_id = str(actor_id) if actor_id else (user_id_ctx_var.get() or "system")
            audit_logger.info(
                "User role changed: actor_id=%s, target_user_id=%s, old_role=%s, new_role=%s",
                effective_actor_id,
                user.id,
                old_role,
                target_role,
                extra={
                    "actor_id": effective_actor_id,
                    "target_user_id": str(user.id),
                    "old_role": old_role,
                    "new_role": target_role,
                    "action": "user_role_change",
                },
            )

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
        try:
            await self.user_repo.update(session, user)
            await session.commit()
            await session.refresh(user)
        except IntegrityError:
            await session.rollback()
            raise ConflictError(
                "User conflict on update (email or student_id already taken)",
                code="USER_CONFLICT",
            ) from None
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

    async def import_users_csv(
        self,
        session: AsyncSession,
        file_content: bytes | str,
    ) -> list[User]:
        if isinstance(file_content, bytes):
            try:
                content = file_content.decode("utf-8-sig")
            except UnicodeDecodeError as err:
                raise ValidationAppError(
                    "Validation failed on one or more rows",
                    code="CSV_IMPORT_ERROR",
                    details={
                        "errors": [
                            {
                                "row": 0,
                                "field": "file",
                                "message": "Invalid UTF-8 encoding in CSV file",
                                "error": "Invalid UTF-8 encoding in CSV file",
                            }
                        ]
                    },
                ) from err
        else:
            content = file_content

        if not content.strip():
            raise ValidationAppError(
                "CSV file contains no user data rows",
                code="CSV_EMPTY_ERROR",
                details={
                    "errors": [
                        {
                            "row": 0,
                            "field": "file",
                            "message": "CSV file contains no user data rows",
                            "error": "CSV file contains no user data rows",
                        }
                    ]
                },
            )

        reader = csv.DictReader(io.StringIO(content))
        cleaned_fieldnames = (
            [f.strip().lower() for f in reader.fieldnames if f] if reader.fieldnames else []
        )
        reader.fieldnames = cleaned_fieldnames
        required_cols = {"email", "full_name", "role", "student_id", "password"}
        if not required_cols.issubset(set(cleaned_fieldnames)):
            missing = required_cols - set(cleaned_fieldnames)
            raise ValidationAppError(
                "Validation failed on one or more rows",
                code="CSV_IMPORT_ERROR",
                details={
                    "errors": [
                        {
                            "row": 0,
                            "field": "header",
                            "message": f"Missing required columns: {', '.join(sorted(missing))}",
                            "error": f"Missing required columns: {', '.join(sorted(missing))}",
                        }
                    ]
                },
            )

        errors: list[dict[str, Any]] = []
        seen_emails: set[str] = set()
        seen_student_ids: set[str] = set()
        rows_data: list[dict[str, Any]] = []

        rows = list(reader)
        if not rows:
            raise ValidationAppError(
                "CSV file contains no user data rows",
                code="CSV_EMPTY_ERROR",
                details={
                    "errors": [
                        {
                            "row": 0,
                            "field": "file",
                            "message": "CSV file contains no user data rows",
                            "error": "CSV file contains no user data rows",
                        }
                    ]
                },
            )

        valid_roles = {r.value for r in UserRole}

        for row_idx, row in enumerate(rows, start=1):
            email_val = (row.get("email") or "").strip().lower()
            full_name_val = (row.get("full_name") or "").strip()
            role_val = (row.get("role") or "").strip().lower()
            raw_stu_id = (row.get("student_id") or "").strip()
            student_id_val = raw_stu_id if raw_stu_id else None
            password_val = (row.get("password") or "").strip()

            row_errors: list[dict[str, Any]] = []

            # 1. Email format
            if not email_val:
                row_errors.append(
                    {
                        "row": row_idx,
                        "field": "email",
                        "message": "Email is required",
                        "error": "Email is required",
                    }
                )
            else:
                try:
                    TypeAdapter(EmailStr).validate_python(email_val)
                except Exception:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "email",
                            "message": f"Invalid email format: '{email_val}'",
                            "error": f"Invalid email format: '{email_val}'",
                        }
                    )

            # 2. Full name
            if not full_name_val:
                row_errors.append(
                    {
                        "row": row_idx,
                        "field": "full_name",
                        "message": "Full name is required",
                        "error": "Full name is required",
                    }
                )

            # 3. Role
            if role_val not in valid_roles:
                row_errors.append(
                    {
                        "row": row_idx,
                        "field": "role",
                        "message": (
                            f"Invalid role: '{role_val}'. "
                            f"Must be one of: {', '.join(sorted(valid_roles))}"
                        ),
                        "error": f"Invalid role: '{role_val}'",
                    }
                )

            # 4. Student ID rule
            if role_val == UserRole.STUDENT.value:
                if not student_id_val:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "student_id",
                            "message": "student_id is required for student role",
                            "error": "student_id is required for student role",
                        }
                    )
            else:
                if student_id_val is not None:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "student_id",
                            "message": f"student_id must be empty for {role_val} role",
                            "error": f"student_id must be empty for {role_val} role",
                        }
                    )

            # 5. Password complexity
            if not password_val or not PASSWORD_REGEX.match(password_val):
                row_errors.append(
                    {
                        "row": row_idx,
                        "field": "password",
                        "message": (
                            "Password must be at least 8 characters long "
                            "and contain at least one letter and one number"
                        ),
                        "error": "Password complexity requirement not met",
                    }
                )

            # 6. In-memory duplicates
            if email_val:
                if email_val in seen_emails:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "email",
                            "message": f"Duplicate email in CSV: '{email_val}'",
                            "error": f"Duplicate email in CSV: '{email_val}'",
                        }
                    )
                else:
                    seen_emails.add(email_val)

            if student_id_val:
                stu_lower = student_id_val.lower()
                if stu_lower in seen_student_ids:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "student_id",
                            "message": f"Duplicate student_id in CSV: '{student_id_val}'",
                            "error": f"Duplicate student_id in CSV: '{student_id_val}'",
                        }
                    )
                else:
                    seen_student_ids.add(stu_lower)

            # 7. DB duplicate checks (only if format was valid)
            if email_val and not any(e["field"] == "email" for e in row_errors):
                db_user = await self.user_repo.get_by_email(session, email_val)
                if db_user is not None:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "email",
                            "message": f"User with email '{email_val}' already exists",
                            "error": f"User with email '{email_val}' already exists",
                        }
                    )

            if student_id_val and not any(e["field"] == "student_id" for e in row_errors):
                db_stu = await self.user_repo.get_by_student_id(session, student_id_val)
                if db_stu is not None:
                    row_errors.append(
                        {
                            "row": row_idx,
                            "field": "student_id",
                            "message": f"User with student_id '{student_id_val}' already exists",
                            "error": f"User with student_id '{student_id_val}' already exists",
                        }
                    )

            if row_errors:
                errors.extend(row_errors)
            else:
                rows_data.append(
                    {
                        "email": email_val,
                        "full_name": full_name_val,
                        "role": role_val,
                        "student_id": student_id_val,
                        "password": password_val,
                    }
                )

        if errors:
            await session.rollback()
            raise ValidationAppError(
                "Validation failed on one or more rows",
                code="CSV_IMPORT_ERROR",
                details={"errors": errors},
            )

        users_to_create = [
            User(
                email=r["email"],
                full_name=r["full_name"],
                role=r["role"],
                student_id=r["student_id"],
                hashed_password=hash_password(r["password"]),
                is_active=True,
            )
            for r in rows_data
        ]
        created = await self.user_repo.create_many(session, users_to_create)
        await session.commit()
        for u in created:
            await session.refresh(u)
        return created


user_service = UserService()
