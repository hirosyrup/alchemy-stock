"""J-Quants API (JPX 公式、V2) から株価と銘柄の情報を取る (PLAN.md §5)。

- 認証は API キー。リポジトリ直下の .env の JQUANTS_API_KEY を読む (.env は Git に入らない)
- 回数の上限はプランごとに1分あたり 無料 5 / ライト 60 / スタンダード 120 回。
  PoliteSession の間隔で上限より遅く回し、429 が返ったら止める
- 量の多いものは一括ダウンロード (ライト以上) で取る。過去は月ごと、最近は日ごとの gzip の CSV で、
  /bulk/list で一覧、/bulk/get で15分だけ有効なダウンロード先を受け取る
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


def bulk_list(session: PoliteSession, endpoint: str) -> list[dict]:
    """一括ダウンロードできるファイルの一覧 (Key・Size・LastModified)。endpoint は "/equities/bars/daily" など。"""
    return get_all(session, "/bulk/list", endpoint=endpoint)


def bulk_url(session: PoliteSession, key: str) -> str:
    """ファイルのダウンロード先 (15分だけ有効)。"""
    return session.get(f"{BASE}/bulk/get", params={"key": key}).json()["url"]
