#!/bin/bash
# =============================================================================
# TDnet の開示一覧 (時刻つき、直近31日分しか残らない) を毎日 GCS に貯める Cloud Run ジョブを作り直す
#
# 毎日の起動は Cloud Scheduler (cloud/terraform の collect_tdnet_daily)。ジョブが先に要るので、
# 初めての時はこれを動かしてから terraform apply する。
#
#   ./cloud/collect_tdnet.sh
# =============================================================================
set -euo pipefail

PROJECT=alchemy-stock-513407
REGION=us-central1
BUCKET=gs://${PROJECT}-raw
JOB=collect-tdnet

cd "$(git rev-parse --show-toplevel)"
IMAGE=$(./cloud/build_image.sh | tail -1)

# 1回の読み込みごとに3秒あける。1日数十ページなので数分で終わる
gcloud run jobs deploy "${JOB}" --project "${PROJECT}" --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "daily-collector@${PROJECT}.iam.gserviceaccount.com" \
  --tasks 1 --parallelism 1 --max-retries 0 --task-timeout 1h \
  --cpu 1 --memory 512Mi \
  --args="scripts/collect_tdnet.py,--dest,${BUCKET}"

echo "手で動かす: gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION}"
