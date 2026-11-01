# =============================================================================
# Variables - alchemy-stock
# =============================================================================

variable "project_id" {
  description = "GCP Project ID"
  type        = string
  default     = "alchemy-stock-513407"
}

variable "region" {
  description = "GCP Region (Cloud Storage の無料枠がある米国リージョン)"
  type        = string
  default     = "us-central1"
}

variable "bigquery_location" {
  description = "BigQuery dataset location (us-central1 のバケットから取り込める US マルチリージョン)"
  type        = string
  default     = "US"
}
