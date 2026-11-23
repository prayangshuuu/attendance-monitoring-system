# Attendance Monitoring System

[![CI](https://github.com/prayangshuuu/attendance-monitoring-system/actions/workflows/ci.yml/badge.svg)](https://github.com/prayangshuuu/attendance-monitoring-system/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.165+-009688.svg?logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0%20(async)-red.svg)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg?logo=postgresql&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-2.x-336699.svg)
![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)
![Mypy](https://img.shields.io/badge/type%20checking-mypy%20strict-informational.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

Production-ready institutional attendance monitoring, automated tracking, and reporting system built with **FastAPI**, **SQLAlchemy 2.0 (async)**, and **PostgreSQL**.

The application delivers two unified experiences from a single high-performance codebase:
1. **Interactive Web Application**: Responsive, lightweight server-rendered portals for Administrators, Teachers, and Students powered by **Jinja2** and **HTMX 2.x** (zero Node.js/npm dependencies).
2. **RESTful JSON API**: Fully typed, OpenAPI/Swagger-documented endpoints at `/api/v1` for programmatic integration and mobile clients.

---

## Table of Contents

- [Features & Role Portals](#features--role-portals)
- [Architecture & Tech Stack](#architecture--tech-stack)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [Option 1: Local Setup with uv](#option-1-local-setup-with-uv)
  - [Option 2: Docker Compose](#option-2-docker-compose)
- [Demo Credentials](#demo-credentials)
- [System Endpoints](#system-endpoints)
  - [Web Portals](#web-portals)
  - [REST API v1](#rest-api-v1)
- [Environment Variables](#environment-variables)
- [Testing & Quality Assurance](#testing--quality-assurance)
- [Directory Structure](#directory-structure)
- [License](#license)

---

## Features & Role Portals

### 🛡️ Role-Based Portals

| Portal | Role | Key Capabilities |
|---|---|---|
| **Admin Portal** (`/admin`) | `admin` | Institutional KPI dashboard, user CRUD, bulk CSV user import with atomic rollback, course & teacher assignments, student enrollments, system-wide session audits, institutional reporting & CSV downloads. |
| **Teacher Portal** (`/teacher`) | `teacher` | Assigned course listings, scheduled and recurring session generation, live class session launcher with dynamic check-in code and countdown timer, live attendance roster polling, manual status overrides, course attendance analytics & CSV exports. |
| **Student Portal** (`/student`) | `student` | Personal attendance summary with overall attendance percentage, low-attendance at-risk warning badges, live 6-character check-in form with instant feedback, course-by-course status breakdown. |

### ⚡ Attendance Engine & Business Rules
- **Dynamic Check-in Codes**: Generates cryptographic 6-character alphanumeric codes with configurable TTL (default: 15 minutes) and one-click regeneration.
- **Automated Late Detection**: Automatically classifies check-ins as `late` if submitted after `starts_at + late_after_minutes` (default: 10 minutes), otherwise `present`.
- **Auto-Absent on Session Close**: Closing an active session automatically marks every enrolled student without an attendance record as `absent` in a single atomic transaction.
- **Concurrency & Duplicate Guarding**: Prevents double check-ins and handles concurrent marking safely with database-level constraints and atomic transaction rollbacks.
- **At-Risk Alerts**: Flags students whose attendance rate falls below the course's threshold (e.g. `< 80%`).

### 🔒 Security & Reliability
- **Authentication**: JWT access and refresh token rotation with Argon2 password hashing via `pwdlib`.
- **Web Security**: HttpOnly `SameSite=Lax` cookies, double-submit cookie CSRF protection on all mutating web routes, and strict Content Security Policy (`CSP`).
- **Production Middleware**: Structured JSON logging, `X-Request-ID` correlation tracing, sliding-window client IP rate limiting, and CORS handling.

---

## Architecture & Tech Stack

```mermaid
flowchart TD
    Client["Browser (HTMX + HTML) / API Client (JSON)"]
    
    subgraph Middleware["FastAPI Middleware Pipeline"]
        CORS["CORSMiddleware"]
        ReqID["RequestIDMiddleware (X-Request-ID)"]
        AccessLog["AccessLogMiddleware (Structured JSON)"]
        SecHeaders["SecurityHeadersMiddleware (CSP, HSTS, X-Frame)"]
    end

    subgraph Routers["FastAPI Routers"]
        WebAuth["Web Auth (/login, /logout)"]
        WebAdmin["Web Admin (/admin/*)"]
        WebTeacher["Web Teacher (/teacher/*)"]
        WebStudent["Web Student (/student/*)"]
        ApiV1["API v1 (/api/v1/*)"]
    end

    subgraph Services["Domain Service Layer"]
        AuthSvc["AuthService"]
        UserSvc["UserService"]
        CourseSvc["CourseService"]
        SessionSvc["SessionService"]
        AttSvc["AttendanceService"]
        ReportSvc["ReportService"]
    end

    subgraph Repositories["Repository Layer (SQLAlchemy 2.0)"]
        UserRepo["UserRepository"]
        CourseRepo["CourseRepository"]
        SessionRepo["SessionRepository"]
        AttRepo["AttendanceRepository"]
        ReportRepo["ReportRepository"]
    end

    DB[("PostgreSQL 16 / asyncpg")]

    Client --> CORS --> ReqID --> AccessLog --> SecHeaders
    SecHeaders --> Routers
    Routers --> Services
    Services --> Repositories
    Repositories --> DB
```

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, `asyncpg`, Alembic, PyJWT, Argon2 (`pwdlib`).
- **Frontend**: Server-rendered Jinja2 templates, HTMX 2.x, vanilla CSS design system with CSS custom properties (responsive, mobile cards, system dark/light theme).
- **Testing & Tooling**: `uv`, `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx`, `ruff`, `mypy` (strict), `bandit`, `pip-audit`.

---

## Quick Start

### Prerequisites
- **Python**: `>= 3.12`
- **uv**: Fast Python package installer (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker & Docker Compose** (for containerized PostgreSQL or full-stack deployment)

---

### Option 1: Local Setup with uv

1. **Clone the repository**:
   ```bash
   git clone https://github.com/prayangshuuu/attendance-monitoring-system.git
   cd attendance-monitoring-system
   ```

2. **Install dependencies**:
   ```bash
   uv sync
   ```

3. **Configure environment**:
   ```bash
   cp .env.example .env
   ```

4. **Start PostgreSQL**:
   ```bash
   # Using Docker for the local PostgreSQL database
   docker compose up -d db
   ```

5. **Apply database migrations**:
   ```bash
   uv run alembic upgrade head
   ```

6. **Seed demo data**:
   ```bash
   # Populates admin, teachers, students, courses, sessions, and records:
   uv run python scripts/seed.py
   
   # Or create only the initial admin account:
   # uv run python scripts/create_admin.py
   ```

7. **Start the development server**:
   ```bash
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

Now open [http://localhost:8000](http://localhost:8000) in your browser.

---

### Option 2: Docker Compose

Run the entire application stack (PostgreSQL database, migrations, and API/web service) with a single command:

```bash
# 1. Copy configuration
cp .env.example .env

# 2. Build and launch all containers
docker compose up --build

# 3. Seed demo data in the running container
docker compose exec api python scripts/seed.py
```

The web application and API will be live at [http://localhost:8000](http://localhost:8000).

---

## Demo Credentials

The database seed (`scripts/seed.py`) provisions the following ready-to-use accounts:

| Role | Email | Password | Description |
|---|---|---|---|
| **Admin** | `admin@example.com` | `ChangeMe123!` | System Administrator |
| **Teacher** | `teacher1@example.com` | `Teacher123!` | Prof. Alan Turing (CS101, CS201) |
| **Teacher** | `teacher2@example.com` | `Teacher123!` | Prof. Grace Hopper (CS102, CS202) |
| **Teacher** | `teacher3@example.com` | `Teacher123!` | Prof. Donald Knuth (CS301) |
| **Student** | `student1@example.com` | `Student123!` | Student 01 (ID: `STU1001`) |
| **Student** | `student2@example.com` – `student40@example.com` | `Student123!` | Enrolled Students (IDs: `STU1002`–`STU1040`) |

> 💡 **Live Check-in Demo**: Course **CS101** starts with an active open session and check-in code **`DEMO01`**. Log in as `student1@example.com` to test live code check-in immediately.

---

## System Endpoints

### Web Portals

| Route | Role | Description |
|---|---|---|
| `GET /` | Public | Role-based smart landing redirect (routes to `/admin`, `/teacher`, `/student`, or `/login`). |
| `GET /login`, `POST /login` | Public | Login page with secure cookie session creation. |
| `POST /logout` | Authenticated | Clears authentication cookies and redirects to login. |
| `/admin/*` | `admin` | Dashboard, users management, CSV import, courses, enrollments, sessions, institutional reports. |
| `/teacher/*` | `teacher` | Dashboard, my courses, scheduled sessions, live session monitor, attendance roster, reports. |
| `/student/*` | `student` | Dashboard, 6-character live check-in, personal attendance history, enrolled courses. |

### REST API v1

Interactive documentation:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

| Resource | Endpoints | Auth | Description |
|---|---|---|---|
| **Auth** | `POST /api/v1/auth/token`<br>`POST /api/v1/auth/refresh`<br>`GET /api/v1/auth/me` | Public / Bearer | JWT login, token refresh, and identity inspection. |
| **Users** | `GET /api/v1/users`<br>`POST /api/v1/users`<br>`GET /api/v1/users/{id}`<br>`PATCH /api/v1/users/{id}`<br>`POST /api/v1/users/bulk-import` | Admin | Paginated user management, search filter, and CSV bulk import. |
| **Courses** | `GET /api/v1/courses`<br>`POST /api/v1/courses`<br>`POST /api/v1/courses/{id}/enroll` | Authenticated | Course management and student enrollments. |
| **Sessions** | `POST /api/v1/sessions`<br>`POST /api/v1/sessions/{id}/open`<br>`POST /api/v1/sessions/{id}/close` | Teacher / Admin | Session lifecycle management and code generation. |
| **Attendance** | `POST /api/v1/attendance/check-in`<br>`POST /api/v1/attendance/mark`<br>`POST /api/v1/attendance/bulk-mark` | Student / Teacher | Live code check-in and manual attendance marking. |
| **Reports** | `GET /api/v1/reports/courses/{id}`<br>`GET /api/v1/reports/courses/{id}/export-csv`<br>`GET /api/v1/reports/dashboard` | Teacher / Admin | Attendance percentage summaries, at-risk flags, CSV exports. |

---

## Environment Variables

All configuration is managed through Pydantic Settings with automatic `.env` loading:

| Variable | Description | Default |
|---|---|---|
| `APP_NAME` | Institutional application title | `Attendance Monitoring System` |
| `ENVIRONMENT` | Runtime mode (`development`, `testing`, `production`) | `development` |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://postgres:postgres@localhost:5432/attendance` |
| `JWT_SECRET_KEY` | Secret key for signing JWT tokens (min 32 characters in production) | `change-me-32-byte-long-secret-key-attendance-system` |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifespan in minutes | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token lifespan in days | `7` |
| `CHECKIN_CODE_TTL_MINUTES` | Dynamic session check-in code validity in minutes | `15` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated list or JSON array) | `http://localhost:3000` |
| `FIRST_ADMIN_EMAIL` | Initial administrator bootstrap email | `admin@example.com` |
| `FIRST_ADMIN_PASSWORD` | Initial administrator bootstrap password | `ChangeMe123!` |
| `COOKIE_SECURE` | Set `true` in production to require HTTPS for auth cookies | `false` |
| `CSRF_SECRET_KEY` | Secret key for generating and validating double-submit CSRF tokens | `change-me-csrf-secret-key-attendance-system` |

> ⚠️ **Production Security Notice**: When `ENVIRONMENT=production`, strict startup validation triggers. `JWT_SECRET_KEY` must be at least 32 characters with no default keywords, and `FIRST_ADMIN_PASSWORD` cannot use default credentials.

---

## Testing & Quality Assurance

The codebase maintains strict quality gates across linting, formatting, type checking, security auditing, and test suites:

### 1. Run Automated Test Suite
```bash
# Run all tests with coverage report (575+ tests)
uv run pytest

# Run unit tests only
uv run pytest -m unit

# Run integration tests only
uv run pytest -m integration

# Run against a dedicated PostgreSQL instance
TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/attendance_test" uv run pytest
```

### 2. Code Quality & Formatting
```bash
# Check lint rules
uv run ruff check .

# Check code formatting
uv run ruff format --check .

# Auto-fix linting and format files
uv run ruff check . --fix
uv run ruff format .
```

### 3. Static Type Checking
```bash
# Strict Mypy type validation across the application
uv run mypy app
```

### 4. Security Audits & Smoke Testing
```bash
# Static application security testing (SAST)
uv run bandit -r app

# Dependency vulnerability scanning
uv run pip-audit

# End-to-end smoke test against a live instance
uv run python scripts/smoke_test.py

# High-concurrency check-in load test (50 concurrent users)
uv run python scripts/load_test_checkin.py
```

---

## Directory Structure

```text
attendance-monitoring-system/
├── alembic/                 # Database migrations (async environment)
├── app/
│   ├── api/                 # REST API dependencies and v1 route handlers
│   ├── core/                # Configuration, security (JWT/Argon2), middleware, logging
│   ├── db/                  # Async database session and declarative base
│   ├── models/              # SQLAlchemy 2.0 ORM models (User, Course, Session, Attendance)
│   ├── repositories/        # Database data access layer
│   ├── schemas/             # Pydantic v2 schemas for request validation & serialization
│   ├── services/            # Pure business logic and domain workflows
│   ├── static/              # Static assets (vendored htmx.min.js, app.css, app.js)
│   ├── templates/           # Jinja2 templates (portals: admin, teacher, student, components)
│   ├── utils/               # Clock abstraction and in-memory rate limiter
│   ├── web/                 # Server-rendered web route handlers (Admin, Teacher, Student)
│   └── main.py              # Application factory, lifespan bootstrap, and exception handlers
├── scripts/
│   ├── create_admin.py      # Idempotent administrator account creation
│   ├── seed.py              # Full institutional demo data seeder
│   ├── smoke_test.py        # End-to-end multi-role verification script
│   └── load_test_checkin.py # Concurrent student check-in stress tester
├── tests/
│   ├── factories.py         # Test data model factories
│   ├── conftest.py          # Pytest fixtures and test database configuration
│   ├── unit/                # Unit test suites
│   └── integration/         # Integration test suites for API, Web UI, and security
├── docker-compose.yml       # Multi-container orchestration (DB, Migrations, API)
├── Dockerfile               # Production multi-stage Docker build
├── pyproject.toml           # Project metadata, dependencies, and tool settings
└── README.md                # Project documentation
```

---

## License

This project is licensed under the [MIT License](LICENSE).
