# Attendance Monitoring System

Backend API service for course attendance monitoring and tracking.

**Status:** in development

## Features
- FastAPI asynchronous application architecture
- Typed configuration via Pydantic v2 settings
- Async SQLAlchemy 2.0 database session and declarative base
- Alembic async database migrations
- Health check endpoint with database probe

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
