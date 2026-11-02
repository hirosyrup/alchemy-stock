"""J-Quants の取り方と、gzip の保存のテスト。実際の株価は使わない (規約上、公開リポジトリに載せない)。"""
from alchemy_stock import jquants
from alchemy_stock.store import read_jsonl, write_jsonl


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class FakeSession:
    """1回目は続きの鍵つき、2回目で終わる返事を返す。"""

    def __init__(self):
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        if url.endswith("/bulk/get"):
            return FakeResponse({"url": "https://example.com/x.csv.gz"})
        if "pagination_key" not in params:
            return FakeResponse({"data": [{"Key": "a.csv.gz"}], "pagination_key": "k1"})
        return FakeResponse({"data": [{"Key": "b.csv.gz"}]})


def test_bulk_list_follows_pagination_key():
    session = FakeSession()
    rows = jquants.bulk_list(session, "/equities/bars/daily")
    assert [r["Key"] for r in rows] == ["a.csv.gz", "b.csv.gz"]
    assert [p for _, p in session.calls] == [
        {"endpoint": "/equities/bars/daily"},
        {"endpoint": "/equities/bars/daily", "pagination_key": "k1"},
    ]


def test_bulk_url():
    session = FakeSession()
    assert jquants.bulk_url(session, "a.csv.gz") == "https://example.com/x.csv.gz"
    assert session.calls == [(f"{jquants.BASE}/bulk/get", {"key": "a.csv.gz"})]


def test_load_api_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nJQUANTS_API_KEY="abc"\n', encoding="utf-8")
    assert jquants.load_api_key(env) == "abc"


def test_jsonl_gz_roundtrip(tmp_path):
    path = tmp_path / "x" / "2025-06-02.jsonl.gz"
    rows = [{"Code": "11110", "名前": "テスト"}]
    write_jsonl(path, rows)
    assert path.read_bytes()[:2] == b"\x1f\x8b"  # gzip になっている
    assert read_jsonl(path) == rows
    assert not list(path.parent.glob("*.tmp"))
