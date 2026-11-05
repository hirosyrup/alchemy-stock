# Interactive Brokers (IBKR) を使う場合のまとめ

作成: 2026-11-04。まだ口座は作っていない。**「確認済み」と書いたもの以外は、調べた範囲の見込み** で、口座を作る前・使う前に公式で確かめる。

## なぜ IBKR か

- 夜間 PTS (ジャパンネクスト) に、プログラムから注文を出せる見込みがあるのが、今のところ IBKR だけ
  - 三菱UFJ eスマート証券の kabuステーション API は、注文で選べる市場が 東証・名証・福証・札証・SOR・東証+ だけで、夜間 PTS は不可 (確認済み、2026-11-04、仕様書)。SOR は昼間に PTS へ回ることがあるだけ
  - SBI・楽天・松井は夜間 PTS を扱うが、個人向けの注文の API が無い
  - 立花証券 e支店は 2026-10-23 から API が停止中 (docs/PLAN.md)
- 使いたいロジック: 引け後の開示や、海外の提携先の夜の発表を読み、夜間 PTS で翌朝の始値より前に入る ([research/LOGIC_CANDIDATES.md](../research/LOGIC_CANDIDATES.md) の 12・15)。
  取引時間中のロジック (10・11) にも使える
- 夜間 PTS の過去データは手に入らない (J-Quants は東証だけ。ジャパンネクストや ICE は業者向け)。取れる分は、少額で動かしながら確かめることになる

## 口座

- 日本に住む個人は、IBKR の日本の子会社 **インタラクティブ・ブローカーズ証券 (IBSJ)** で **個人の口座** を作る (法人口座ではない)。ネットで申し込み、本人確認の書類が要る
- 日本株の取引の許可と、PTS (ジャパンネクスト) の取引の許可は、口座を作った後の設定でオンにする必要がある見込み
- 入金の最低額があるかは未確認
- 練習用の口座 (ペーパー取引) は本物の口座と一緒に使えるのが普通。夜間 PTS がペーパーで再現されるかは不明

## 夜間 PTS の取引 (IBSJ の取引ルールの原文で確認済み、2026-11-05、ユーザーが保存したもの)

- ジャパンネクストの夜間: 17:00〜翌 06:00 (IBSJ の場合。証券会社によって時間が違う)
- 原文: 「Orders can only be placed via TWS, not via the Client Portal or the IBKR Mobile App.」
  → 除かれているのはブラウザ版とスマホだけ。TWS API は TWS (か同じ中身の IB Gateway) を通すので **出せる見込みが高い**。ただし API について明記は無く、未確認
- 原文: 宛先を「JapanNEXT Direct (direct-route)」にして、「Allow this order to be filled outside of RTH」にチェック → API では `contract.exchange` = ジャパンネクストのコード、`order.outsideRth = True` に当たる
- 夜間の注文の種類・信用・空売りについては書かれていない
- 手数料 (階段制、ジャパンネクストの夜間): 売買代金の 0.0044% と、約定ごとに 3.245円 + 売買代金の 0.00044% の清算手数料 (IBSJ の手数料の PDF、2021-03 版)

## 現物の口座の決まり (取引ルールの原文で確認済み、2026-11-05)

- 同じ銘柄を、同じ日に同じお金で「買う → 売る → 買う」はできない (差金決済の禁止)
- 違う銘柄を同じお金で回す「A を買う → A を売る → B を買う → B を売る …」は **できる** (原文の例あり)。1日に何件も回すロジックは現物の口座でも組める

## 手数料 (IBSJ の手数料の PDF、2021-03 版。今と違う可能性あり)

- 日本株 (月の売買代金 1.5億円まで): 固定制 0.088% / 最低 88円、階段制 0.055% / 最低 88円 (1回の注文ごと)。階段制はこれに市場・清算の手数料が少し乗る
- 10万円の売買なら片道 88円 = 0.088%、往復 約0.18%

## API (2種類)

| | TWS API (使う方) | Web API |
|---|---|---|
| つなぎ方 | 手元で TWS か IB Gateway を動かし、そこにプログラムからつなぐ (TCP) | インターネット越し (REST、WebSocket) |
| 土台 | デスクトップの TWS と同じ | ブラウザ版の Client Portal と同じ |
| 夜間 PTS | **出せる見込みが高い** (取引ルールで除かれているのはブラウザ版とスマホだけ)。未確認 | 夜間は TWS からだけという取引ルールから、**たぶん不可** |
| 言語 | Python・Java・C++・C# など | 何でも (HTTP) |

### TWS API で使う関数と項目

1. **宛先として選べる市場を調べる**: `reqContractDetails(reqId, contract)`
   - `contract`: `symbol` = 銘柄コード (例 "7203")、`secType` = "STK"、`currency` = "JPY"、`exchange` = "SMART"、`primaryExchange` = 東証のコード (IBKR では "TSEJ" と記憶、要確認)
   - 返ってくる `ContractDetails` の **`validExchanges`** にジャパンネクストのコードが入っているかを見る。**`tradingHours`・`liquidHours`** で夜間の時間帯が載っているかも見る
2. **注文を出す**: `placeOrder(orderId, contract, order)`
   - `contract.exchange` = 1 で見つけたジャパンネクストのコード (直接指定)
   - `order.outsideRth = True`: 「通常の取引時間の外でも注文が発動・約定できる」(確認済み、旧版・新版のリファレンス)
   - `order.includeOvernight`: 「夜間の取引を含むか。初期値 false」(新版のリファレンスにある)。米国株の夜間取引向けの可能性が高く、ジャパンネクストの夜間に要るかは不明
   - `order.orderType = "LMT"`、`order.tif` = "DAY" / "GTC" / "GTD" など。夜間の DAY がいつまで有効かは不明
3. **値段を見る**: `reqMktData(reqId, contract, ...)`。宛先をジャパンネクストにすれば PTS の気配・約定が取れるかもしれない (市場のデータの契約が要る)。夜間 PTS の値段を自分でためる方法にもなる

参考 (Web API で同じことをする場合): 銘柄の検索 `GET /iserver/secdef/search`、宛先の確認 `GET /iserver/contract/{conid}/info` (`validExchanges`)、注文 `POST /iserver/account/{accountId}/orders` (`outsideRTH`)

### ドキュメント

- 新版 (ibkrcampus.com、interactivebrokers.com/docs) は自動の閲覧を止める設定で、Claude からは読めない (403)。ブラウザで保存したものを渡せば読める
- 旧版 (interactivebrokers.github.io/tws-api) は読める。日本株の例は無い (米国株だけ)

## 値段のデータ

- IBSJ の市場データの料金のページ (2026-11-05 保存) では、個人 (Non-Professional) の月額は **ジャパンネクスト L1+L2 で 110円、東証 L1 で 330円**。
  保存したページの表が崩れていて列の読み取りに少し不確かさがある。手数料の PDF (2021-03 版) では、ジャパンネクスト L1 は無料、L2 は 110円
- 口座の資産が 500ドル相当より少ないと、市場データは止められる
- **過去のデータ (ジャパンネクストの過去の値段) が取れるかは、どちらの資料にも書いていない**。口座を作ってから `reqHistoricalData` で試すか、サポートに聞く
- 注文を出すだけなら、契約なしでも出せることが多い

## 動かし方

- **IB Gateway (画面の無い API 専用のソフト) を動かし続ける**。プログラムはそこにつなぐ
- 起動とログインに数十秒〜1分かかる。本番の口座は2段階認証が必須で、起動のたびに認証が要る
  → **必要なときだけ起動する形 (Cloud Run など) は成り立たない**。注文の窓口だけは動かし続ける
- 毎日の再起動の設定をすれば、ログインの状態を保ったまま 1週間ほど動く見込み。**週に1回は2段階認証 (IBKR のスマホアプリの通知を押す) が要る**
- **IBC** (無料の道具) で、ログイン画面の入力と再起動を自動にできる。IB Gateway と IBC をまとめた Docker の仕組みもある
- 形: 注文の窓口 (IB Gateway + 注文を受けて出す小さいプログラム) だけ小さいサーバーで動かし続け、見張り・LLM の判断・記録は Cloud Run で必要なときだけ動かして、窓口に注文を頼む

### 置き場所

| 置き場所 | 月額 | メモ |
|---|---|---|
| Google Cloud の無料枠 (e2-micro) | 0円 | 米国 (オレゴン・アイオワ・サウスカロライナ) だけ。メモリ 1GB で IB Gateway にはぎりぎり (Java のメモリを絞る)。日本まで往復 0.1〜0.2秒余計。夜間 PTS (分の余裕) なら問題ない |
| Google Cloud 東京の e2-micro / e2-small | 約1,000〜1,500円 / 約2,000〜2,500円 | 取引時間中の秒の勝負もするならこちら |
| Oracle Cloud の無料枠 | 0円 | 東京に置ける。大きいのは ARM の CPU で、IB Gateway が動くか要確認 |

まず試すだけなら Google Cloud の無料枠 (米国) で足りる。

## 確かめること (口座を作る前・作った後)

1. **TWS API から、ジャパンネクストの夜間に注文を出せるか** (IBKR のサポートに聞くのが早い)
2. ジャパンネクストの宛先のコード (`validExchanges` で見る)、`includeOvernight` が要るか、夜間の `tif` の扱い
3. 日本株・PTS の取引の許可の出し方、夜間に信用・空売りができるか
4. 入金の最低額、市場のデータの月額
5. 週1回の2段階認証より手間が増えないか (IB Gateway の自動再起動、IBC)
6. 夜間 PTS の板の厚さ (小さい会社でどれくらいの金額まで約定するか)
7. PTS の約定の仕組み (ジャパンネクストの取引説明書で確かめる): 見込みは次のとおり
   - 東証のような寄り付きの板寄せは無く、時間が始まった瞬間から注文が来るたびに約定する (「始値」はその時間の最初の約定にすぎない)
   - 約定できる値段は東証の値幅制限の中 (夜間はその日の東証の終値をもとにした幅)。夜の開示がストップ高級でも、PTS でもその上限まで
   - 東証より細かい刻みで売買できる市場 (X-Market) がある
   - 夜間が始まった直後 (17:00) は注文がまばらで、値段が飛びやすいと思われる。手放す側は翌朝の東証の始値 (板寄せ) なので、ずれにくい

## 参考

- IBSJ 取引ルール: https://www.interactivebrokers.co.jp/en/trading/trading-rules.php
- IBSJ 手数料: https://www.interactivebrokers.com/download/ibsj/EN-Fees_Disclosures.pdf
- IBKR JapanNext の手数料: https://www.interactivebrokers.co.uk/en/accounts/fees/JapanNext-StkFees.php
- TWS API (旧版): https://interactivebrokers.github.io/tws-api/
- TWS API (新版): https://www.interactivebrokers.com/docs/tws-api/ref/introduction 、Order: https://ibkrcampus.com/docs/tws-api/ref/order.md
- IBKR API の案内: https://www.interactivebrokers.co.jp/jp/trading/ib-api.php
