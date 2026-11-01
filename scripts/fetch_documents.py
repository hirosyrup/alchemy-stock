"""開示の PDF (と、あれば XBRL) を東証上場会社情報サービスから取ってくる。

XBRL は、決算短信の1ページ目 (サマリー) を数字に印を付けた HTML (インライン XBRL) にしたもの。

doc_id を含む JSONL を渡すと、その開示だけを取る。取ったものは data/pdf/YYYY/<doc_id>.pdf、
data/xbrl/YYYY/<doc_id>.htm に置く。既にあるものは飛ばすので、止まっても同じコマンドで続きから再開できる。

    uv run python scripts/fetch_documents.py data/prep/sample_v1.jsonl --xbrl
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.store import read_jsonl  # noqa: E402

DATA = Path("data")


def save(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", help="doc_id を含む JSONL")
    p.add_argument("--xbrl", action="store_true", help="XBRL があれば一緒に取る")
    p.add_argument("--interval", type=float, default=3.0)
    args = p.parse_args()

    wanted = {r["doc_id"] for r in read_jsonl(Path(args.targets))}
    urls: dict[str, dict] = {}
    for f in sorted((DATA / "jpx_listed" / "disclosures").glob("*.jsonl")):
        for r in read_jsonl(f):
            if r["doc_id"] in wanted:
                urls.setdefault(r["doc_id"], r)
    print(f"対象 {len(wanted)}件 (一覧に見つかったもの {len(urls)}件)")

    session = PoliteSession(min_interval=args.interval)
    done = missing = 0
    total_bytes = 0
    try:
        for i, (doc_id, r) in enumerate(sorted(urls.items()), 1):
            jobs = [(r["pdf_url"], DATA / "pdf" / r["date"][:4] / f"{doc_id}.pdf")]
            if args.xbrl and r.get("xbrl_url"):
                jobs.append((r["xbrl_url"], DATA / "xbrl" / r["date"][:4] / f"{doc_id}{Path(r['xbrl_url']).suffix}"))
            for url, path in jobs:
                if path.exists():
                    continue
                res = session.get(url, allow_404=True)
                if res is None:
                    missing += 1
                    print(f"  見つからない: {url}")
                    continue
                save(path, res.content)
                total_bytes += len(res.content)
                done += 1
            if i % 50 == 0:
                print(f"  {i}/{len(urls)} (読み込み {session.count} 回, {total_bytes / 1e6:.1f}MB)")
    except Blocked as e:
        print(f"拒否されたので止めます: {e}")
        sys.exit(2)
    print(f"新しく保存 {done}件 / 見つからない {missing}件 / 読み込み {session.count} 回 / {total_bytes / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
