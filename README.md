# Attendance Monitoring System

Backend API service for course attendance monitoring and tracking.

**Status:** in development

## Features
- FastAPI asynchronous application architecture
- JWT authentication with access token & refresh token rotation
- Argon2 password hashing via pwdlib
- Role-based authorization (`admin`, `teacher`, `student`)
- User management (admin CRUD, pagination, ILIKE search, self password change, atomic bulk CSV import)
- Courses & enrollments (course CRUD, teacher assignment, student enrollment, ownership access control)
- Typed configuration via Pydantic v2 settings
- Async SQLAlchemy 2.0 database session and declarative base
- Alembic async database migrations
- Health check endpoint with database probe

## Auth
- Login endpoint: `POST /api/v1/auth/login` returning access and refresh tokens.
- Authentication header: `Authorization: Bearer <access_token>` on protected routes.

## Create Admin
```bash
uv run python scripts/create_admin.py
```

## Data Model
- `User`: System accounts with roles (`admin`, `teacher`, `student`), credentials, and optional student identifier.
- `Course`: Academic courses managed by teachers with attendance percentage thresholds.
- `Enrollment`: Student enrollments associating students with courses.
- `ClassSession`: Individual class meetings with schedule intervals, check-in codes, and session status lifecycle.
- `AttendanceRecord`: Check-in log entries tracking student attendance statuses, methods, and audit notes.

## Quick Start

1. Install dependencies:
```bash
uv sync
```

2. Configure environment:
```bash
cp .env.example .env
```

3. Run database migrations:
```bash
uv run alembic upgrade head
```

4. Run the development server:
```bash
uv run uvicorn app.main:app --reload
```

## Running Tests

```bash
uv run pytest
```

## API Documentation

- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
