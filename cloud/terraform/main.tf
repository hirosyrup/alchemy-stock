# =============================================================================
# Terraform Configuration - alchemy-stock
# =============================================================================
# 状態ファイル用のバケット (alchemy-stock-terraform-state) だけは Terraform の外で作った:
#   gcloud storage buckets create gs://alchemy-stock-terraform-state \
#     --project alchemy-stock-513407 --location us-central1 \
#     --uniform-bucket-level-access --public-access-prevention
#   gcloud storage buckets update gs://alchemy-stock-terraform-state --versioning

terraform {
  required_version = ">= 1.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "alchemy-stock-terraform-state"
    prefix = "alchemy-stock"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# =============================================================================
# API Enablement
# =============================================================================

resource "google_project_service" "apis" {
  for_each = toset([
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudscheduler.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# =============================================================================
# Cloud Storage - 開示の PDF の原本
# =============================================================================
# 米国リージョンは Standard 5GB まで無料枠。取得したデータは公開しないので、公開を強制的に防ぐ。
# 文字起こしのやり方が固まったら、Archive に移すライフサイクルを足す (PLAN.md §8)。

resource "google_storage_bucket" "raw" {
  name     = "${var.project_id}-raw"
  location = upper(var.region)

  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  depends_on = [google_project_service.apis]
}

# =============================================================================
# BigQuery - 開示の一覧・本文のテキスト
# =============================================================================

resource "google_bigquery_dataset" "disclosures" {
  dataset_id  = "disclosures"
  location    = var.bigquery_location
  description = "適時開示の一覧 (TDnet・東証上場会社情報サービス) と本文のテキスト"

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Cloud Run ジョブ - 開示の PDF・XBRL を取って GCS に置く
# =============================================================================
# ジョブ本体はイメージや引数を差し替えながら使うので、cloud/*.sh (gcloud) で作る。
# ここではイメージ置き場と、ジョブが使うサービスアカウント・権限だけを持つ。

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "alchemy-stock"
  format        = "DOCKER"
  description   = "alchemy-stock の Cloud Run 用イメージ"

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

resource "google_service_account" "document_fetcher" {
  account_id   = "document-fetcher"
  display_name = "開示の PDF・XBRL を取る Cloud Run ジョブ"
}

# 対象の一覧を読み、取ったものを置き、何があるかの一覧を取る
resource "google_storage_bucket_iam_member" "document_fetcher_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.document_fetcher.email}"
}

# =============================================================================
# Cloud Run ジョブ - 毎日の取得 (TDnet の開示一覧、時刻つき)
# =============================================================================
# TDnet の閲覧サイトは直近31日分しか残らないので、毎日 GCS に貯める。ジョブは cloud/collect_tdnet.sh で作る。

resource "google_service_account" "daily_collector" {
  account_id   = "daily-collector"
  display_name = "毎日の取得 (TDnet など) の Cloud Run ジョブ"
}

resource "google_storage_bucket_iam_member" "daily_collector_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.daily_collector.email}"
}

# Cloud Scheduler がジョブを起動するときに使う
resource "google_service_account" "scheduler" {
  account_id   = "job-scheduler"
  display_name = "Cloud Scheduler から Cloud Run ジョブを起動する"
}

resource "google_project_iam_member" "scheduler_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

# 毎日 23:30 (日本時間)。その日の開示が出そろった後。直近3日分は毎回取り直す
resource "google_cloud_scheduler_job" "collect_tdnet_daily" {
  name      = "collect-tdnet-daily"
  region    = var.region
  schedule  = "30 23 * * *"
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/collect-tdnet:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
}
