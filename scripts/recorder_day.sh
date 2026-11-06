#!/bin/bash
# =============================================================================
# 記録係を手元で1日動かす (準備 → 記録 → 答え合わせ)。注文は出さない。取引日の朝 8:30〜8:50 に起動する。
# Mac がスリープしないよう caffeinate で包む。ログは data/recorder/<日付>/run.log
#
#   SAXO_GCLOUD_ACCOUNT=<個人の Google アカウント> ./scripts/recorder_day.sh
# =============================================================================
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
DAY=$(TZ=Asia/Tokyo date +%F)
mkdir -p "data/recorder/${DAY}"
LOG="data/recorder/${DAY}/run.log"

caffeinate -i bash -c "
  uv run python scripts/recorder_prepare.py --day ${DAY} &&
  uv run python scripts/recorder_run.py --day ${DAY}
  # 日足が出るのを待って答え合わせ (17:00 から 30分おきに 4回まで試す)
  for i in 1 2 3 4; do
    now=\$(TZ=Asia/Tokyo date +%H%M); [ \$now -lt 1700 ] && sleep \$(( (17*60 - (10#\${now:0:2}*60 + 10#\${now:2:2})) * 60 ))
    uv run python scripts/recorder_reconcile.py --day ${DAY} && break
    sleep 1800
  done
" 2>&1 | tee -a "${LOG}"
