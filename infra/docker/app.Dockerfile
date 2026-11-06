# API / worker / migration image. Build context: repository root.
FROM python:3.12-slim-bookworm AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY engine/pyproject.toml engine/pyproject.toml
COPY backend/pyproject.toml backend/pyproject.toml
COPY engine/src engine/src
COPY backend/src backend/src
RUN uv sync --frozen --no-dev --package erp-backend --no-editable

FROM python:3.12-slim-bookworm
RUN groupadd --gid 10001 erp && useradd --uid 10001 --gid erp --no-create-home \
      --shell /usr/sbin/nologin erp
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY backend/alembic.ini backend/alembic.ini
COPY backend/alembic backend/alembic
COPY mappings mappings
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER erp
EXPOSE 8000
CMD ["uvicorn", "--factory", "erp_backend.main:app_factory", "--host", "0.0.0.0", \
     "--port", "8000", "--proxy-headers", "--no-server-header"]
