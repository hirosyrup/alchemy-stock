"""準備検証 (PLAN.md §6.4) に使う開示を、候補の種類ごとに同じ数ずつ無作為に選ぶ。

株価は見ないが、念のため学習期間の中から選ぶ。選んだ一覧は data/prep/ に残し、同じ種 (seed) なら同じものが選ばれる。

    uv run python scripts/make_prep_sample.py --per-kind 20
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.screening import CANDIDATE, GROUP_OF  # noqa: E402
from alchemy_stock.store import read_jsonl, write_jsonl  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--per-kind", type=int, default=20)
    p.add_argument("--seed", type=int, default=20261101)
    p.add_argument("--start", default="2016-10-01")
    p.add_argument("--end", default="2021-10-31")
    p.add_argument("--out", default="data/prep/sample_v1.jsonl")
    args = p.parse_args()

    passed = [
        r
        for r in read_jsonl(Path("data/screened/disclosures.jsonl"))
        if r["dropped_by"] is None and args.start <= r["date"] <= args.end
    ]
    rnd = random.Random(args.seed)
    chosen: dict[str, dict] = {}
    kinds = sorted({k for r in passed for k in r["kinds"] if GROUP_OF[k] == CANDIDATE})
    for kind in kinds:
        pool = sorted((r for r in passed if kind in r["kinds"] and r["doc_id"] not in chosen), key=lambda r: r["doc_id"])
        for r in rnd.sample(pool, min(args.per_kind, len(pool))):
            chosen[r["doc_id"]] = {**r, "sampled_for": kind}
        print(f"{kind}: {min(args.per_kind, len(pool))}件 (母数 {len(pool):,})")
    write_jsonl(Path(args.out), list(chosen.values()))
    print(f"→ {args.out} ({len(chosen)}件)")


if __name__ == "__main__":
    main()
