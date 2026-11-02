#!/bin/bash
# =============================================================================
# Cloud Run のジョブ用のイメージを作って置き場に送り、イメージの名前を最後の行に出す
#
#   IMAGE=$(./cloud/build_image.sh | tail -1)
#
# 最初に1回: gcloud auth configure-docker us-central1-docker.pkg.dev
# =============================================================================
set -euo pipefail

PROJECT=alchemy-stock-513407
REGION=us-central1
IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/alchemy-stock/jobs:$(git rev-parse --short HEAD)

cd "$(git rev-parse --show-toplevel)"
docker build --platform linux/amd64 -f cloud/jobs/Dockerfile -t "${IMAGE}" . >&2
docker push "${IMAGE}" >&2
echo "${IMAGE}"
