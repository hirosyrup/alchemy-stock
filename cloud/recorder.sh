#!/bin/bash
# =============================================================================
# 記録係 (docs/RECORDER_DESIGN.md) の Cloud Run ジョブを作り直す。東京 (asia-northeast1)。
#
# 先に cloud/terraform を apply しておく (サービスアカウント・Firestore・東京のイメージ置き場・Scheduler・権限)。
# 初めての時だけ、J-Quants の API キーを Secret Manager に入れ、財務情報の蓄えを GCS に置く:
#   grep '^JQUANTS_API_KEY=' .env | cut -d= -f2- | tr -d '"'"'"'\n' | gcloud secrets versions add jquants-api-key --data-file=- --project alchemy-stock-513407
#   RECORDER_GCS=gs://alchemy-stock-513407-raw/recorder uv run python scripts/recorder_job.py upload-fins
#
#   ./cloud/recorder.sh
# =============================================================================
set -euo pipefail

PROJECT=alchemy-stock-513407
REGION=asia-northeast1
SA=recorder@${PROJECT}.iam.gserviceaccount.com
ENV=RECORDER_GCS=gs://${PROJECT}-raw/recorder
SECRETS=JQUANTS_API_KEY=jquants-api-key:latest

cd "$(git rev-parse --show-toplevel)"
IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/alchemy-stock/jobs:$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -f cloud/jobs/Dockerfile -t "${IMAGE}" . >&2
docker push "${IMAGE}" >&2

deploy() {  # 名前 タスク 時間の上限 メモリ やり直しの回数
  gcloud run jobs deploy "$1" --project "${PROJECT}" --region "${REGION}" \
    --image "${IMAGE}" --service-account "${SA}" \
    --tasks 1 --parallelism 1 --max-retries "$5" --task-timeout "$3" \
    --cpu 1 --memory "$4" \
    --set-env-vars "${ENV}" --set-secrets "${SECRETS}" \
    --args="scripts/recorder_job.py,$2"
}

deploy recorder-prepare   prepare   30m 2Gi 1
deploy recorder-morning   morning   3h  1Gi 0
deploy recorder-window    window    10m 1Gi 0
deploy recorder-close     close     15m 1Gi 0
deploy recorder-reconcile reconcile 30m 2Gi 1

echo "手で動かす: gcloud run jobs execute recorder-prepare --project ${PROJECT} --region ${REGION}"
