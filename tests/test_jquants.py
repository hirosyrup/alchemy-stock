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
        self.calls.append(params)
        if "pagination_key" not in params:
            return FakeResponse({"data": [{"Code": "11110"}], "pagination_key": "k1"})
        return FakeResponse({"data": [{"Code": "22220"}]})


def test_get_all_follows_pagination_key():
    session = FakeSession()
    rows = jquants.get_all(session, "/equities/bars/daily", date="2025-06-02")
    assert [r["Code"] for r in rows] == ["11110", "22220"]
    assert session.calls == [{"date": "2025-06-02"}, {"date": "2025-06-02", "pagination_key": "k1"}]


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
