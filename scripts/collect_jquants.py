"""J-Quants の一括ダウンロードで、全銘柄の日足 (bars) と銘柄の一覧 (master) を取る。

J-Quants 側のファイル (過去は月ごと、最近は日ごとの gzip の CSV) を、そのままの名前で data/jquants/ に置く。
手元に同じ大きさのファイルがあれば飛ばすので、毎日動かせば増えた分・書き換わった分だけ取る。
J-Quants 側の一覧から消えたファイル (月ごとのファイルに入った後の日ごとのファイルなど) は手元からも消す。

    uv run python scripts/collect_jquants.py              # 両方
    uv run python scripts/collect_jquants.py bars         # 日足だけ
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.jquants import bulk_list, bulk_url, load_api_key, make_session  # noqa: E402

ENDPOINTS = {"bars": "/equities/bars/daily", "master": "/equities/master"}


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kinds", nargs="*", help=f"取るもの {list(ENDPOINTS)} (省略で全部)")
    p.add_argument("--out", type=Path, default=Path("data/jquants"))
    p.add_argument("--interval", type=float, default=1.0)
    args = p.parse_args()
    if unknown := set(args.kinds) - set(ENDPOINTS):
        p.error(f"知らない種類: {sorted(unknown)}")

    api = make_session(load_api_key(), args.interval)
    download = PoliteSession(min_interval=args.interval)  # ダウンロード先は別のサーバー。API キーは送らない
    for kind in args.kinds or ENDPOINTS:
        try:
            got, removed = sync(api, download, ENDPOINTS[kind], args.out)
        except Blocked as e:
            print(f"回数の上限か拒否なので止めます: {e}", file=sys.stderr)
            return 2
        print(f"{kind}: {got}ファイル取得、{removed}ファイル削除")
    print(f"API の読み込み {api.count}回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
