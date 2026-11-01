"""開示の一覧を GCS に置き、BigQuery に読み込む (PLAN.md §8)。

JSONL を gzip して GCS の lists/ に置き、そこから読み込みジョブで表を作り直す。
GCS にも同じものが残るので、表を作り直したくなったらそこから読み直せる。
"""
from __future__ import annotations

import gzip
import json
import google.auth
from google.cloud import bigquery, storage

PROJECT = "alchemy-stock-513407"
BUCKET = f"{PROJECT}-raw"
DATASET = "disclosures"

S = bigquery.SchemaField

SCHEMAS: dict[str, list[bigquery.SchemaField]] = {
    "jpx_companies": [
        S("code", "STRING", "REQUIRED"),
        S("name", "STRING"),
        S("market", "STRING", description="プライム / スタンダード / グロース。上場廃止は -"),
        S("industry", "STRING", description="業種。ETF などは -"),
        S("fiscal_month", "STRING"),
        S("flag", "STRING"),
        S("delisted", "BOOL"),
        S("delisted_on", "DATE", description="上場廃止日"),
        S("n_disclosures", "INT64"),
        S("fetched_at", "TIMESTAMP"),
    ],
    "jpx_disclosures": [
        S("code", "STRING", "REQUIRED"),
        S("date", "DATE", "REQUIRED"),
        S("category", "STRING", description="区分のコード (1101 決算情報 など)"),
        S("category_name", "STRING"),
        S("title", "STRING"),
        S("doc_id", "STRING", "REQUIRED", description="PDF のファイル名。TDnet と同じ番号。同じ開示が2つの区分に載ると2行になる"),
        S("pdf_url", "STRING"),
        S("xbrl_url", "STRING"),
        S("summary_html_url", "STRING"),
        S("attachment_html_url", "STRING"),
        S("fetched_at", "TIMESTAMP"),
    ],
    "tdnet_disclosures": [
        S("date", "DATE", "REQUIRED"),
        S("time", "STRING", description="公開時刻 (日本時間 HH:MM)"),
        S("code", "STRING"),
        S("name", "STRING"),
        S("title", "STRING"),
        S("doc_id", "STRING", "REQUIRED"),
        S("pdf_url", "STRING"),
        S("xbrl_url", "STRING"),
        S("exchange", "STRING"),
        S("history", "STRING"),
        S("fetched_at", "TIMESTAMP"),
    ],
    "screened_disclosures": [
        S("doc_id", "STRING", "REQUIRED"),
        S("code", "STRING"),
        S("date", "DATE"),
        S("title", "STRING"),
        S("kinds", "STRING", "REPEATED", description="種類 (複数つくことがある)"),
        S("groups", "STRING", "REPEATED", description="候補 / 保留 / 付随 / 対象外"),
        S("dropped_by", "STRING", description="落とした理由。通過は NULL"),
        S("event_id", "STRING", description="同じ会社の同じ日を束ねた出来事"),
        S("rules_version", "STRING"),
    ],
}


def _credentials():
    # 手元の既定の請求先 (quota project) が FX のプロジェクトになっていても、こちらに付くようにする
    credentials, _ = google.auth.default(quota_project_id=PROJECT)
    return credentials


def load_table(table: str, rows: list[dict], gcs_path: str) -> int:
    """rows を gzip した JSONL で GCS に置き、BigQuery の表を作り直す。読み込んだ行数を返す。"""
    allowed = {f.name for f in SCHEMAS[table]}
    data = "".join(json.dumps({k: v for k, v in r.items() if k in allowed}, ensure_ascii=False) + "\n" for r in rows)

    credentials = _credentials()
    blob = storage.Client(project=PROJECT, credentials=credentials).bucket(BUCKET).blob(gcs_path)
    blob.upload_from_string(gzip.compress(data.encode("utf-8")), content_type="application/gzip")

    client = bigquery.Client(project=PROJECT, credentials=credentials)
    job = client.load_table_from_uri(
        f"gs://{BUCKET}/{gcs_path}",
        f"{PROJECT}.{DATASET}.{table}",
        job_config=bigquery.LoadJobConfig(
            schema=SCHEMAS[table],
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()
    return client.get_table(f"{PROJECT}.{DATASET}.{table}").num_rows
