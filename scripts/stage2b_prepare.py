"""段階2b (research/stage2b_revision/PREREG.md): 業績予想の修正のお知らせに、開示の時点で分かっていた数字の表を付けて LLM に渡す形にする。

対象は、場の外に出た業績予想の修正のお知らせ (向き・幅を問わない) で、次の朝の始値で売買できるもの。
--set a は 2016-11 〜 2019 からランダムに 300件 (種 0、磨く場所)、--set b は 2020 〜 2021-10 から 500件 (種 0、1回だけ確かめる)。
番号 (a001 / b001 ...) と書類の対応は data/stage2b/items_<set>.jsonl にだけ書く。
--out の下に <番号>.md (数字の表 + 伏せたお知らせの文字) を置く。サブエージェントにはこのフォルダだけを渡す。

    uv run --group extract python scripts/stage2b_prepare.py --set a --out <フォルダ>
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.liquidity import MIN_ADV  # noqa: E402
from alchemy_stock.mask import clean, mask, name_variants  # noqa: E402
from stage1_numbers import BARS, FINS, MASTER, build  # noqa: E402

SETS = {"a": (date(2016, 11, 1), date(2019, 12, 31), 300), "b": (date(2020, 1, 1), date(2021, 10, 31), 500)}
UNIVERSE = (date(2016, 11, 1), date(2019, 12, 31))  # 会社の大きさの帯は、磨く場所の全体で分ける
RAW = Path("data/stage2b/raw")
RAW_2A = Path("data/stage2a/raw")
ITEMS_2A = Path("data/stage2a/items.jsonl")
TARGETS = "data/targets/documents_v1.jsonl"
BUCKET = "alchemy-stock-513407-raw"
PAGES = 3
PRE_DAYS = (5, 20, 60)
LINES = (("sales", "売上"), ("op", "営業利益"), ("odp", "経常利益"), ("np", "純利益"))


def prepare(con: duckdb.DuckDBPyConnection) -> None:
    """予想の流れ (通期・上期、連結が無ければ単体)、決算の実績、値段の表を作る。"""
    v = {k: (f"coalesce({a}::double, {b}::double)") for k, a, b in (
        ("s", "FSales", "FNCSales"), ("o", "FOP", "FNCOP"), ("r", "FOdP", "FNCOdP"), ("n", "FNP", "FNCNP"),
        ("hs", "FSales2Q", "FNCSales2Q"), ("ho", "FOP2Q", "FNCOP2Q"), ("hr", "FOdP2Q", "FNCOdP2Q"), ("hn", "FNP2Q", "FNCNP2Q"),
        ("xs", "NxFSales", "NxFNCSales"), ("xo", "NxFOP", "NxFNCOP"), ("xr", "NxFOdP", "NxFNCOdP"), ("xn", "NxFNp", "NxFNCNP"),
        ("xhs", "NxFSales2Q", "NxFNCSales2Q"), ("xho", "NxFOP2Q", "NxFNCOP2Q"), ("xhr", "NxFOdP2Q", "NxFNCOdP2Q"),
        ("xhn", "NxFNp2Q", "NxFNCNP2Q"),
        ("asl", "Sales", "NCSales"), ("ao", "OP", "NCOP"), ("ar", "OdP", "NCOdP"), ("an", "NP", "NCNP"))}
    con.sql(f"""
        create table g as
        select DiscDate::date d, case when length(DiscTime) = 5 then DiscTime || ':00' else DiscTime end t,
               Code code, DiscNo no, regexp_extract(DocType, '^[^_]+') kind, CurPerType per,
               CurFYSt::date fyst, CurFYEn::date fy, NxtFYEn::date nfy,
               {", ".join(f"{e} {k}" for k, e in v.items())}
        from read_csv('{FINS}', union_by_name=true, all_varchar=true)
        where DocType not like '%REIT%' and DocType not like 'OtherPeriod%'
    """)
    # 予想の流れ: 決算短信 (通期以外)・修正は今期の予想、通期の決算短信は来期の予想を、その会計年度の予想として並べる
    con.sql("""
        create table fs as
        with s as (
            select code, fy, d, t, no, kind, 'FY' h, s vs, o vo, r vd, n vn from g where kind <> 'FYFinancialStatements'
            union all select code, fy, d, t, no, kind, 'H1', hs, ho, hr, hn from g where kind <> 'FYFinancialStatements'
            union all select code, nfy, d, t, no, kind, 'FY', xs, xo, xr, xn from g where kind = 'FYFinancialStatements'
            union all select code, nfy, d, t, no, kind, 'H1', xhs, xho, xhr, xhn from g where kind = 'FYFinancialStatements'
        )
        select *,
            last_value(vs ignore nulls) over w ps, last_value(vo ignore nulls) over w po,
            last_value(vd ignore nulls) over w pd, last_value(vn ignore nulls) over w pn
        from s where fy is not null
        window w as (partition by code, fy, h order by d, t, no rows between unbounded preceding and 1 preceding)
    """)
    con.sql("create table ci as select date, row_number() over (order by date) i, o, c from cal")
    con.sql(f"""
        create table px as
        select Code code, Date::date date, C::double c,
               sum(ln(coalesce(AdjFactor::double, 1))) over (partition by Code order by Date) s
        from read_csv('{BARS}', union_by_name=true, all_varchar=true)
        where C::double > 0
    """)


def universe(con, start: date, end: date) -> list[tuple]:
    """場の外に出た修正のお知らせを、会社と入る日ごとにまとめる (同じ朝に入るものは1件)。"""
    return con.execute(f"""
        with rev as (
            select g.code, g.d, g.t, g.no, c.date entry
            from g asof join cal c on c.date >= case when g.t < '09:00:00' then g.d else g.d + 1 end
            where g.kind = 'EarnForecastRevision'
              and (g.t < '09:00:00' or g.t >= case when g.d <= date '2024-11-01' then '15:00:00' else '15:30:00' end)
        )
        select rev.code, rev.entry, any_value(tr.adv),
               list(rev.no order by rev.d, rev.t, rev.no), list('1401' || rev.no order by rev.d, rev.t, rev.no),
               list(tg.date order by rev.d, rev.t, rev.no), any_value(m.CoName), any_value(m.CoNameEn)
        from rev
        join tr on tr.cand = 'ALL' and tr.code = rev.code and tr.entry = rev.entry
        left join read_json('{TARGETS}') tg on tg.doc_id = '1401' || rev.no
        left join read_csv('{MASTER}', union_by_name=true, all_varchar=true) m on m.Code = rev.code and m.Date::date = rev.entry
        where tr.prod = '011' and tr.mkt not in ('0105', '0109') and tr.adv >= {MIN_ADV} and tr.o > 0 and tr.c > 0
          and rev.entry between ? and ?
        group by rev.code, rev.entry
        order by rev.entry, rev.code
    """, [start, end]).fetchall()


def facts(con, code: str, entry: date, nos: list[str]) -> dict:
    """開示の時点で分かっていた数字。"""
    first, last = nos[0], nos[-1]
    d, t, fy, fyst = con.execute("select d, t, fy, fyst from g where code = ? and no = ?", [code, last]).fetchone()
    d0 = con.execute("select min(d) from g where code = ? and no = ?", [code, first]).fetchone()[0]
    out: dict = {}
    for h in ("FY", "H1"):
        new = con.execute("select vs, vo, vd, vn from fs where code = ? and h = ? and no = ?", [code, h, last]).fetchone()
        if not new or all(x is None for x in new):
            continue
        # 修正前は前の日までの予想 (同じ時刻に出た決算短信がすでに修正後の予想を載せていることがあるので、同じ日のものは使わない)
        prior = con.execute("""
            select arg_max(vs, k) filter (where vs is not null), arg_max(vo, k) filter (where vo is not null),
                   arg_max(vd, k) filter (where vd is not null), arg_max(vn, k) filter (where vn is not null)
            from (select *, d::varchar || t || no k from fs where code = ? and fy = ? and h = ? and d < ?)
        """, [code, fy, h, d0]).fetchone()
        out[h] = {"prior": dict(zip(("sales", "op", "odp", "np"), prior)), "new": dict(zip(("sales", "op", "odp", "np"), new))}
    out["month"] = (d.year - fyst.year) * 12 + d.month - fyst.month + 1 if fyst else None
    out["fy_end_month"] = fy.month if fy else None
    out["same_day"] = con.execute(
        "select any_value(per) from g where code = ? and d = ? and kind like '%FinancialStatements'", [code, d]).fetchone()[0]
    # 前期の実績 (この会計年度より前の通期の決算短信で、開示の日までに出ていたもの)
    prev = con.execute("""
        select fy, asl, ao, ar, an from g where code = ? and kind = 'FYFinancialStatements' and fy < ? and d <= ?
        order by fy desc, d desc limit 1""", [code, fy, d]).fetchone()
    out["prev_actual"] = dict(zip(("sales", "op", "odp", "np"), prev[1:])) if prev else None
    # 進み具合: 直近の四半期決算 (同じ日を含む) の累計実績 ÷ 修正前の通期予想、前期の同じ時点の累計 ÷ 前期の実績
    q = con.execute("""
        select per, asl, coalesce(ao, ar), ao is null from g where code = ? and fy = ? and kind like '%FinancialStatements'
          and per in ('1Q', '2Q', '3Q') and d <= ? order by d desc, t desc limit 1""", [code, fy, d]).fetchone()
    if q and "FY" in out:
        per, cs, cp, used_odp = q
        ps, pp = out["FY"]["prior"]["sales"], out["FY"]["prior"]["odp" if used_odp else "op"]
        last_year = None
        if prev:
            ly = con.execute("""select asl, coalesce(ao, ar) from g where code = ? and fy = ? and per = ?
                                and kind like '%FinancialStatements' order by d desc limit 1""", [code, prev[0], per]).fetchone()
            if ly:
                pa = prev[1], prev[3 if used_odp else 2]
                last_year = (ratio(ly[0], pa[0]), ratio(ly[1], pa[1]))
        out["progress"] = {"per": per, "profit": "経常利益" if used_odp else "営業利益",
                           "now": (ratio(cs, ps), ratio(cp, pp)), "last_year": last_year}
    # 今期の予想がこれまでに何度変わったか (決算発表の中での変更も数える、通期を優先して上期でも見る)、前回の向き
    prev_changes = con.execute("""
        select coalesce(any_value(sign(vo - po)) filter (where h = 'FY'), any_value(sign(vn - pn)) filter (where h = 'FY'),
                        any_value(sign(vo - po)) filter (where h = 'H1'), any_value(sign(vn - pn)) filter (where h = 'H1')) dir
        from fs where code = ? and fy = ? and d < ?
        group by d, t, no having dir is not null and dir <> 0 order by d, t, no
    """, [code, fy, d0]).fetchall()
    out["count"], out["prev_dir"] = len(prev_changes) + 1, (prev_changes[-1][0] if prev_changes else None)
    # 開示の前の値動き (入る日の前の取引日の終値まで、TOPIX との差)
    pre = {}
    for k in PRE_DAYS:
        r = con.execute(f"""
            with b as (
                select c0.date d0, cn.date dn, c0.c t0, cn.c tn from ci e
                join ci c0 on c0.i = e.i - 1 join ci cn on cn.i = e.i - 1 - {k} where e.date = ?
            )
            select (p0.c / pn.c) * exp(pn.s - p0.s) - t0 / tn, p0.date, pn.date from b
            asof join px p0 on p0.code = ? and b.d0 >= p0.date
            asof join px pn on pn.code = ? and b.dn >= pn.date
        """, [entry, code, code]).fetchone()
        pre[k] = r[0] if r and r[1] and r[2] and (r[1] - r[2]).days >= k else None
    out["pre"] = pre
    return out


def ratio(a, b):
    return a / b if a is not None and b not in (None, 0) and b > 0 else None


def rate(f: dict) -> float | None:
    """数字だけで並べる比べる相手: 通期 (無ければ上期) の営業利益 → 経常 → 純利益のうち、変わった最初のものの
    修正前からの変化 ÷ |修正前|。どれも変わっていなければ 0、修正前が無ければ None。"""
    seen = False
    for h in ("FY", "H1"):
        if h in f:
            for k in ("op", "odp", "np"):
                p, n = f[h]["prior"][k], f[h]["new"][k]
                if p not in (None, 0) and n is not None:
                    seen = True
                    if n != p:
                        return (n - p) / abs(p)
    return 0.0 if seen else None


def money(x) -> str:
    if x is None:
        return "—"
    m = x / 1e6
    return f"{m:,.0f}" if abs(m) >= 100 else f"{m:,.1f}"


def pct(x, signed=True) -> str:
    return "—" if x is None else (f"{x * 100:+.1f}%" if signed else f"{x * 100:.0f}%")


def change(p, n) -> str:
    if p is None or n is None:
        return "—"
    if p > 0:
        return "赤字に転落" if n < 0 else pct(n / p - 1)
    return "黒字に転換" if n > 0 >= p else ("赤字が縮小" if n > p else ("赤字が拡大" if n < p else "変わらず"))


def render(f: dict, size: str) -> str:
    out = ["## 数字の表 (開示の時点で分かっていたもの。金額は百万円)", ""]
    for h, title in (("FY", "通期の予想"), ("H1", "上期 (第2四半期までの累計) の予想")):
        if h not in f:
            continue
        if h == "H1" and (f["month"] or 0) > 6:
            title += "。上期はすでに終わった後の発表"
        out += [f"【{title}】", "| | 修正前 | 修正後 | 変化 |" + (" 前期の実績 | 前期の実績との比較 |" if h == "FY" else ""),
                "|---|---|---|---|" + ("---|---|" if h == "FY" else "")]
        for k, label in LINES:
            p, n = f[h]["prior"][k], f[h]["new"][k]
            if p is None and n is None:
                continue
            row = f"| {label} | {money(p)} | {money(n)} | {change(p, n)} |"
            if h == "FY":
                a = (f["prev_actual"] or {}).get(k)
                row += f" {money(a)} | {change(a, n)} |"
            out.append(row)
        out.append("")
    if f["month"] is not None:
        when = (f"決算期の {f['month']}か月目" if f["month"] <= 12 else "決算期が終わった後 (決算の発表の前)")
        out.append(f"【時期】{when} (決算期末は {f['fy_end_month']}月)")
    out.append(f"【同じ日の決算発表】{'あり (' + f['same_day'] + ' の決算)' if f['same_day'] else 'なし'}")
    p = f.get("progress")
    if p:
        now = f"売上 {pct(p['now'][0], False)}、{p['profit']} {pct(p['now'][1], False)}"
        ly = f"売上 {pct(p['last_year'][0], False)}、{p['profit']} {pct(p['last_year'][1], False)}" if p["last_year"] else "—"
        out.append(f"【進み具合】{p['per']} までの累計実績 ÷ 修正前の通期予想: {now} (前期の同じ時点 ÷ 前期の実績: {ly})")
    prev = {1: "上方", -1: "下方", 0: "変わらず"}.get(f["prev_dir"], "なし")
    out.append(f"【今期の予想の変更】今回で {f['count']}回目 (決算発表の中での変更も数える。前回の向き: {prev})")
    out.append("【開示の前の値動き (TOPIX との差)】" + "  ".join(f"前{k}日 {pct(f['pre'][k])}" for k in PRE_DAYS))
    out.append(f"【会社の大きさ】ふだんの売買代金の帯: {size}")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--set", choices=SETS, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    start, end, count = SETS[args.set]

    con = duckdb.connect()
    build(con)
    prepare(con)
    advs = sorted(r[2] for r in universe(con, *UNIVERSE))
    cuts = advs[len(advs) // 3], advs[2 * len(advs) // 3]
    pool = universe(con, start, end)
    random.Random(0).shuffle(pool)
    print(f"対象 {len(pool)}件から {count}件")

    raw_2a = {(it["code"], it["entry"]): it["qid"] for it in map(json.loads, ITEMS_2A.read_text().splitlines())}
    import pymupdf
    import pymupdf4llm
    from google.cloud import storage

    bucket = storage.Client(project="alchemy-stock-513407").bucket(BUCKET)
    RAW.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    items, missing = [], 0
    for code, entry, adv, nos, doc_ids, dates, name, name_en in pool:
        if len(items) == count:
            break
        raw = RAW / f"{code}_{entry}.md"
        if not raw.exists() and (code, str(entry)) in raw_2a:
            raw.write_text((RAW_2A / f"{raw_2a[(code, str(entry))]}.md").read_text())
        if not raw.exists():
            parts = []
            for doc_id, d in zip(doc_ids, dates):
                blob = bucket.blob(f"pdf/{d.year}/{doc_id}.pdf") if d else None
                if blob is None or not blob.exists():
                    continue
                with pymupdf.open(stream=blob.download_as_bytes(), filetype="pdf") as doc:
                    parts.append(pymupdf4llm.to_markdown(doc, pages=list(range(min(PAGES, doc.page_count)))))
            if not parts:
                missing += 1
                print(f"{code} {entry}: PDF が無い", file=sys.stderr)
                continue
            raw.write_text(clean("\n\n---\n\n".join(parts)))
        f = facts(con, code, entry, nos)
        size = "大 (上位 1/3)" if adv >= cuts[1] else ("中" if adv >= cuts[0] else "小 (下位 1/3)")
        qid = f"{args.set}{len(items) + 1:03d}"
        text = mask(clean(raw.read_text()), name_variants(name, name_en), code)
        (args.out / f"{qid}.md").write_text(render(f, size) + "\n\n---\n\n## お知らせの本文\n\n" + text)
        items.append({"qid": qid, "code": code, "entry": str(entry), "doc_ids": doc_ids, "adv": adv,
                      "size": size, "rate": rate(f), "name": name, "name_en": name_en})
    path = Path(f"data/stage2b/items_{args.set}.jsonl")
    path.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items))
    print(f"書いた {len(items)}件、PDF が無くて飛ばした {missing}件 → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
