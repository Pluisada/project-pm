# Build stage for frontend
# Node 24 is Active LTS (Node 20 is end of life); Next 16.4.2 requires >=20.9.0.
FROM node:24-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Python dependencies, installed exactly as locked in backend/uv.lock.
# --locked fails the build if pyproject.toml and uv.lock disagree; dev extras
# are not installed. The uv version is pinned (the lock needs a recent uv).
FROM python:3.11-slim AS python-deps
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Runtime stage
FROM python:3.11-slim
WORKDIR /app

# Locked virtual environment from the previous stage
COPY --from=python-deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy backend files (the application runs from source; it is not installed)
COPY backend/*.py ./

# Copy built frontend to static directory
RUN mkdir -p static
COPY --from=frontend-builder /app/frontend/out static/

# Run as an unprivileged user. /app stays writable for it because SQLite
# creates its journal next to the database file (/app/pm.db by default). A
# mounted database must be writable by uid 10001 (docs/DEPLOYMENT.md).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chown app:app /app
USER app

EXPOSE 8000
ENV PORT=8000
ENV DATABASE_URL=sqlite:///./pm.db
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
