"""J-Quants の一括ダウンロードで、全銘柄の日足 (bars)・銘柄の一覧 (master)・財務情報 (fins)・TOPIX の日足 (topix) を取る。
アドオンを契約しているときは、分足 (minute、量が多いので名前を指定したときだけ) と、
時刻つきの適時開示の一覧 (td、過去5年分を1つの CSV で) も取れる。

財務情報は、決算短信と業績予想・配当予想の修正の数字 (会計基準をまたいで同じ項目名) と、開示の時刻。

J-Quants 側のファイル (過去は月ごと、最近は日ごとの gzip の CSV) を、そのままの名前で data/jquants/ に置く。
手元に同じ大きさのファイルがあれば飛ばすので、毎日動かせば増えた分・書き換わった分だけ取る。
J-Quants 側の一覧から消えたファイル (月ごとのファイルに入った後の日ごとのファイルなど) は手元からも消す。

    uv run python scripts/collect_jquants.py              # 全部
    uv run python scripts/collect_jquants.py bars         # 日足だけ
    uv run python scripts/collect_jquants.py minute td    # アドオンの分 (分足・適時開示の一覧)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.jquants import BASE, bulk_list, bulk_url, load_api_key, make_session  # noqa: E402

ENDPOINTS = {
    "bars": "/equities/bars/daily",
    "master": "/equities/master",
    "fins": "/fins/summary",
    "topix": "/indices/bars/daily/topix",
}
ADDONS = {"minute": "/equities/bars/minute"}  # 分足・ティック アドオン。省略したときは取らない
TD = "td"  # TDnet/適時開示情報 アドオン (/td/bulk は別の仕組み)


def sync(api: PoliteSession, download: PoliteSession, endpoint: str, out: Path) -> tuple[int, int]:
    files = bulk_list(api, endpoint)
    wanted = {out / f["Key"] for f in files}
    got = 0
    for f in files:
        path = out / f["Key"]
        if path.exists() and path.stat().st_size == int(f["Size"]):
            continue
        r = download.get(bulk_url(api, f["Key"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(r.content)
        os.replace(tmp, path)
        got += 1
        print(f"  {f['Key']} {len(r.content) / 1e6:.1f}MB")
    stale = [p for p in (out / endpoint.strip("/")).rglob("*.csv.gz") if p not in wanted]
    for p in stale:
        p.unlink()
    return got, len(stale)


def sync_td(api: PoliteSession, download: PoliteSession, out: Path) -> int:
    """適時開示の一覧 (過去5年分の1つの CSV)。J-Quants 側の更新日時が変わっていれば取り直す。"""
    j = api.get(f"{BASE}/td/bulk").json()
    path, stamp = out / "td" / "td_bulk.csv.gz", out / "td" / "last_updated.txt"
    if path.exists() and stamp.exists() and stamp.read_text() == j["lastUpdated"]:
        return 0
    r = download.get(j["url"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(r.content)
    os.replace(tmp, path)
    stamp.write_text(j["lastUpdated"])
    print(f"  td_bulk.csv.gz {len(r.content) / 1e6:.1f}MB (更新 {j['lastUpdated']})")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kinds", nargs="*", help=f"取るもの {list(ENDPOINTS) + list(ADDONS) + [TD]} (省略で {list(ENDPOINTS)})")
    p.add_argument("--out", type=Path, default=Path("data/jquants"))
    p.add_argument("--interval", type=float, default=1.0)
    args = p.parse_args()
    if unknown := set(args.kinds) - set(ENDPOINTS) - set(ADDONS) - {TD}:
        p.error(f"知らない種類: {sorted(unknown)}")

    api = make_session(load_api_key(), args.interval)
    download = PoliteSession(min_interval=args.interval)  # ダウンロード先は別のサーバー。API キーは送らない
    for kind in args.kinds or ENDPOINTS:
        try:
            if kind == TD:
                print(f"{kind}: {sync_td(api, download, args.out)}ファイル取得")
                continue
            got, removed = sync(api, download, {**ENDPOINTS, **ADDONS}[kind], args.out)
        except Blocked as e:
            print(f"回数の上限か拒否なので止めます: {e}", file=sys.stderr)
            return 2
        print(f"{kind}: {got}ファイル取得、{removed}ファイル削除")
    print(f"API の読み込み {api.count}回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
