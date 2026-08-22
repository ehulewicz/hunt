FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.23

COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
COPY backend/src ./src
COPY artifacts/resume/main.json ./artifacts/resume/main.json
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "uvicorn personal_os.main:app --host 0.0.0.0 --port ${PORT}"]
