# DI記事の再精査

- 担当: `review_di`（記事専任エージェント）
- 日時: 2026-09-16
- 確認対象: `doeff-di.md`、`examples/dependencies.py`
- 確認版: `d4705914e39740aee98a9f57a4535c463d9479cc`
- 適用した指針: `doeff-patterns`、`doeff-runtime`、記事別共通レビュー基準

## 指摘と修正

1. 元の整形実装は普通の関数で、利用側も `formatter(text)` と直接呼んでいた。`FormatTitleFn` というProtocolを依存キーにし、両実装を `@do`、利用側を `yield formatter(text)` へ変更した。取得する段階と子Programを実行する段階を区別した。
2. 遅延解決を観測するための `built.append(prefix)` を、`yield Tell(prefix)` に変更した。`writer`・`state`・`writer_log`を実際に合成して、取得4回に対して作成2回という動作を検証した。
3. `reader`は辞書の値をそのまま渡すこと、`lazy_ask`が `Ask` と `Local`を処理すること、Programの遅延評価にschedulerが必要なことを明記した。`Local`内の変更は依存する結果だけ別に作ることを確認した。
4. 環境変数の例を `env_var_ask(...)(lazy_ask(env={})(...))` に変更した。内部の未登録Askが外側へ渡ることまで確認した。架空の変数を `patch.dict` で設定し、終了時に元へ戻す。利用側Programは環境変数を直接読まない。
5. `doeff-pinjected`は現行workspaceから削除済み。削除契約テストを根拠に短い注記を置き、存在しない連携APIは紹介しない。
6. 依存の取得と、通信操作をエフェクトで切ることを区別した。HTTPの具体例は、HTTPハンドラとMemoハンドラを合成するreplay記事へリンクした。
7. 全コード行に目的・期待する値や動作の日本語コメントを追加した。画像の正本仕様を `di-visuals.json` に保存し、本文altとキャプションを一致させた。

## 実装根拠

| 確認内容 | 根拠 |
|---|---|
| Askは任意のキーを保持し、LocalはenvとProgramを受け取る | `packages/doeff-core-effects/doeff_core_effects/effects.py` の `Ask` / `Local` |
| readerは辞書の値をそのまま返す | 同 `handlers.py` の `reader` |
| 遅延評価・依存追跡・Localごとのキャッシュ・外側への委譲 | 同 `handlers.py` の `lazy_ask` |
| セマフォで遅延解決を調整する | `lazy_ask` の `CreateSemaphore` / `AcquireSemaphore` / `ReleaseSemaphore` |
| writerは状態へログを保存し、writer_logはコピーを返す | 同 `handlers.py` の `_writer_handler` / `writer_log` |
| 環境変数の通常値は文字列、未登録はPass | 同 `handlers.py` の `env_var_ask` |
| ハッシュ可能な非文字列キーを使える | `tests/misc/test_reader_hashable_keys.py` |
| pinjected連携が削除された状態を契約とする | `tests/core/test_pinjected_removal.py` |

## 検証

- `.venv/bin/python publications/zenn-use-cases-v0/examples/dependencies.py`: 成功。整形2種、通常/Local/復帰の結果、作成ログ2件、環境変数への委譲をassertで確認。
- 本文のPythonコードを正規表現 `r'```python\n(.*?)```'` で抽出し、同じ名前空間に順番に `exec(compile(...))`: **3ブロック成功**。第3ブロックは本文に明記したとおり第2ブロックの `read_greeting`を利用する。
- `.venv/bin/ruff check publications/zenn-use-cases-v0/examples/dependencies.py`: 成功。
- `.venv/bin/python -m pytest tests/effects/test_lazy_ask.py tests/effects/test_env_var_ask.py tests/misc/test_reader_hashable_keys.py tests/core/test_pinjected_removal.py -q`: **33 passed**。既存のADR未収集警告1件。今回は指定したDI関連テストの検証であり、ADR全件の検証とはしていない。
- `tokenize`でコメント位置と実質行を照合: 専用例63行、本文20行・31行・6行にコメントあり。空行、閉じ括弧のみの行、冒頭docstringは除外。画像用抜粋8行も各行にコメントあり。

## 検証範囲と残り

実HTTP・LLM・agent・外部の環境設定・pinjected連携は実行していない。使用した環境変数は記事専用の架空値のみ。製品runtime・共通資料・他記事は変更していない。画像の生成とmanifest/captionsの更新はroot担当へ引き渡す。

## 週4の公開に向けた仕上げ（2026-10-16）

- 担当: 記事の取り組みの週4の担当（card ki-e35175c03800）
- 確認版: `914ea5629ecbc9ed7af84926310eff312cd4eb12`（doeffのmain）
- 基準: 記事の書き方の決まり9項目と、[公開計画](../publication-plan.md)の各回の仕上げ基準

### 直したこと

1. 題を「DIの代わりにeffect handler — 依存を外側から渡す」にした。形を、悩み→短いコード→全体の図→本文→まとめ→参考に揃えた。
2. 冒頭の悩みを1つにした。引数で運ぶ手間と、コンテナに書き方が縛られる事。言葉の表でDI・エフェクト・ハンドラ・Programを説明した。
3. 自作のハンドラ`test_formatter`の枠を足した。`@handler`・`@do`、依頼の判別、`Resume`、担当外の`Pass`と外側の`reader`の重なりを示す。
4. まとめに、効く所と儀式になる所の表を置いた。`Ask`で実装を選ぶだけなら能力はDIと同じ事、答え手が無いと実行時に`UnhandledEffect`になる事も書いた。引数で渡すDIを勧める文は消した。
5. pinjectedの削除の注記と参考のリンクを抜いた。読者に要らないリポジトリの経緯のため。社内の別のprojectの名は、草稿に見当たらなかった。
6. 参考のリンクが指していた`d4705914e`はリポジトリに無く開けないので、mainの`914ea5629`へ替えた。
7. 図2枚をSVGで描き直した。imagegenの版はリポジトリに入っておらず、失われていた。作り方は週3と同じ`visuals/render_svg.mjs`で、[di-svg-source.txt](../imagegen/di-svg-source.txt)に書いた。
8. 完全な例`examples/dependencies.py`を本文の4枠と同じ中身にし、型をキーにする例を足した。

### 9項目の照らし

| 項目 | 結果 |
| --- | --- |
| 長さ | 本文3,218字（読者に見える字・空白と表の縦線を除く）。記号とURLを含めると4,466字 |
| コードの枠 | 5枠（Python4・bash1） |
| 図 | 2枚。冒頭の短いコードの直後に全体の図 |
| 言葉 | 冒頭の表でDI・エフェクト・ハンドラ・Programを説明 |
| 口調 | です・ます。60字を越える文0 |
| 形 | 悩み→短いコード→全体の図→本文→まとめ→参考 |
| 数字 | 取得4回・作成2回はassertで確かめた値。検証の日と版を参考に書いた |
| 秘密 | APIキー・トークン・個人の情報・社内の機体やprojectの名は無い。環境変数は架空の値 |
| 公開の前の確認 | `published: false`のまま。公開の許しを利用者に問う |

### 検証

- 本文のPython4枠を順に同じ名前空間で実行: 4枠とも成功。
- `uv run python publications/zenn-use-cases-v0/examples/dependencies.py`: 成功。
- `uv run ruff check publications/zenn-use-cases-v0/examples/dependencies.py`: 成功。
- `verify_comments.py`: 成功。
- `verify_publication.py`: 他の記事の43枚のPNGがリポジトリに無いため、画像の組の検査で失敗する（mainでも同じ）。同じ検査をDIの記事と図2枚に絞ると成功した。
