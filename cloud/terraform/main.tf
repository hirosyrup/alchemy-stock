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
