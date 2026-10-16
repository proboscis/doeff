# HTTP取得・応答再利用の記事レビュー

## 担当と確認対象

- 担当: `/root/review_replay`
- 日付: 2026-09-16
- 記事: `publications/zenn-use-cases-v0/doeff-replay.md`
- 実行例: `publications/zenn-use-cases-v0/examples/http_replay.py`
- 画像指定: `publications/zenn-use-cases-v0/reviews/replay-visuals.json`
- 適用スキル: `doeff-patterns`、`doeff-runtime`

## 指摘と修正

旧例は`http_fixture_handler(..., client_factory=...)`へ利用者が`httpx.AsyncClient`と`MockTransport`を用意しており、「依頼の解釈をハンドラで交換する」説明の中心がHTTPクライアントの構築へずれていた。

HTTPの取得担当とMemoによる保存担当を分離し、次の構成へ書き換えた。

1. `fetch_text`が`yield HttpRequest(...)`で依頼する。
2. `make_memo_rewriter(HttpRequest)`が保存を照会する。
3. `sqlite_memo_handler(database)`がMemo系の依頼を処理する。
4. 未保存時のみHTTP依頼を外側へ出し直す。
5. HTTP取得担当を、固定応答の`fixed_http`または明示的に拒否する`reject_http`へ交換する。
6. 応答を取得できた場合はMemoへ保存し、元の処理を再開する。

HTTPクライアント、`client_factory`、`MockTransport`を実行例から除去した。`fixed_http`は`@handler`と`@do`で定義し、正式な公開応答型`HttpResponse`を`Resume`で返す。HTTP以外は`Pass`で外側へ委譲する。ループを`async def`へ逃がす構造や架空のAPIは使っていない。

本文は、HTTPの型全体が選択対象になること、GET以外の再利用可否は自動判定されないこと、400/500も応答値として返れば保存されること、保存層が未設置の場合は現行実装が取得へ進むこと、別runの検証とプロセス停止・復旧の検証の違いを明記した。

既存の`http_fixture_handler`は内部で取得ハンドラを組み立てる公開APIとして補足した。今回、製品コードや既存APIは変更していない。

## 実装根拠

- `packages/doeff-core-effects/doeff_core_effects/http_effects.hy`: `HttpRequest`の引数、`HttpResponse`のフィールド、`raise_for_status()`。
- `packages/doeff-core-effects/doeff_core_effects/_http_handlers_impl.hy`: 標準HTTPハンドラは`httpx.Response`を`HttpResponse`へ変換する。fixtureのrecord構成は取得ハンドラも内部で生成する。
- `packages/doeff-core-effects/doeff_core_effects/memo_handlers.py`: `make_memo_rewriter`のMemoExists/Get、hitでResume、missで`yield effect`、応答後にMemoPutという順序。保存層未設置時の動作。
- `packages/doeff-core-effects/doeff_core_effects/_memo_handlers_impl.hy`: 保存層のexists/get/put、外側への委譲、最外側での保存ミス。
- `packages/doeff-core-effects/doeff_core_effects/storage/sqlite.py`: SQLite永続保存、pickleシリアライズ、I/Oを`Await`で待つ実装。
- `tests/effects/test_memo_rewriter_no_terminal.py`: 保存層との合成と、保存層がない場合の取得動作。
- `tests/effects/test_memo_rewriter_compute_unhandled.py`: 取得担当不在による未処理依頼を隠さないこと。

## 検証

実行コマンド:

```sh
uv run --no-sync python publications/zenn-use-cases-v0/examples/http_replay.py # HTTP/Memo合成をオフライン検証し、最後にOKを表示する。
uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/http_replay.py # 実行例が規約に沿うことを確認する。
uv run --no-sync pytest tests/effects/test_memo_rewriter_no_terminal.py tests/effects/test_memo_rewriter_compute_unhandled.py -q # 参照したメモ化の契約3件を確認する。
```

- 実行例: 成功。
- 初回取得一回、別のProgram・SQLiteハンドラ・`run`での保存値再利用を確認。
- 二回目はHTTPへ到達すると必ず失敗する`reject_http`を設置したまま成功。HTTP取得担当が呼ばれていないことを確認。
- 未保存URLは`LookupError`になり、例外に対象URLが保持されることを確認。
- 同じ未保存URLでも取得担当を`fixed_http`へ交換すると成功し、取得回数が一回だけ増えることを確認。
- 想定した未保存エラーのdoeffトレースが出力されるが、`pytest.raises`で内容を検査し、プロセスの終了コードは0。
- Ruff: 全項目成功。
- 既存の契約テスト: 3 passed。対象を絞った実行のため、収集していないADRファイルについての既存警告が1件出る。
- 記事内Python 3ブロック: ASTによる構文検査と、各実質行に日本語コメントがあることを確認。
- 各行コメントは目的と期待値・期待動作を記載。空行と説明済みの閉じ括弧を除き、実行例にも対応するコメントを付けた。

## 実行していない範囲

本番HTTP構成は説明のみ。外部HTTP、LLM、agent、Docker、クラウド接続は実行していない。ここで実証した永続値再利用は、同じプロセス内での別`run`であり、kill→resumeはこの担当範囲に含まない。

画像の実生成・差し替えは親担当へ依頼済み。JSONには平面ダイアグラムのノード・条件分岐・矢印、正確な3行のコードと行コメントを指定した。本文のaltとcaptionはそのJSONに合わせて更新している。

## 連載6本目「外部APIのテストを、通信せずに回す」への仕上げ（2026-10-17）

- 担当: 記事の取り組みの依頼を受けた会話（Claude Opus 5.5）。
- 対象: `doeff-replay.md`、`examples/http_replay.py`、`reviews/replay-check.py`、図2枚。

### 直したこと

1. 冒頭を「外部APIのテストが遅い・落ちる・鍵が要る」の悩み3つから始め、悩み → 短いコード → 全体の図 → 本文 → まとめ → 参考の形に組み直した。
2. 最初のコードを、自作の最小ハンドラ`fake_http`（`@handler`・`@do`・`Resume`・`Pass`）で通信せずに終わるテストにした。
3. 記録と再生を`run_recorded`（記録の層・SQLite・取得担当の3層）にまとめ、記録→再生→記録に無いURLの失敗を1つのテストにした。
4. 本番の取得担当`http_production_handler()`の枠を足し、同じ機械の中に立てたHTTPサーバーで実際に通信して記録し、止めた後に再生できることを確かめた。
5. 既定の鍵`content_address`がヘッダのAPIキーも鍵に入れることを、コードと確かめで示した。`key_fn`でメソッドとURLだけの鍵にすると、偽の鍵でも記録に当たる枠を足した。
6. 限界（POSTも対象・400/500も記録・pickle・既定の鍵の版の間の同一性は未確認・保存のハンドラの付け忘れ）と、`http_fixture_handler`との違い（ヘッダの値を鍵に入れ、鍵の作り方を替えられない）を書いた。
7. コードの枠を3つから5つにした。図2枚を手で書いたSVGで描き直した（作り方は`imagegen/replay-svg-source.txt`）。
8. 実装へのリンクを、リポジトリに無いcommit d4705914から、確かめたmainのcommitへ付け替えた。

### 実行した確かめ

```bash
uv run --no-sync python publications/zenn-use-cases-v0/reviews/replay-check.py  # 掲載の5枠を順に実行し、完全な例と比べ、決まりの数を測る。
uv run --no-sync python publications/zenn-use-cases-v0/examples/http_replay.py  # 完全な例の5つのテストを、pytestなしで順に走らせる。
uv run --no-sync pytest publications/zenn-use-cases-v0/examples/http_replay.py -q --durations=0  # 同じ5つをpytestで走らせ、時間を測る。
uv run --no-sync python publications/zenn-use-cases-v0/verify_comments.py  # 掲載コードと完全な例の各行に日本語コメントがあるかを見る。
uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/http_replay.py publications/zenn-use-cases-v0/reviews/replay-check.py  # 規約に沿うかを見る。
```

- `replay-check.py`は、4枠目のURLと記録のファイルだけを、localhostのサーバーと一時の場所に替えて実行する。
- 別のPythonのプロセスから同じ記録のファイルを開き、既定の鍵とメソッドとURLの鍵の両方で再生できることも確かめる。
- SQLiteの`cache`表の鍵に、APIキーの文字（`本物の鍵`・`Bearer`）が入らないことも確かめる。
- 2026-10-17（main d1c67469d）の結果：上の5つは全部exit 0。本文4,274字・コードの枠5・図2・60字を越える文0・です・ます以外0。pytestは2回とも5 passedで、関数の実行時間の合計は約1.8秒と約2.9秒（同じ機械で他の重い仕事も動いている時）。
- Zennの形：公開順の表で6本目は下書きなので、`export_zenn.py`は書き出さない。表は書き換えず、`export_zenn.prepare`と`write`を6本目だけ公開予定として呼ぶ小さな手で書き出し、`zenn-check/check.mjs --online`で読んだ。検査の誤り0・画像の欠け0・リンク9個中の切れ0・Markdownの崩れ0・秘密の形0（zenn-cli 0.5.6）。

### 実行していない範囲

外のサービスへの通信はしていない。本番の取得担当は、同じ機械の中のHTTPサーバー（127.0.0.1）でだけ確かめた。doeffの版を上げた後も既定の鍵が同じになるかは確かめていない。PyPIの公開版で動くかは確かめていない。
