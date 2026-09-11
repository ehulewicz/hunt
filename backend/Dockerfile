FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.23

# The workspace root owns the lock, so both members must be present for uv to
# resolve it. Only `personal-os-backend` is installed.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/README.md ./backend/
COPY workers/pyproject.toml workers/README.md ./workers/
COPY backend/src ./backend/src
COPY artifacts/resume/main.json ./artifacts/resume/main.json
RUN uv sync --frozen --no-dev --package personal-os-backend

ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "uvicorn personal_os.main:app --host 0.0.0.0 --port ${PORT}"]
