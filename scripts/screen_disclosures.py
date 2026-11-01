"""集めた開示の一覧に、ふるい分け a・b・c・e を当てる (② ふるい分け、PLAN.md §3)。

落としたものも理由付きで全部残す。d (売買の量) は株価がそろってから当てる。

    uv run python scripts/screen_disclosures.py
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.screening import CANDIDATE, RULES_VERSION, screen  # noqa: E402
from alchemy_stock.store import read_jsonl, write_jsonl  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", default="data/jpx_listed")
    p.add_argument("--out", default="data/screened/disclosures.jsonl")
    args = p.parse_args()

    src = Path(args.src)
    companies = {c["code"]: c for c in read_jsonl(src / "companies.jsonl")}
    disclosures = [d for f in sorted((src / "disclosures").glob("*.jsonl")) for d in read_jsonl(f)]
    rows = screen(disclosures, companies)
    write_jsonl(Path(args.out), rows)

    print(f"決まりの版 {RULES_VERSION}: 開示 {len(rows):,}件 → {args.out}")
    for reason, n in collections.Counter(r["dropped_by"] or "通過" for r in rows).most_common():
        print(f"  {reason}: {n:,}")
    events = collections.defaultdict(set)
    for r in rows:
        if r["dropped_by"] is None:
            events[r["date"][:4]].add(r["event_id"])
    print("通過した出来事 (会社×日) の年ごとの数:")
    for y in sorted(events):
        print(f"  {y}: {len(events[y]):,}")
    kinds = collections.Counter(k for r in rows if r["dropped_by"] is None for k in r["kinds"])
    print(f"通過した開示の種類 ({CANDIDATE}以外の種類も一緒に付いていれば数える):")
    for k, n in kinds.most_common():
        print(f"  {k}: {n:,}")


if __name__ == "__main__":
    main()
