"""手元に集めた開示の一覧とふるい分けの結果を、GCS に置いて BigQuery に読み込む。

表は毎回まるごと作り直す。GCS には gs://alchemy-stock-513407-raw/lists/ に同じものが残る。

    uv run python scripts/load_bigquery.py            # 全部
    uv run python scripts/load_bigquery.py screened   # 1つだけ
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.bq import load_table  # noqa: E402
from alchemy_stock.store import read_jsonl  # noqa: E402

DATA = Path("data")


def companies() -> list[dict]:
    meta = {m["code"]: m for m in read_jsonl(DATA / "jpx_listed" / "company_meta.jsonl")}
    return [
        {**c, "delisted_on": meta.get(c["code"], {}).get("delisted_on"), "n_disclosures": meta.get(c["code"], {}).get("n_disclosures")}
        for c in read_jsonl(DATA / "jpx_listed" / "companies.jsonl")
    ]


def jpx() -> list[dict]:
    return [r for f in sorted((DATA / "jpx_listed" / "disclosures").glob("*.jsonl")) for r in read_jsonl(f)]


def tdnet() -> list[dict]:
    return [r for f in sorted((DATA / "tdnet").glob("*/*.jsonl")) for r in read_jsonl(f)]


def screened() -> list[dict]:
    return read_jsonl(DATA / "screened" / "disclosures.jsonl")


TABLES = {
    "companies": ("jpx_companies", companies, "lists/jpx_companies.jsonl.gz"),
    "jpx": ("jpx_disclosures", jpx, "lists/jpx_disclosures.jsonl.gz"),
    "tdnet": ("tdnet_disclosures", tdnet, "lists/tdnet_disclosures.jsonl.gz"),
    "screened": ("screened_disclosures", screened, "lists/screened_disclosures.jsonl.gz"),
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*", help=f"読み込む表 {list(TABLES)} (省略で全部)")
    args = p.parse_args()
    if unknown := set(args.names) - set(TABLES):
        p.error(f"知らない表: {sorted(unknown)}")
    for name in args.names or TABLES:
        table, read, gcs_path = TABLES[name]
        rows = read()
        n = load_table(table, rows, gcs_path)
        print(f"{table}: 手元 {len(rows):,}行 → BigQuery {n:,}行")


if __name__ == "__main__":
    main()
