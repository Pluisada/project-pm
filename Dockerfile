# Build stage for frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Runtime stage
FROM python:3.11-slim
WORKDIR /app

# Copy backend files
COPY backend/pyproject.toml ./
COPY backend/*.py ./

# Copy built frontend to static directory
RUN mkdir -p static
COPY --from=frontend-builder /app/frontend/out static/

# Install Python dependencies using pip
RUN pip install --no-cache-dir -e .

EXPOSE 8000
ENV PORT=8000
ENV DATABASE_URL=sqlite:///./pm.db

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
