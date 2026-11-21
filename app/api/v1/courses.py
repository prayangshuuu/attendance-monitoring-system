from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models.course import Course
from app.schemas.common import Page, PaginationParams
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate
from app.services.course_service import course_service

router = APIRouter()


@router.post(
    "",
    response_model=CourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new course (admin only)",
)
async def create_course(
    data: CourseCreate,
    _admin: AdminUser,
    db: DbSession,
) -> Course:
    return await course_service.create_course(session=db, data=data)


@router.get(
    "",
    response_model=Page[CourseRead],
    status_code=status.HTTP_200_OK,
    summary="List courses with pagination and filters",
)
async def list_courses(
    current_user: CurrentUser,
    db: DbSession,
    pagination: Annotated[PaginationParams, Depends()],
    search: Annotated[str | None, Query(description="Search by code or title")] = None,
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
) -> Page[CourseRead]:
    return await course_service.list_courses(
        session=db,
        current_user=current_user,
        page=pagination.page,
        size=pagination.size,
        search=search,
        is_active=is_active,
    )


@router.get(
    "/{id}",
    response_model=CourseRead,
    status_code=status.HTTP_200_OK,
    summary="Get course details by ID",
)
async def get_course_by_id(
    id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> Course:
    return await course_service.get_course(session=db, course_id=id, current_user=current_user)


@router.patch(
    "/{id}",
    response_model=CourseRead,
    status_code=status.HTTP_200_OK,
    summary="Update a course (admin only)",
)
async def update_course(
    id: UUID,
    data: CourseUpdate,
    _admin: AdminUser,
    db: DbSession,
) -> Course:
    return await course_service.update_course(session=db, course_id=id, data=data)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a course (admin only)",
)
async def delete_course(
    id: UUID,
    _admin: AdminUser,
    db: DbSession,
) -> None:
    await course_service.deactivate_course(session=db, course_id=id)
