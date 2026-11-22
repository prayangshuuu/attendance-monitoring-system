# Attendance Monitoring System

[![CI](https://github.com/prayangshuuu/attendance-monitoring-system/actions/workflows/ci.yml/badge.svg)](https://github.com/prayangshuuu/attendance-monitoring-system/actions/workflows/ci.yml)

Production-ready backend API service for academic course attendance monitoring, automated tracking, and reporting built with FastAPI, SQLAlchemy 2.0, and PostgreSQL.

## Features

- **Authentication & RBAC**: JWT access/refresh token rotation, Argon2 password hashing via `pwdlib`, and role-based access control (`admin`, `teacher`, `student`).
- **User Management**: Admin CRUD, paginated listings with search, self password management, and atomic bulk CSV user import.
- **Courses & Enrollments**: Course management, teacher assignment, student enrollment, and access control.
- **Class Sessions & Lifecycle**: Scheduled & recurring sessions, lifecycle management (`scheduled`, `active`, `closed`), and dynamic check-in codes with TTL expiration.
- **Attendance Tracking**: Code-based student check-in, manual marking, duplicate prevention, and automatic absent marking on session close.
- **Reports & Analytics**: Course/student attendance summaries, at-risk low attendance alerts, administrative metrics dashboard, and CSV export.
- **Production Middleware**: Structured JSON logging, Request-ID tracing, client IP rate limiting, and CORS configuration.

## Quick Start

### Local Setup with uv

1. **Install dependencies**:
   ```bash
   uv sync
   ```

2. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```

3. **Run database migrations**:
   ```bash
   uv run alembic upgrade head
   ```

4. **Seed database with initial admin**:
   ```bash
   uv run python scripts/create_admin.py
   # Or full demo seed:
   # uv run python scripts/seed.py
   ```

5. **Start the development server**:
   ```bash
   uv run uvicorn app.main:app --reload
   ```

### Docker Compose

Run the entire application stack (PostgreSQL database and API service) with Docker Compose:

```bash
# Copy environment configuration
cp .env.example .env

# Start containers
docker compose up --build

# Run database seed in running container
docker compose exec api python scripts/seed.py
```

## Tests

Run the automated test suite with coverage:

```bash
# Run all tests with coverage report
uv run pytest

# Run unit tests only
uv run pytest -m unit

# Run integration tests only
uv run pytest -m integration

# Run against PostgreSQL instance
TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/attendance_test" uv run pytest
```

Linting and type checking:
```bash
uv run ruff check .
uv run mypy app
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `APP_NAME` | Name of the application | `Attendance Monitoring System` |
| `ENVIRONMENT` | Environment mode (`development`, `testing`, `production`) | `development` |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://postgres:postgres@localhost:5432/attendance` |
| `JWT_SECRET_KEY` | Secret key for signing JWT tokens (min 32 chars in production) | `change-me-32-byte-long-secret-key-attendance-system` |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifespan in minutes | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token lifespan in days | `7` |
| `CHECKIN_CODE_TTL_MINUTES` | Dynamic session check-in code validity in minutes | `15` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated or JSON list) | `http://localhost:3000` |
| `FIRST_ADMIN_EMAIL` | Initial administrator email for bootstrapping | `admin@example.com` |
| `FIRST_ADMIN_PASSWORD` | Initial administrator password for bootstrapping | `ChangeMe123!` |

> In `production` mode (`ENVIRONMENT=production`), the application enforces strict credentials: `JWT_SECRET_KEY` must be at least 32 characters and cannot contain placeholder values, and `FIRST_ADMIN_PASSWORD` cannot be a default password.

## API Documentation

When running locally in development mode:
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

## License

This project is licensed under the MIT License.
