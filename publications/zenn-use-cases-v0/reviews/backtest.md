# バックテストの記事の作成と確かめ

- 担当: 記事の取り組みの7本目の担当（card ki-a57e9d88b594）
- 日時: 2026-10-17（JST）
- 確認版: `d1c67469d14d7ab3ee185f5161f6965092213e9d`（doeffのmain）の上に、この記事の変更を足した木
- 基準: 記事の書き方の決まり9項目と、[公開計画](../publication-plan.md)の各回の仕上げ基準・利用者の条件

## 書いたこと

1. 新しい記事`doeff-backtest.md`を書いた。題は「バックテストと本番で同じ処理を動かす — 時刻・値段・注文をハンドラで差し替える」。
2. 時刻(`GetTime`・`Delay`)・値段(`GetPrice`)・注文(`PlaceOrder`)・控え(`Tell`)を、処理からの依頼として書く。
3. 同じ`trading_loop`を、バックテストでは`sim_time_handler`と`backtest_market`、本番では`sync_time_handler`と`live_market`で動かす。
4. 見せ場は、値段のハンドラが仮想の時計に今を問い、今以前の記録だけを返す事。未来の値段を読めない事と、記録より前では止まる事をテストで確かめる。
5. 判断の処理`decide`は、時刻も値段も見ずに毎回同じ注文を返す置き物にした。値段はすべて架空の数。
6. 完全な例`examples/backtest.py`に、本文の5枠と同じ定義と、本番のハンドラを偽物のクライアントで試すテストを置いた。

## 書き方の決まり9項目の照らし

| 項目 | 決まり | この記事 | 確かめ方 |
|---|---|---|---|
| 長さ | 本文3,000〜6,000字 | 3,364字 | `render_release.py`の数え方 |
| code | 6枠まで | 5枠 | 同上 |
| 図 | 2〜4枚・冒頭に全体の図 | 2枚・最初の枠の直後に全体の図 | 同上と`reviews/backtest-check.py`の位置の検査 |
| 言葉 | 初見の技術者が読める言葉 | エフェクト・ハンドラ・`Program`・`Tell`を記事の中で説明。内部の呼び名は使わない | 読み直し |
| 口調 | です・ます・1文60字まで | 60字を越える文0・です・ます以外0 | `render_release.py`の数え方 |
| 形 | 悩み→短いcode→全体の図→本文→まとめ→参考 | その順 | 読み直しと位置の検査 |
| 数字 | 測った日と範囲 | 2026年10月17日・同じ機械で3回・1週間分3.1〜3.8秒・30日分約17秒・本番の枠約60秒 | 下の「検証」 |
| 秘密 | 鍵・token・個人の情報・社内の機械の名を載せない | 0件 | `reviews/backtest-check.py`の語の検査とgrep |
| 公開 | `published: false` | `published: false` | front matter |

## 書かない事の確かめ

利用者の条件「実際に試した戦略・損益・相場を記録する仕組み・機械の構成に触れない」を、2つの方法で確かめた。

- 語の検査: `reviews/backtest-check.py`が、本文を次の語で探し、0件である事を確かめる。損益・利益・損失・儲・勝率・勝ち・負け・残高・口座・PnL・profit・loss・balance・sharpe・戦略・strategy・移動平均・RSI・MACD・ボリンジャー・約定履歴・板情報・ティック・tick・websocket・記録する仕組み・収集。社内の名(agora・zeus・atlas・cisco・doeff-cluster・機体・/home/)と鍵の形も探す。
- grep: 変えた13のfile（PNGを除く）を同じ語と秘密の形で探した。当たりは、既存の他の記事の台帳の語と、利用者の条件を写した文だけだった。
- 読み直し: 判断は`decide`の置き物だけで、値段は100.0・101.0・102.0・999.0の架空の数。取引所への接続は`Broker`の2つの操作の形だけで、実際の取引所・口座・機械の名は無い。

## 検証

2026-10-17（JST）に、上の確認版の木で走らせた。

| 命令 | 結果 |
|---|---|
| `uv run --no-sync python publications/zenn-use-cases-v0/reviews/backtest-check.py --real-time` | 5枠とも成功。2枠目の表示は本文の注釈と同じ。test関数3つが通った。本番の枠は偽物のクライアントで`[('17:38', 100.0, '受付A')]`・60.0秒 |
| `uv run --no-sync python publications/zenn-use-cases-v0/examples/backtest.py` | 成功（test関数5つ） |
| `uv run --no-sync python publications/zenn-use-cases-v0/render_release.py doeff-backtest.md --output /tmp/doeff-backtest.release.md` | 本文3,364字・枠5・画像2・60字を越える文0・です・ます以外0・記事間の相対リンク0 |
| `uv run --no-sync python publications/zenn-use-cases-v0/verify_comments.py` | 成功 |
| `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/backtest.py publications/zenn-use-cases-v0/reviews/backtest-check.py publications/zenn-use-cases-v0/verify_examples.py` | 成功 |
| 1週間分のテストを3回・30日分を1回、`time.perf_counter`で測った | 1週間分は実時間3.09・3.40・3.81秒（CPU 2.31〜2.34秒）。30日分（43,200回）は実時間16.52秒（CPU 10.27秒） |

rebase の後(main `c32e9d16d` の上)でも、`backtest-check.py`・完全な例・`verify_comments.py`・ruff が成功した。`verify_publication.py` は main と同じ画像の検査(49行目)で止まる。

公開計画の7本目の行を「これから書く」から原稿へのリンクに変えた（公開状態は下書きのまま）。その前後で、週1〜4・6・9本目と7本目の公開する本文が同じである事を`render_release.release_text`で確かめた。

## 検証範囲と残り

- 実際の取引所・非同期のSDK・実際の注文は使っていない。本番の枠は、固定の値を返す偽物のクライアントでだけ動かした。
- 公開パッケージ（PyPI）の版で動くかは確かめていない。
- `verify_publication.py`の記事の数を25に上げた(8本目が24にしていた)。メインの記事へのリンクの検査は、メインを変えていないので7本目で通らない。ただし他の記事の図が揃っていないため、手前の画像の検査で止まる(main と同じ所)。
