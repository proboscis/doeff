# 画像生成・編集の記事レビュー

- 担当: `/root/review_image`
- 日付: 2026-09-16
- 参照した実装: `d4705914e39740aee98a9f57a4535c463d9479cc`
- 対象: `doeff-image.md`、`examples/image_pipeline.py`
- 適用基準: 共通の `REVIEW-INSTRUCTIONS.md`、doeff-patterns、doeff-runtime。APIはスキルの例より現行コードを優先した。

## 指摘と修正

1. 従来の例は生成・編集を一つの関数に置くだけで、Program同士の合成を見せていなかった。`generate_cover`、`brighten_cover`、`create_cover`をそれぞれ`@do`にし、helperを`yield`して合成した。独自の`async def`、一連の処理を包む`Await`は使わない。
2. 付属mockが16×16の画像を返すことだけでは、生成画像を次の編集へ渡している証拠にならない。独自の固定effect handlerで、画像リストの同一性、生成→編集の順序、各依頼のモデル・プロンプト、最終結果の同一性を検証した。
3. GeminiとSeedreamの付属mockは、画像エフェクトのモデル名を本番同様には振り分けず、入力画像を実際に編集もしない。テスト専用モデル名と16×16という値の意味を明記し、本番モデルの利用可否・編集品質の検証とは区別した。
4. `ImageEdit.mask`はフィールドとして存在するが、現行の両productionハンドラの編集実装ではSDKへ転送されない。「マスクも渡せる」という推薦を撤回し、実装上の限界を本文へ明記した。
5. 本番ハンドラの断片を、不要な通常関数のラッパーからProgram定数へ変更した。接続の定義だけであり、認証・Await・状態・ログを扱う実行環境が別途必要なことを明記した。
6. 検証途中で`from doeff import WithHandler`が現行APIでは失敗すると確認した。新例は公開された`@handler`でProgramへ取り付ける形へ直し、実行をやり直した。
7. 全Pythonブロックと例ファイルの各実質行へ、目的・戻り値・期待動作の日本語コメントを追加した。多行呼び出しの閉括弧だけの行は、直前までのコメントで対応が分かる形にした。

## 実装根拠

- `packages/doeff-image/src/doeff_image/effects/generate.py`: `ImageGenerate`はキーワード引数のdataclass。`prompt`、`model`を必須とする。
- `packages/doeff-image/src/doeff_image/effects/edit.py`: `ImageEdit.images`は`list[PIL.Image.Image]`。
- `packages/doeff-image/src/doeff_image/types.py`: `ImageResult.images`、`model`、`prompt`。`to_pil_image()`は先頭の画像を返す。
- `packages/doeff-gemini/src/doeff_gemini/handlers/testing.py`: 画像mockは共通`ImageResult`を返し、既定では16×16の画像を作る。
- `packages/doeff-seedream/src/doeff_seedream/handlers/testing.py`: 同じ共通結果型と既定サイズ。画像の編集依頼を記録するが、その入力を実際に編集するテストではない。
- 両パッケージの`handlers/production.py`: `ImageGenerate`、`ImageEdit`を対応モデルへ振り分け、`Resume`で結果を返す。Geminiの`_image_edit_impl`、Seedreamの`_image_edit_impl`は`mask`を参照しない。
- `doeff/program.py`: `handler(raw_handler)`が生の依頼処理をProgram変換としてラップする。`doeff/__init__.py`は`WithHandler`という名前を公開していない。

## 実行検証

`uv run --no-sync python`から、各workspace packageの`src`（無い場合はpackage自身）をPython探索パスへ加え、次を実行した。

- `publications/zenn-use-cases-v0/examples/image_pipeline.py`: 成功。
  - `ImageEdit.images is base.images`
  - 依頼順が`["generate", "edit"]`
  - 生成・編集のモデル名とプロンプト
  - 最終戻り値が固定編集結果そのもの
  - Gemini・Seedream付属mockの結果型・画像数・モデル名・16×16サイズ
- 記事の5個のPythonブロックを抽出して同じ名前空間で順番に評価: 全て成功。固定ハンドラの抜粋は関数定義を確認し、その処理本体は完全な例で実行。本番用ブロックはProgramの構築までで止めた。
- `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/image_pipeline.py`: 成功。

製品コード、共通検査スクリプト、共通の画像manifestは変更していない。実LLM・画像生成API・認証・SDK通信・課金・外部送信は実行していない。

## 図版への申し送り

`image-visuals.json`を新しい図版指示の正本とする。白背景の平面的な図へ、各行コメント付きの実例と値の受け渡しを添える。生成と編集を並列に描かない。テスト用ハンドラを、本番品質を検証した装置として描かない。記事のalt・本文のキャプションをJSONと一致させた。
