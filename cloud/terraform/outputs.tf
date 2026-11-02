output "raw_bucket" {
  value = google_storage_bucket.raw.url
}

output "disclosures_dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.disclosures.dataset_id}"
}

output "artifact_registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "document_fetcher_service_account" {
  value = google_service_account.document_fetcher.email
}
