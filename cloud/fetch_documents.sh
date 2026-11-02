#!/bin/bash
# =============================================================================
# 開示の PDF・XBRL を全件取る Cloud Run ジョブを作り直す (PLAN.md §5)
#
# 対象の一覧 (scripts/make_document_targets.py で作る) を GCS に置き、イメージを作ってジョブを更新する。
# 動かすのは別のコマンド (最後に表示する)。止まっても、もう一度動かせば GCS に無いものだけ取る。
#
#   ./cloud/fetch_documents.sh                                   # 本番の一覧
#   TARGETS=targets/documents_trial.jsonl ./cloud/fetch_documents.sh   # 少数で試す
# =============================================================================
set -euo pipefail

PROJECT=alchemy-stock-513407
REGION=us-central1
BUCKET=gs://${PROJECT}-raw
TARGETS=${TARGETS:-targets/documents_v1.jsonl}
JOB=fetch-documents

cd "$(git rev-parse --show-toplevel)"

gcloud storage cp "data/${TARGETS}" "${BUCKET}/${TARGETS}" --project "${PROJECT}"
IMAGE=$(./cloud/build_image.sh | tail -1)

# 3本で取り、それぞれ1秒以上あける (全体で1秒に3回まで)。どれかが拒否されたら全部止まる (PLAN.md §5 のお作法)
# Cloud Run のジョブは CPU 1 未満にできない。約21万ファイルが約20時間、計算の費用は約¥210 (2026-11-02 見積もり)
gcloud run jobs deploy "${JOB}" --project "${PROJECT}" --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "document-fetcher@${PROJECT}.iam.gserviceaccount.com" \
  --tasks 1 --parallelism 1 --max-retries 0 --task-timeout 168h \
  --cpu 1 --memory 512Mi \
  --args="scripts/fetch_documents.py,${BUCKET}/${TARGETS},--dest,${BUCKET},--xbrl,--interval,1,--workers,3,--progress-every,1000"

echo "動かす: gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION}"
