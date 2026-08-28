FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/workspace/apps/api/.venv/bin:$PATH"

WORKDIR /workspace
RUN pip install --no-cache-dir uv
COPY apps/api/pyproject.toml apps/api/uv.lock ./apps/api/
COPY packages/python/alcuin-core ./packages/python/alcuin-core
COPY packages/python/alcuin-knowledge ./packages/python/alcuin-knowledge
COPY packages/python/alcuin-storage ./packages/python/alcuin-storage
COPY packages/python/alcuin-web-search ./packages/python/alcuin-web-search
COPY extensions/operations-copilot ./extensions/operations-copilot
COPY apps/api/src ./apps/api/src
WORKDIR /workspace/apps/api
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uvicorn", "alcuin_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
