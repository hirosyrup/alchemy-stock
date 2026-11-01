# alchemy-stock

LLM が日本株の適時開示を読んで売買判断をする仕組みの研究。計画は [docs/PLAN.md](docs/PLAN.md)、経緯は [HANDOFF.md](HANDOFF.md)。

## 準備

```bash
uv sync
uv run pytest
```

## 開示の一覧を集める

取ったデータは `data/` に保存され、Git には入らない (公開しない)。どちらも1回の読み込みごとに3秒あけ、拒否されたら止まる。止まっても同じコマンドで続きから再開できる。

```bash
# TDnet: 直近31日分 (時刻つき)。毎日1回動かす
uv run python scripts/collect_tdnet.py

# 東証上場会社情報サービス: 全社の10年分 (日付のみ)
uv run python scripts/collect_jpx_listed.py companies      # 会社の一覧 (上場中 + 上場廃止)
uv run python scripts/collect_jpx_listed.py disclosures    # 会社ごとの開示の一覧 (約3時間半)
```

| 保存先 | 中身 |
|---|---|
| `data/tdnet/YYYY/YYYY-MM-DD.jsonl` | その日の開示 (時刻・コード・会社名・表題・PDF・XBRL) |
| `data/jpx_listed/companies.jsonl` | 会社の一覧 (市場・業種・決算期・上場廃止か) |
| `data/jpx_listed/disclosures/<code>.jsonl` | 会社ごとの開示 (日付・区分・表題・PDF・XBRL・HTML) |
| `data/jpx_listed/company_meta.jsonl` | 会社ごとの上場廃止日と開示の件数 |

2つのサイトの開示は、PDF のファイル名 (`doc_id`) が同じ番号なので突き合わせられる。

## ふるい分け

集めた一覧に、題名の決まり文句で種類を付け、ふるい分け (訂正・種類・普通の株か・同じ日の束ね) を当てる。落としたものも理由付きで全部残す。決まりは [src/alchemy_stock/screening.py](src/alchemy_stock/screening.py)、種類の一覧と件数は [docs/PLAN.md](docs/PLAN.md) §3。

```bash
uv run python scripts/screen_disclosures.py
```

| 保存先 | 中身 |
|---|---|
| `data/screened/disclosures.jsonl` | 開示ごとの種類・落とした理由 (`dropped_by`、通過は空)・束ね先 (`event_id`)・決まりの版 |
