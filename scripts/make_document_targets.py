"""PDF を全件取る対象の一覧を作る (PLAN.md §5)。

ふるい分け a〜c を通った開示のうち、d (開示の日より前の20営業日の平均売買代金が MIN_ADV 以上) も通ったもの。
PDF と XBRL の URL まで入れるので、Cloud Run 側は東証上場会社情報サービスの一覧を持たなくてよい。

    uv run python scripts/make_document_targets.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.liquidity import MIN_ADV, create_adv, create_passed_docs  # noqa: E402

JPX = "data/jpx_listed/disclosures/*.jsonl"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-adv", type=float, default=MIN_ADV)
    p.add_argument("--out", type=Path, default=Path("data/targets/documents_v1.jsonl"))
    p.add_argument("--kinds", nargs="+", help="候補以外の種類を後から足すとき、その種類だけを対象にする (例: 事業の知らせ 資産の売り買い)")
    p.add_argument("--skip", type=Path, help="すでに取った対象の一覧。ここにある書類は外す")
    args = p.parse_args()

    con = duckdb.connect()
    create_adv(con)
    create_passed_docs(con, kinds=args.kinds)
    if args.skip:
        con.sql(f"delete from passed where doc_id in (select doc_id from read_json('{args.skip}'))")
    # 同じ開示が2つの区分に載ると2行あるので、doc_id ごとに1つにする
    con.sql(f"""
        create table urls as
        select doc_id, any_value(pdf_url) as pdf_url, max(nullif(xbrl_url, '')) as xbrl_url
        from read_json('{JPX}', columns = {{'doc_id': 'VARCHAR', 'pdf_url': 'VARCHAR', 'xbrl_url': 'VARCHAR'}})
        group by doc_id
    """)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    con.sql(f"""
        copy (
            select p.doc_id, p.code, strftime(p.date, '%Y-%m-%d') as date, p.kinds, round(p.adv) as adv, u.pdf_url, u.xbrl_url
            from passed p join urls u using (doc_id)
            where p.adv >= {args.min_adv}
            order by p.date, p.doc_id
        ) to '{args.out}' (format json)
    """)
    n, tanshin, xbrl = con.execute(f"""
        select count(*), count(*) filter (where list_contains(kinds, '決算短信')), count(xbrl_url)
        from read_json('{args.out}')
    """).fetchone()
    print(f"{args.out}: {n:,}件 (うち決算短信 {tanshin:,}件、XBRL あり {xbrl:,}件)")


if __name__ == "__main__":
    main()
