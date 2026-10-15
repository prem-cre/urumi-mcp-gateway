FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.4 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations migrations
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY services/__init__.py services/__init__.py
COPY services/gateway services/gateway
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "services.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
