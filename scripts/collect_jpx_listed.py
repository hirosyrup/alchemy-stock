"""東証上場会社情報サービスから、全社の10年分の適時開示の一覧を集める。

1. companies: 上場中 (プライム・スタンダード・グロース) と上場廃止 (廃止後5年分) の会社の一覧
2. disclosures: 会社ごとのページを開いて、開示の一覧を会社ごとのファイルに保存する。
   上場廃止日は company_meta.jsonl に追記する。
   既に保存した会社は飛ばすので、途中で止まっても同じコマンドで続きから再開できる。

    uv run python scripts/collect_jpx_listed.py companies
    uv run python scripts/collect_jpx_listed.py disclosures [--limit 10] [--codes 72030 37780]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.jpx_listed import (  # noqa: E402
    MARKETS,
    SEARCH_PAGE,
    fetch_company_page,
    parse_delisted_on,
    parse_disclosures,
    search_companies,
)
from alchemy_stock.store import now_utc, read_jsonl, write_jsonl  # noqa: E402

DETAIL_FORM_MARK = 'name="JJK010040Form"'


def collect_companies(session: PoliteSession, out: Path) -> None:
    companies = []
    for code, name in MARKETS.items():
        found = search_companies(session, market=code)
        print(f"{name}: {len(found)}社")
        companies += found
    delisted = search_companies(session, delisted=True)
    print(f"上場廃止: {len(delisted)}社")
    fetched_at = now_utc()
    write_jsonl(out / "companies.jsonl", [{**c, "fetched_at": fetched_at} for c in companies + delisted])


def collect_disclosures(session: PoliteSession, out: Path, limit: int | None, codes: list[str] | None) -> None:
    companies = read_jsonl(out / "companies.jsonl")
    if codes:
        companies = [c for c in companies if c["code"] in codes]
    todo = [c for c in companies if not (out / "disclosures" / f"{c['code']}.jsonl").exists()]
    print(f"対象 {len(companies)}社 / 残り {len(todo)}社")
    session.get(SEARCH_PAGE)
    for i, c in enumerate(todo[:limit] if limit else todo, 1):
        html = fetch_company_page(session, c["code"], c["flag"])
        if DETAIL_FORM_MARK not in html:
            # セッションが切れて検索ページに戻された。開き直して1回だけやり直す
            session.get(SEARCH_PAGE)
            html = fetch_company_page(session, c["code"], c["flag"])
            if DETAIL_FORM_MARK not in html:
                print(f"  {c['code']} 会社ページを開けなかったので飛ばします")
                continue
        rows = parse_disclosures(html, c["code"])
        fetched_at = now_utc()
        meta = {"code": c["code"], "delisted_on": parse_delisted_on(html), "n_disclosures": len(rows),
                "fetched_at": fetched_at}
        with (out / "company_meta.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        write_jsonl(out / "disclosures" / f"{c['code']}.jsonl", [{**r, "fetched_at": fetched_at} for r in rows])
        if i % 50 == 0 or i <= 5:
            print(f"  {i}/{len(todo)} {c['code']} {c['name']} {len(rows)}件 (読み込み {session.count} 回)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["companies", "disclosures"])
    p.add_argument("--out", type=Path, default=Path("data/jpx_listed"))
    p.add_argument("--limit", type=int)
    p.add_argument("--codes", nargs="*")
    p.add_argument("--interval", type=float, default=3.0)
    args = p.parse_args()

    session = PoliteSession(min_interval=args.interval)
    try:
        if args.step == "companies":
            collect_companies(session, args.out)
        else:
            collect_disclosures(session, args.out, args.limit, args.codes)
    except Blocked as e:
        print(f"拒否されたので止めます: {e}", file=sys.stderr)
        return 2
    print(f"読み込み {session.count} 回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
