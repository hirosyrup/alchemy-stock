"""記録係のルール (src/alchemy_stock/recorder/rules.py) を過去の日付で1日ずつ回し、検証のコードと同じ取引になるかを確かめる。

    uv run python scripts/check_recorder_rules.py

data/explore/stage4_vault.duckdb (2024-06〜2026-10) を、検証のコード (run_stage4.py・run_stage3.py の金庫の設定) と同じように読む。
見るのは取引の一覧 (会社・入る日・出る日) だけで、損益は計算しない。

- 「検証と同じ条件」で回したときは、検証のコードと1件残らず一致するはず
- 「本番の条件」で回したときの差 (調整前の終値で 200円を見る、S16 の期限切れ) と、
  54 で開示がその日の日付しかない件数 (寄った後の開示で判定されていた可能性があるもの) を数える
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["STAGE4_VAULT"] = "1"
os.environ["STAGE3_VAULT"] = "1"
os.chdir(ROOT)

import run_stage3 as r3  # noqa: E402
import run_stage4 as r4  # noqa: E402
from alchemy_stock.recorder import rules  # noqa: E402


def replay_54(close_col: str) -> pd.DataFrame:
    dates, codes = r4.dates, list(r4.codes)
    common = [c in r4.common for c in codes]
    disc = r4.dd.rename(columns={"D": "date"}).assign(time=None)
    gap = (r4.O / r4.C.shift(1) - 1).values  # 調整済みの値段 = 調整前の値段を分割の比率で直したもの (rules.gap_54 と同じ)
    jx = {c: j for j, c in enumerate(codes)}
    book, rows = rules.Book(), []
    for i in range(1, len(dates)):
        if i + rules.HOLD_54 - 1 >= len(dates):  # 出る日がデータの外
            break
        prev = pd.DataFrame({"code": codes, "close_raw": r4.CR.iloc[i - 1].values, "close_adj": r4.C.iloc[i - 1].values,
                             "shares": r4.SH.iloc[i - 1].values, "common": common})
        uni = rules.universe_54(prev, close_col=close_col)
        hit = rules.disclosed_54(disc, dates[i - 1], dates[i])
        early = rules.disclosed_54(disc, dates[i - 1], dates[i] - pd.Timedelta(days=1))
        for code in sorted(set(uni.code) & hit):
            g = gap[i, jx[code]]
            if not (g == g and rules.is_signal_54(g)) or book.blocked(code, dates[i]):
                continue
            dx = dates[i + rules.HOLD_54 - 1]
            book.hold(code, dx)
            rows.append((code, dates[i], dx, code not in early))
    return pd.DataFrame(rows, columns=["code", "de", "dx", "today_only"])


def replay_s16(timeout: bool) -> pd.DataFrame:
    cal = list(r3.dates)
    dis = r3.dis.assign(kind=[rules.s16_kind(t) for t in r3.dis.title]).dropna(subset=["kind"])
    ann = dis[dis.kind == "発表"][["code", "date"]].drop_duplicates()
    ends = {code: list(zip(g.date, g.kind)) for code, g in dis[dis.kind != "発表"].groupby("code")}
    rows = []
    for r in ann.itertuples():
        end = rules.s16_end(r.date, ends.get(r.code, []))
        if end is None and not timeout:
            continue
        stop = end[0] if end else rules.s16_timeout_day(r.date)
        de, dx = rules.next_trading_day(cal, r.date), rules.next_trading_day(cal, stop)
        if de is None or dx is None or dx < de or r.code not in r3.codes:
            continue
        rows.append(dict(code=r.code, D=r.date, de=de, dx=dx, timeout=end is None))
    t = r3.info(pd.DataFrame(rows))  # 発表の日より前の最新の市場・業種 (検証と同じ)
    t = t[(t.MktNm != "TOKYO PRO MARKET") & t.S33Nm.notna() & (t.S33Nm != "その他")]
    book, keep = rules.Book(), []
    for r in t.sort_values(["de", "code"]).itertuples():
        if book.blocked(r.code, r.de):
            continue
        book.hold(r.code, r.dx)
        keep.append(r.Index)
    return t.loc[keep, ["code", "de", "dx", "timeout"]].sort_values(["de", "code"]).reset_index(drop=True)


def compare(name: str, ref: pd.DataFrame, mine: pd.DataFrame) -> None:
    a = set(zip(ref.code, ref.de, ref.dx))
    b = set(zip(mine.code, mine.de, mine.dx))
    print(f"- {name}: 検証のコード {len(a)}件 / 記録係 {len(b)}件、一致 {len(a & b)}件、検証だけ {len(a - b)}件、記録係だけ {len(b - a)}件")
    for x in sorted(a - b)[:5]:
        print(f"  - 検証だけ: {x[0]} {x[1]:%Y-%m-%d}〜{x[2]:%Y-%m-%d}")
    for x in sorted(b - a)[:5]:
        print(f"  - 記録係だけ: {x[0]} {x[1]:%Y-%m-%d}〜{x[2]:%Y-%m-%d}")


if __name__ == "__main__":
    print(f"# 記録係のルールの突き合わせ ({r4.dates[0]:%Y-%m-%d}〜{r4.dates[-1]:%Y-%m-%d})\n")

    print("## 54\n")
    ref = r4.to_frame(r4.gap_trades(54))
    same = replay_54("close_adj")
    live = replay_54("close_raw")
    compare("検証と同じ条件 (調整済みの終値で 200円)", ref, same)
    compare("本番の条件 (調整前の終値で 200円)", ref, live)
    n = int(live.today_only.sum())
    print(f"- 本番の条件の {len(live)}件のうち、開示がその日の日付しかないもの {n}件 ({n / len(live):.0%})。"
          "寄った後の開示で判定されていた可能性がある (時刻つきの一覧は 2026-10 以降しか無いので、上限として数える)")

    print("\n## S16\n")
    ref = r3.clean(r3.t_s16())
    compare("検証と同じ条件 (実施・中止が出たものだけ)", ref, replay_s16(timeout=False))
    live = replay_s16(timeout=True)
    compare("本番の条件 (出なければ 30暦日で買い戻す)", ref, live)
    print(f"- 本番の条件で 30暦日の期限切れになったもの {int(live.timeout.sum())}件")
