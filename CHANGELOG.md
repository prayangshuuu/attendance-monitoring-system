# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.1.0

- **Server-Rendered Web Portals**: Interactive, responsive Jinja2 and HTMX 2.x web application providing dedicated portals for Administrators (`/admin`), Teachers (`/teacher`), and Students (`/student`) with zero Node.js/npm dependencies.
- **Admin Portal**: Institutional analytics dashboard, user CRUD management with atomic bulk CSV user import, course-teacher assignment, student enrollment management, and system-wide session oversight.
- **Teacher Portal**: Course session scheduling, recurring session generator, live session monitor with dynamic check-in code and countdown timer, live attendance roster polling, and manual attendance marking overrides.
- **Student Portal**: Real-time personal attendance summary with overall attendance percentage, low-attendance at-risk warning badges, and 6-character live check-in with instant feedback.
- **Session Authentication & Web Security**: Secure HttpOnly cookies (`SameSite=Lax`), double-submit cookie CSRF protection on mutating routes, and Content Security Policy (`CSP`) updates.
- **Packaging & Release Pipeline**: Hatchling packaging build configuration for wheel and sdist distributions, automated GitHub Container Registry (GHCR) package publishing workflow, and release automation.

## 1.0.0

- **Authentication & RBAC**: JWT access and refresh token authentication with Argon2 password hashing via `pwdlib` and strict role-based access control (`admin`, `teacher`, `student`).
- **User Management**: Admin CRUD endpoints, paginated user listings with ILIKE search, self password change, and atomic bulk user CSV import.
- **Courses & Enrollments**: Academic course lifecycle management, teacher course assignments, student enrollments, and teacher ownership access control.
- **Class Sessions & Lifecycle**: Class session scheduling, recurring session generator, lifecycle transitions (`scheduled`, `active`, `closed`), and dynamic 6-character check-in code generation with TTL expiration.
- **Attendance Marking & Auto-absent**: Code-based student check-in, teacher/admin manual attendance marking, duplicate check-in prevention, and automatic marking of absent students upon session closure.
- **Reports & Analytics**: Comprehensive course and student attendance percentage summaries, low-attendance at-risk student detection, streaming CSV export, and administrative dashboard metrics with optimized database indexes.
- **Production Middleware**: Structured JSON logging, X-Request-ID correlation tracking, in-memory IP rate limiting, CORS configuration, and production security credential validators.
- **Docker & CI**: Production multi-stage Dockerfile, docker-compose orchestration, automated Alembic migrations, database seeding scripts, and GitHub Actions CI workflow (linting, type checking, security auditing, and test coverage).
