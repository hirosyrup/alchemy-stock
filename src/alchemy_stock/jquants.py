"""J-Quants API (JPX 公式、V2) から株価と銘柄の情報を取る (PLAN.md §5)。

- 認証は API キー。リポジトリ直下の .env の JQUANTS_API_KEY を読む (.env は Git に入らない)
- 回数の上限はプランごとに1分あたり 無料 5 / ライト 60 / スタンダード 120 回。
  PoliteSession の間隔で上限より遅く回し、429 が返ったら止める
- 1回で返りきらないときは pagination_key が付くので、それを付けて続きを取る
- 取ったデータは自分だけで使う。公開リポジトリや人に見せるページに載せない (規約)
"""
from __future__ import annotations

from pathlib import Path

from .http import PoliteSession

BASE = "https://api.jquants.com/v2"


def load_api_key(env: Path = Path(".env")) -> str:
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("JQUANTS_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError(f"{env} に JQUANTS_API_KEY がありません")


def make_session(api_key: str, min_interval: float) -> PoliteSession:
    session = PoliteSession(min_interval=min_interval)
    session.session.headers["x-api-key"] = api_key
    return session


def get_all(session: PoliteSession, path: str, **params) -> list[dict]:
    """path の結果を、pagination_key をたどって最後まで集める。"""
    rows: list[dict] = []
    while True:
        j = session.get(f"{BASE}{path}", params=params).json()
        rows += j.get("data", [])
        if not j.get("pagination_key"):
            return rows
        params = {**params, "pagination_key": j["pagination_key"]}


def daily_bars(session: PoliteSession, day: str) -> list[dict]:
    """その日に売買のあった全銘柄の日足 (上場廃止した銘柄も含む)。休みの日は空。"""
    return get_all(session, "/equities/bars/daily", date=day)


def master(session: PoliteSession, day: str) -> list[dict]:
    """その日時点の全銘柄の一覧 (市場・業種・規模・貸借の区分)。"""
    return get_all(session, "/equities/master", date=day)
