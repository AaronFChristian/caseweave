# Multi-stage, non-root, pinned deps — the production-readiness checklist
# items called out in the architecture doc, applied to this repo.
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
RUN groupadd -r caseweave && useradd -r -g caseweave caseweave
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY data/regulatory/ data/regulatory/
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/src"
USER caseweave
EXPOSE 8123
CMD ["uvicorn", "caseweave.api.main:app", "--host", "0.0.0.0", "--port", "8123"]
