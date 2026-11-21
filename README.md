# Attendance Monitoring System

Backend API service for course attendance monitoring and tracking.

**Status:** in development

## Features
- FastAPI asynchronous application architecture
- Configurable environment-based settings
- Health check endpoint

## Quick Start

1. Install dependencies:
```bash
uv sync
```

2. Configure environment:
```bash
cp .env.example .env
```

3. Run the development server:
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
