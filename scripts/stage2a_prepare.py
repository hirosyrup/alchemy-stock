"""段階2a (research/stage2a_llm_trial/PREREG.md): 読ませるお知らせを選び、文字にして社名・コード・年を伏せる。

対象 (学習期間の U10 で、業績予想の修正のお知らせがあるもの) をランダムな順 (種 0) に並べ、
伏せた番号 (q0001 ...) を振って data/stage2a/items.jsonl に置く。番号と書類の対応はここにしか書かない。
--start / --count で選んだ範囲の PDF を GCS から取り、pymupdf4llm で最初の3ページを文字にして伏せ、
--out の下に <番号>.md として置く (サブエージェントにはこのフォルダだけを渡す)。

    uv run --group extract python scripts/stage2a_prepare.py --start 0 --count 100 --out <フォルダ>
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.mask import clean, mask, name_variants  # noqa: E402
from stage1_numbers import MASTER, build, eligible  # noqa: E402

ITEMS = Path("data/stage2a/items.jsonl")
RAW = Path("data/stage2a/raw")  # 伏せる前の文字 (伏せ方を直したときに PDF を取り直さないため)
TARGETS = "data/targets/documents_v1.jsonl"
BUCKET = "alchemy-stock-513407-raw"
PAGES = 3


def make_items() -> list[dict]:
    con = duckdb.connect()
    build(con)
    rows = con.execute(f"""
        with u as (select code, entry from tr where {eligible(1)} and entry <= date '2021-10-31'),
        rev as (
            select f.code, f.d, f.t, '1401' || f.no doc_id, c.date entry
            from f asof join cal c on c.date >= case when f.t < '09:00:00' then f.d else f.d + 1 end
            where f.kind = 'EarnForecastRevision'
              and (f.t < '09:00:00' or f.t >= case when f.d <= date '2024-11-01' then '15:00:00' else '15:30:00' end)
        )
        select u.code, u.entry::varchar, list(rev.doc_id order by rev.d, rev.t), list(tg.date order by rev.d, rev.t),
               any_value(m.CoName), any_value(m.CoNameEn)
        from u join rev on rev.code = u.code and rev.entry = u.entry
        join read_json('{TARGETS}') tg on tg.doc_id = rev.doc_id
        left join read_csv('{MASTER}', union_by_name=true, all_varchar=true) m on m.Code = u.code and m.Date::date = u.entry
        group by u.code, u.entry
        order by u.entry, u.code
    """, ["U10"]).fetchall()
    items = [{"code": c, "entry": e, "doc_ids": ids, "dates": [str(d) for d in ds], "name": n, "name_en": en}
             for c, e, ids, ds, n, en in rows]
    random.Random(0).shuffle(items)
    for i, it in enumerate(items, 1):
        it["qid"] = f"q{i:04d}"
    return items


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    if not ITEMS.exists():
        ITEMS.parent.mkdir(parents=True, exist_ok=True)
        ITEMS.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in make_items()))
    items = [json.loads(line) for line in ITEMS.read_text().splitlines()]
    print(f"対象 {len(items)}件")

    import pymupdf
    import pymupdf4llm
    from google.cloud import storage

    bucket = storage.Client(project="alchemy-stock-513407").bucket(BUCKET)
    args.out.mkdir(parents=True, exist_ok=True)
    missing = 0
    RAW.mkdir(parents=True, exist_ok=True)
    for it in items[args.start:args.start + args.count]:
        raw = RAW / f"{it['qid']}.md"
        if not raw.exists():
            parts = []
            for doc_id, d in zip(it["doc_ids"], it["dates"]):
                blob = bucket.blob(f"pdf/{d[:4]}/{doc_id}.pdf")
                if not blob.exists():
                    continue
                with pymupdf.open(stream=blob.download_as_bytes(), filetype="pdf") as doc:
                    parts.append(pymupdf4llm.to_markdown(doc, pages=list(range(min(PAGES, doc.page_count)))))
            if not parts:
                missing += 1
                print(f"{it['qid']}: PDF がまだ無い", file=sys.stderr)
                continue
            raw.write_text(clean("\n\n---\n\n".join(parts)))
        text = mask(clean(raw.read_text()), name_variants(it["name"], it["name_en"]), it["code"])
        (args.out / f"{it['qid']}.md").write_text(text)
    print(f"書いた {args.count - missing}件、PDF が無い {missing}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
