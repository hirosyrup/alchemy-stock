"""相手のサイトに負荷をかけないための HTTP クライアント。

- 1回の読み込みごとに最低 min_interval 秒あける
- 一時的なエラー (5xx・タイムアウト) は少し待って数回だけやり直す
- 拒否 (403・429) されたら Blocked を投げて、呼び出し側で全体を止める
"""
from __future__ import annotations

import time

import requests

USER_AGENT = "Mozilla/5.0 (Macintosh) alchemy-stock-research/0.1 (personal, low-rate)"


class Blocked(RuntimeError):
    """相手に拒否された。続けずに止める。"""


class PoliteSession:
    def __init__(self, min_interval: float = 3.0, max_retries: int = 3, timeout: float = 30.0):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last = 0.0
        self.count = 0

    def _wait(self) -> None:
        delay = self._last + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def request(self, method: str, url: str, allow_404: bool = False, **kwargs) -> requests.Response | None:
        for attempt in range(self.max_retries + 1):
            self._wait()
            self.count += 1
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException:
                if attempt == self.max_retries:
                    raise
                time.sleep(self.min_interval * 2 ** (attempt + 1))
                continue
            if r.status_code in (403, 429):
                raise Blocked(f"HTTP {r.status_code} {method} {url}")
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code >= 500 and attempt < self.max_retries:
                time.sleep(self.min_interval * 2 ** (attempt + 1))
                continue
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = "utf-8"
            return r
        raise RuntimeError("unreachable")

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)
