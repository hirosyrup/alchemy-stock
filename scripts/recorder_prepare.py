"""記録係の、次の取引日の材料を作る (docs/RECORDER_DESIGN.md §3 の「前の日の夜」)。

    uv run python scripts/recorder_prepare.py               # 15:30 より後なら次の取引日、前ならその日
    uv run python scripts/recorder_prepare.py --day 2026-11-09

作るもの: data/recorder/prep/<日付>.json
- calendar: 取引日の暦 (前後)
- universe: 前の取引日の時点で時価総額の上位 1,344 (普通株) の会社と、順位・終値 (調整前)・Saxo の Uic
- saxo: Saxo で扱っている日本株 CFD の 銘柄コード → Uic (全部、S16 用)
- book: 上位 1,344 の会社・会計年度ごとの今の業績予想 (49 の「前の予想」)

財務情報は手元の一括ダウンロード (data/jquants/fins/summary) に、それより後の日を J-Quants の API で足す
(API で取った日は data/recorder/fins_api/ に置き、次からは読み直さない)。前の取引日の日足がまだ出ていなければ止まる。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alchemy_stock.jquants import get_all, load_api_key, make_session  # noqa: E402
from alchemy_stock.recorder import rules  # noqa: E402
from alchemy_stock.recorder.prepare import ForecastBook, cap_ranks, fins_rows  # noqa: E402
from alchemy_stock.recorder.saxo_read import SaxoReader, code_of  # noqa: E402
from alchemy_stock.recorder import store  # noqa: E402
from alchemy_stock.recorder.store import now_jst  # noqa: E402

FINS_BASE = "fins_base.csv.gz"  # GCP では、手元の一括ダウンロードの必要な列だけを GCS に置いたもの (scripts/recorder_job.py upload-fins)
FINS_COLS = ["Code", "DiscDate", "DiscTime", "DiscNo", "DocType", "CurFYEn", "NxtFYEn", "FOP", "FOdP", "FNP",
             "NxFOP", "NxFOdP", "NxFNp", "ShOutFY", "TrShFY"]


def calendar(jq, a: date, b: date) -> list[date]:
    rows = get_all(jq, "/markets/calendar", **{"from": a.isoformat(), "to": b.isoformat()})
    return [date.fromisoformat(r["Date"]) for r in rows if r["HolDiv"] == "1"]


def fins_base() -> pd.DataFrame:
    if store.on_gcp():
        import io

        return pd.read_csv(io.BytesIO(store.read_bytes(FINS_BASE)), dtype=str, compression="gzip")
    return duckdb.sql(f"""select {', '.join(FINS_COLS)} from read_csv('{ROOT}/data/jquants/fins/summary/**/*.csv.gz',
        union_by_name=true, all_varchar=true)""").df()


def load_fins(jq, until: date) -> pd.DataFrame:
    local = fins_base()
    last = pd.to_datetime(local.DiscDate).max().date()
    extra, d = [], last + timedelta(days=1)
    while d <= until:
        name = f"fins_api/{d.isoformat()}.json"
        rows = store.read_json(name)
        if rows is None:
            rows = get_all(jq, "/fins/summary", date=d.isoformat())
            if d < until or rows:  # 前の取引日の分は、出るまで置かない
                store.write_json(name, rows)
        extra += rows
        d += timedelta(days=1)
    f = pd.concat([local, pd.DataFrame(extra, columns=FINS_COLS).astype(str).replace({"": None, "None": None, "nan": None})], ignore_index=True)
    print(f"財務情報: 手元 {len(local)}行 (〜{last}) + API {len(extra)}行 (〜{until})")
    return f


def shares(fins: pd.DataFrame, until: date) -> pd.Series:
    """会社ごとの、その日までの最新の (発行済み − 自己株)。検証の run_stage4.py の SH と同じ。"""
    f = fins[fins.ShOutFY.notna()].copy()
    f["dd"] = pd.to_datetime(f.DiscDate)
    f = f[f.dd <= pd.Timestamp(until)]
    f["sh"] = pd.to_numeric(f.ShOutFY, errors="coerce") - pd.to_numeric(f.TrShFY, errors="coerce").fillna(0)
    return f.dropna(subset=["sh"]).sort_values("dd", kind="stable").groupby("Code").sh.last()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=date.fromisoformat)
    a = p.parse_args(argv)
    jq = make_session(load_api_key(ROOT / ".env"), min_interval=1.0)
    now = now_jst()
    cal = calendar(jq, now.date() - timedelta(days=20), now.date() + timedelta(days=70))
    if a.day:
        day = a.day
    else:
        day = now.date() if (now.date() in cal and now.strftime("%H:%M") < "15:30") else rules.next_trading_day(cal, now.date())
    prev_day = max(d for d in cal if d < day)
    print(f"対象の日 {day}、前の取引日 {prev_day}")

    bars = pd.DataFrame(get_all(jq, "/equities/bars/daily", date=prev_day.isoformat()))
    if bars.empty:
        print(f"{prev_day} の日足がまだ J-Quants に出ていない。後でもう一度動かす")
        return 2
    master = pd.DataFrame(get_all(jq, "/equities/master", date=prev_day.isoformat()))
    common = set(master[master.S33Nm.notna() & (master.S33Nm != "その他") & (master.MktNm != "TOKYO PRO MARKET")].Code)
    fins = load_fins(jq, prev_day)
    sh = shares(fins, prev_day)
    prev = pd.DataFrame({"code": bars.Code, "close_raw": pd.to_numeric(bars.C, errors="coerce")})
    prev["shares"] = prev.code.map(sh)
    prev["common"] = prev.code.isin(common)
    ranks = cap_ranks(prev)
    top = ranks[ranks.cap_rank <= rules.N_TOP]

    try:
        lst = SaxoReader().cfd_list()
        store.write_json(f"saxo_list/cfd_tyo_{now:%Y%m%d}.json", lst)
    except Exception as e:  # noqa: BLE001
        name = store.list_names("saxo_list/cfd_tyo_")[-1]
        print(f"Saxo の一覧を取れない ({type(e).__name__})。{name} を使う")
        lst = store.read_json(name)
    saxo = {code_of(x["symbol"]): x["uic"] for x in lst}

    rows = fins_rows(fins)
    rows = rows[rows.d <= pd.Timestamp(prev_day)]
    book = ForecastBook.from_rows(rows[rows.code.isin(set(top.code))])
    keep_from = pd.Timestamp(day - timedelta(days=120))
    book_out: dict[str, dict] = {}
    for (code, fy), v in book.v.items():
        if fy >= keep_from:
            book_out.setdefault(code, {})[fy.date().isoformat()] = v

    out = {
        "day": day.isoformat(), "prev_day": prev_day.isoformat(), "made_at": now.isoformat(timespec="seconds"),
        "calendar": [d.isoformat() for d in cal],
        "universe": {r.code: {"cap_rank": int(r.cap_rank), "close_raw": r.close_raw, "uic": saxo.get(r.code)} for r in top.itertuples()},
        "saxo": saxo,
        "book": book_out,
    }
    path = f"prep/{day.isoformat()}.json"
    store.write_json(path, out)
    n_saxo = sum(v["uic"] is not None for v in out["universe"].values())
    print(f"上位 {len(top)}社 (うち Saxo にある {n_saxo}社)、予想の帳簿 {len(book_out)}社、Saxo の CFD {len(saxo)}銘柄 → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
