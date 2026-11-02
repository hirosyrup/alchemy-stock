# Cloud Run のジョブ用のイメージ。scripts/ のどれを動かすかは、ジョブの引数の先頭で指定する
#   (例: scripts/fetch_documents.py ...、scripts/collect_tdnet.py ...)
#
# ビルドはリポジトリ直下から (cloud/build_image.sh)。.env と data/ は .dockerignore で入れない

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python"]
