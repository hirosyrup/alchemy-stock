output "raw_bucket" {
  value = google_storage_bucket.raw.url
}

output "disclosures_dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.disclosures.dataset_id}"
}
