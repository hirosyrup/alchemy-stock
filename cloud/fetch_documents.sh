#!/bin/bash
# =============================================================================
# 開示の PDF・XBRL を全件取る Cloud Run ジョブを作り直す (PLAN.md §5)
#
# 対象の一覧 (scripts/make_document_targets.py で作る) を GCS に置き、イメージを作ってジョブを更新する。
# 動かすのは別のコマンド (最後に表示する)。止まっても、もう一度動かせば GCS に無いものだけ取る。
#
#   ./cloud/fetch_documents.sh                                   # 本番の一覧
#   TARGETS=targets/documents_trial.jsonl ./cloud/fetch_documents.sh   # 少数で試す
#
# 最初に1回: gcloud auth configure-docker us-central1-docker.pkg.dev
# =============================================================================
set -euo pipefail

PROJECT=alchemy-stock-513407
REGION=us-central1
BUCKET=gs://${PROJECT}-raw
TARGETS=${TARGETS:-targets/documents_v1.jsonl}
JOB=fetch-documents
IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/alchemy-stock/fetcher:$(git rev-parse --short HEAD)

cd "$(git rev-parse --show-toplevel)"

gcloud storage cp "data/${TARGETS}" "${BUCKET}/${TARGETS}" --project "${PROJECT}"
docker build --platform linux/amd64 -f cloud/fetcher/Dockerfile -t "${IMAGE}" .
docker push "${IMAGE}"

# 1つずつ順番に取る (並列なし)。読み込みの間隔は1秒以上 (PLAN.md §5 のお作法)
gcloud run jobs deploy "${JOB}" --project "${PROJECT}" --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "document-fetcher@${PROJECT}.iam.gserviceaccount.com" \
  --tasks 1 --parallelism 1 --max-retries 0 --task-timeout 168h \
  --cpu 1 --memory 1Gi \
  --args="${BUCKET}/${TARGETS},--dest,${BUCKET},--xbrl,--interval,1,--progress-every,500"

echo "動かす: gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION}"
