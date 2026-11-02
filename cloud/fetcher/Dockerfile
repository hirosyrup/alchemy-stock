# 開示の PDF・XBRL を取って GCS に置く Cloud Run ジョブ (scripts/fetch_documents.py)
#
# ビルドはリポジトリ直下から (cloud/fetch_documents.sh):
#   docker build --platform linux/amd64 -f cloud/fetcher/Dockerfile -t fetcher .
# .env と data/ は .dockerignore で入れない

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "scripts/fetch_documents.py"]
