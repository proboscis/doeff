# composition 記事レビュー

- 担当: `/root/review_composition`
- 対象: `doeff-composition.md`、`examples/composition.py`
- 使用したスキル: `doeff-patterns`、`doeff-runtime`
- 状態: 本文・専用例の修正とオフライン検証を完了。図の生成は親担当へ依頼。

## 指摘と修正

1. 旧例ではTellを発行するだけで、Writerの結果を取得・検証していなかった。`yield writer_log()`を本文へ追加し、成功・失敗時のログ内容を検証する。
2. `writer`が内部でGet/Putを発行するため、`state`を外側へ置く必要がある点が未説明だった。`state(...)(writer(program))`という順序と、結果・状態・ログを本文から明示的に返す方法を説明する。
3. 失敗を切り替えるbool引数の例を、正常な見出しと空の見出しという入力データで置き換える。設定・状態・ログ・時間を含む同じ処理が、入力によりOkまたはErrになる。
4. 待機を`@do wait_before_conversion()`へ分割し、`yield wait_before_conversion()`で合成する。ループ全体をasyncに移すような境界は使わない。
5. 状態の非巻き戻しに加え、失敗前のログも残ること、例外型・メッセージが保たれること、ハンドラを作り直した実行間で状態とログが混ざらないことを確認する。
6. 「モナド的な抽象の連結を@do/yieldへ揃える」と「任意のモナドや継続の複製を提供する」を区別する。個々のハンドラの意味は別途設計する。
7. 本文4コードブロックの各実質行と、専用例の58実質行へ、目的・期待値・動作を示す日本語コメントを追加する。

## 実装根拠

- `packages/doeff-core-effects/doeff_core_effects/handlers.py`
  - `reader`: Askを環境辞書から解決する。
  - `state`: ハンドラの作成時に状態辞書を作り、Get/Putで参照・更新する。
  - `_writer_handler`: Get/Putで外側の状態にログを蓄積する。
  - `writer_log`: 蓄積済みのメッセージをリストとして返すProgram。
  - `_try_handler`: 対象Programの成功をOk、ExceptionをErrとして返す。状態のスナップショットや巻き戻しを実装していない。
- `packages/doeff-time/src/doeff_time/handlers/sim_time.py`
  - Delayが現在時刻からの再開時刻を計算し、待機Programへ接続する。
  - GetTimeが仮想時計の現在時刻を返す。
- `doeff/result.py`: Ok/ErrはVMの型を再公開する。
- `tests/test_core_effects.py`: Reader・State・Writer・合成・Tryの現在の呼び方を確認する。

## 実行した検証

```sh
# 専用例: 正常変換・失敗・再実行で、結果・時刻・状態・ログを確認する。
uv run python publications/zenn-use-cases-v0/examples/composition.py
# 専用例の静的な問題を確認する。結果は All checks passed!。
uv run ruff check publications/zenn-use-cases-v0/examples/composition.py
# 本記事が使う基本effectと合成の既存テストを確認する。12 passed。
uv run pytest -q tests/test_core_effects.py::TestReader tests/test_core_effects.py::TestState tests/test_core_effects.py::TestWriter tests/test_core_effects.py::TestComposed tests/test_core_effects.py::TestTry
```

追加で、記事内のPython 4ブロックを抽出し、同じ名前空間へ順番に実行した。末尾のverifyが通過した。Pythonのtokenizeで実質コードのある行にコメントが付いていることを確認した。専用例のモジュール説明文字列と、閉括弧だけの行は対象外。

焦点を絞った既存テストでは、他の実行可能ADRが今回の対象に含まれないという既存の警告が1件出た。12テストは成功。リポジトリ全体の検証はこの担当では行っていない。

外部サービス・API・実エージェントの起動は不要で、実行していない。製品コード・共通管理ファイル・他記事は変更していない。

## 図への引き継ぎ

`reviews/composition-visuals.json`を正本とする。本文のaltとキャプションを対応する内容へ更新済み。概念図はTry・Get・writer_logの戻り値と共通のyield、処理図は空文字の失敗後にも回数と開始ログが残ることを示す。
