# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.0.0

- **Authentication & RBAC**: JWT access and refresh token authentication with Argon2 password hashing via `pwdlib` and strict role-based access control (`admin`, `teacher`, `student`).
- **User Management**: Admin CRUD endpoints, paginated user listings with ILIKE search, self password change, and atomic bulk user CSV import.
- **Courses & Enrollments**: Academic course lifecycle management, teacher course assignments, student enrollments, and teacher ownership access control.
- **Class Sessions & Lifecycle**: Class session scheduling, recurring session generator, lifecycle transitions (`scheduled`, `active`, `closed`), and dynamic 6-character check-in code generation with TTL expiration.
- **Attendance Marking & Auto-absent**: Code-based student check-in, teacher/admin manual attendance marking, duplicate check-in prevention, and automatic marking of absent students upon session closure.
- **Reports & Analytics**: Comprehensive course and student attendance percentage summaries, low-attendance at-risk student detection, streaming CSV export, and administrative dashboard metrics with optimized database indexes.
- **Production Middleware**: Structured JSON logging, X-Request-ID correlation tracking, in-memory IP rate limiting, CORS configuration, and production security credential validators.
- **Docker & CI**: Production multi-stage Dockerfile, docker-compose orchestration, automated Alembic migrations, database seeding scripts, and GitHub Actions CI workflow (linting, type checking, security auditing, and test coverage).
