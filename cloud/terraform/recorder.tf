# =============================================================================
# 記録係 (docs/RECORDER_DESIGN.md) - 注文は出さず、54・49・S16 の判定と Saxo の値段と秒数を記録する
# =============================================================================
# TDnet に近い東京で動かす (49 は秒の勝負)。ジョブそのものは cloud/recorder.sh で作る (イメージが要るため)。
# 記録は Firestore、材料と財務情報の蓄えは raw バケットの recorder/ の下。

variable "recorder_region" {
  description = "記録係の Cloud Run ジョブ・Scheduler・イメージ・Firestore の場所"
  type        = string
  default     = "asia-northeast1"
}

resource "google_project_service" "recorder_apis" {
  for_each = toset([
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# 無料枠があるのは (default) のデータベースだけ
resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.recorder_region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.recorder_apis]
}

resource "google_artifact_registry_repository" "recorder_images" {
  location      = var.recorder_region
  repository_id = "alchemy-stock"
  format        = "DOCKER"
  description   = "alchemy-stock の Cloud Run 用イメージ (東京)"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30日
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "recorder" {
  account_id   = "recorder"
  display_name = "記録係の Cloud Run ジョブ (注文は出さない)"
}

resource "google_storage_bucket_iam_member" "recorder_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.recorder.email}"
}

resource "google_project_iam_member" "recorder_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.recorder.email}"
}

# J-Quants の API キー。値は Terraform の外で入れる (状態ファイルに残さない):
#   grep '^JQUANTS_API_KEY=' .env | cut -d= -f2- | tr -d '"'"'"'\n' | gcloud secrets versions add jquants-api-key --data-file=- --project alchemy-stock-513407
resource "google_secret_manager_secret" "jquants_api_key" {
  secret_id = "jquants-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.recorder_apis]
}

resource "google_secret_manager_secret_iam_member" "recorder_jquants" {
  secret_id = google_secret_manager_secret.jquants_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.recorder.email}"
}

# Saxo のアクセストークンは alchemy-fx の Secret Manager のものを読むだけ (更新は alchemy-fx の worker が 15分ごとにする)。
# リフレッシュトークン (saxo-refresh-token) には権限を付けない。付け足すだけで、alchemy-fx の設定は変えない。
resource "google_secret_manager_secret_iam_member" "recorder_saxo_access_token" {
  project   = "alchemy-fx"
  secret_id = "saxo-access-token"
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.recorder.email}"
}

# -----------------------------------------------------------------------------
# 起動の時刻 (平日。祝日は材料が無いのでジョブがすぐ終わる)
# -----------------------------------------------------------------------------
locals {
  recorder_schedules = {
    "recorder-prepare"   = { job = "recorder-prepare", schedule = "30 7 * * 1-5" }
    "recorder-morning"   = { job = "recorder-morning", schedule = "56 8 * * 1-5" }
    "recorder-window"    = { job = "recorder-window", schedule = "4-59/5 8-15 * * 1-5" } # 5分刻みの時刻の1分前。窓の時刻でなければすぐ終わる
    "recorder-close"     = { job = "recorder-close", schedule = "23 15 * * 1-5" }
    "recorder-reconcile" = { job = "recorder-reconcile", schedule = "0 20 * * 1-5" }
  }
}

resource "google_cloud_scheduler_job" "recorder" {
  for_each  = local.recorder_schedules
  name      = each.key
  region    = var.recorder_region
  schedule  = each.value.schedule
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.recorder_region}/jobs/${each.value.job}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
}

output "recorder_service_account" {
  value = google_service_account.recorder.email
}

output "recorder_artifact_registry_url" {
  value = "${var.recorder_region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.recorder_images.repository_id}"
}
