from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.exceptions import PermissionDeniedError
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import Page, PaginationParams
from app.schemas.user import PasswordChange, UserCreate, UserRead, UserUpdate
from app.services.user_service import user_service

router = APIRouter()


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (admin only)",
)
async def create_user(
    data: UserCreate,
    _admin: AdminUser,
    db: DbSession,
) -> User:
    return await user_service.create_user(session=db, data=data)


@router.get(
    "",
    response_model=Page[UserRead],
    status_code=status.HTTP_200_OK,
    summary="List users with pagination and filters (admin only)",
)
async def list_users(
    _admin: AdminUser,
    db: DbSession,
    pagination: Annotated[PaginationParams, Depends()],
    role: Annotated[UserRole | None, Query(description="Filter by role")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
    search: Annotated[
        str | None, Query(description="Search full_name, email, or student_id")
    ] = None,
) -> Page[UserRead]:
    return await user_service.list_users(
        session=db,
        page=pagination.page,
        size=pagination.size,
        role=role,
        is_active=is_active,
        search=search,
    )


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change password for current authenticated user",
)
async def change_my_password(
    data: PasswordChange,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    await user_service.change_password(session=db, user_id=current_user.id, data=data)


@router.post(
    "/import",
    response_model=list[UserRead],
    status_code=status.HTTP_201_CREATED,
    summary="Bulk import users via CSV file (admin only)",
)
async def import_users_csv(
    _admin: AdminUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="CSV file containing users to import")],
) -> list[User]:
    content = await file.read()
    return await user_service.import_users_csv(session=db, file_content=content)


@router.get(
    "/{id}",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Get user details by ID (admin or self)",
)
async def get_user_by_id(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> User:
    if current_user.role != UserRole.ADMIN and current_user.id != id:
        raise PermissionDeniedError(
            "You do not have permission to access this user",
            code="PERMISSION_DENIED",
        )
    return await user_service.get_user_by_id(session=db, user_id=id)


@router.patch(
    "/{id}",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Update a user (admin only)",
)
async def update_user(
    id: UUID,
    data: UserUpdate,
    _admin: AdminUser,
    db: DbSession,
) -> User:
    return await user_service.update_user(session=db, user_id=id, data=data)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a user (admin only)",
)
async def delete_user(
    id: UUID,
    admin: AdminUser,
    db: DbSession,
) -> None:
    await user_service.deactivate_user(session=db, user_id=id, admin_id=admin.id)
