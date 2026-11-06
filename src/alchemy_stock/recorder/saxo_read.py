"""Saxo の OpenAPI を読むだけの窓口 (docs/RECORDER_DESIGN.md §2)。

- トークンは alchemy-fx の Secret Manager の saxo-access-token (最新の版) を読むだけ。更新 (リフレッシュ) は一切しない。
  alchemy-fx の worker が 15分ごとに更新している。手元に置くのは最長 5分、401 が返ったら1回だけ読み直す
- GET 以外は送らない (注文を出せるトークンなので、この窓口では作らない)
- 回数の上限 (1セッションで、値段の窓口は1分に 120回、まとめて取る list は1分に 30回。FX と共有) のうち、こちらは全部で1分に 20回まで
- トークンの値は画面にもファイルにも出さない
"""
from __future__ import annotations

import os
import subprocess
import time
from collections import deque

import requests

BASE = "https://gateway.saxobank.com/openapi"
PROJECT, SECRET = "alchemy-fx", "saxo-access-token"
TOKEN_TTL = 300
MAX_PER_MIN = 20


def read_token() -> str:
    """Secret Manager から最新のアクセストークンを読む。Cloud Run ではライブラリ、手元では gcloud を使う。

    手元の gcloud の既定のアカウントが別のときは、環境変数 SAXO_GCLOUD_ACCOUNT で読むアカウントを指定する。
    """
    try:
        from google.cloud import secretmanager  # type: ignore

        c = secretmanager.SecretManagerServiceClient()
        return c.access_secret_version(name=f"projects/{PROJECT}/secrets/{SECRET}/versions/latest").payload.data.decode()
    except ImportError:
        pass
    env = dict(os.environ)
    if os.environ.get("SAXO_GCLOUD_ACCOUNT"):
        env["CLOUDSDK_CORE_ACCOUNT"] = os.environ["SAXO_GCLOUD_ACCOUNT"]
    out = subprocess.run(["gcloud", "secrets", "versions", "access", "latest", f"--secret={SECRET}", f"--project={PROJECT}"],
                         capture_output=True, text=True, env=env, timeout=30)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"Saxo のトークンを読めない: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


class SaxoReader:
    def __init__(self, token_fn=read_token, timeout: float = 5.0):
        self.token_fn = token_fn
        self.timeout = timeout
        self.session = requests.Session()
        self._token, self._at = "", 0.0
        self._calls: deque[float] = deque()
        self.last_ratelimit: dict[str, str] = {}

    def _token_now(self, force: bool = False) -> str:
        if force or not self._token or time.time() - self._at > TOKEN_TTL:
            self._token, self._at = self.token_fn(), time.time()
        return self._token

    def _throttle(self) -> None:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > 60:
            self._calls.popleft()
        if len(self._calls) >= MAX_PER_MIN:
            time.sleep(60 - (now - self._calls[0]) + 0.05)
        self._calls.append(time.monotonic())

    def get(self, path: str, **params) -> dict:
        for attempt in range(2):
            self._throttle()
            r = self.session.get(BASE + path, params=params, timeout=self.timeout,
                                 headers={"Authorization": f"Bearer {self._token_now(force=attempt > 0)}"})
            self.last_ratelimit = {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
            if r.status_code == 401 and attempt == 0:
                continue
            if r.status_code == 429:
                raise RuntimeError(f"Saxo の回数の上限 (429): {self.last_ratelimit}")
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Saxo: 401 が続いた (FX のトークン更新が止まっているかもしれない)")

    def quotes(self, uics: list[int], asset_type: str = "CfdOnStock") -> dict[int, dict]:
        """複数の銘柄の値段をまとめて1回で取る。Uic → {bid, ask, mid, last, price_type, market_state, delayed, updated}。"""
        if not uics:
            return {}
        j = self.get("/trade/v1/infoprices/list", AssetType=asset_type, Uics=",".join(map(str, uics)),
                     FieldGroups="Quote,PriceInfo,PriceInfoDetails,InstrumentPriceDetails")
        out = {}
        for d in j.get("Data", []):
            q, pi, pd_ = d.get("Quote", {}), d.get("PriceInfo", {}), d.get("PriceInfoDetails", {})
            out[d["Uic"]] = {
                "bid": q.get("Bid"), "ask": q.get("Ask"), "mid": q.get("Mid"),
                "last": pd_.get("LastTraded"), "open": pd_.get("Open"), "prev_close": pd_.get("LastClose"),
                "high": pi.get("High"), "low": pi.get("Low"),
                "price_type": q.get("PriceTypeBid"), "market_state": q.get("MarketState"),
                "delayed": q.get("DelayedByMinutes"), "updated": d.get("LastUpdated"),
            }
        return out

    def cfd_list(self) -> list[dict]:
        """東証の日本株 CFD の一覧 (Uic と 4桁の銘柄コード)。"""
        rows, skip = [], 0
        while True:
            j = self.get("/ref/v1/instruments", ExchangeId="TYO", AssetTypes="CfdOnStock", **{"$top": 1000, "$skip": skip})
            data = j.get("Data", [])
            rows += [{"uic": d["Identifier"], "symbol": d["Symbol"], "name": d.get("Description")} for d in data]
            if len(data) < 1000:
                return rows
            skip += 1000


def code_of(symbol: str) -> str:
    """Saxo の "7203:xtks" を J-Quants の 5桁の "72030" に直す。"""
    return symbol.split(":")[0] + "0"
