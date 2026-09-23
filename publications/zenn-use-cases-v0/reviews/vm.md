# Rust VM記事の実装照合

担当: review_vm。確認日: 2026-09-16。

## 結果

新記事 `doeff-vm.md` を作成し、Pythonコード11ブロックを本文の順序で実行した。`examples/vm_walkthrough.py` を拡充して実際のRust VMで検証した。製品コードの変更、外部サービスの呼び出し、commit、pushは行っていない。

## 説明と根拠

基準版は `d4705914e39740aee98a9f57a4535c463d9479cc`。本文の実装リンクはすべてこの版へ固定した。

| 主張 | 照合した一次実装 | コードによる確認 |
| --- | --- | --- |
| `@do`呼び出しは`Expand(Apply(...))`を作り、元の関数は実行時に呼ぶ | `doeff/do.py::do` | `deferred`構築後の観測リストが空、実行後に記録される |
| ネストする計算も`yield greet()`でつなぐ | `doeff/do.py`、`vm/step.rs::eval_expand` | `deferred`から`greet`を実行 |
| `run`は`PyVM`を作って実行する | `doeff/run.py::run`、`packages/doeff-vm/src/pyvm.rs` | `PyVM().run(...)`と`run(...)`の同じ結果 |
| generatorの`send`、`StopIteration.value`がVMの再開・戻り値に接続される | `packages/doeff-vm/src/python_generator_stream.rs::send_to_generator` | `name`にハンドラの値が入り、挨拶文字列が返る |
| `Effect`は暗黙の`Perform`に分類される | 同ファイル`classify_python_object` | `greet`と`explicit_greet`の一致 |
| 最も近い境界から処理し、`Pass`が外側へ進める | `packages/doeff-vm-core/src/vm/dispatch.rs::find_handler_for_effect`、`vm/step.rs::eval_pass` | 内側の`forward`を通って外側の`supply_name`へ到達 |
| 継続は本体から境界を含む列であり、切り離し・再接続される | `vm/dispatch.rs::perform_effect`、`reattach_chain`、`continuation.rs` | 再開・戻り順を実行し、内部構造はソースと照合 |
| ハンドラ本体は境界の外側で動く | `vm/dispatch.rs::perform_effect`が`current_segment`を`boundary_parent`に変更 | ソースの接続構造と照合。翻訳の統合例はハンドラ記事が担当 |
| 非末尾`Resume`はハンドラへ戻る | `vm/step.rs`の`Resume`分岐 | `@do(non_tail=True)`の観測順をassert |
| `Transfer`は現在のハンドラ呼び出しフレームを外し、境界は継続に残る | `vm/step.rs`の`Transfer`分岐、`vm/dispatch.rs` | 同じハンドラが2回の`ReadName`を処理する |
| 安全と判定された末尾`Resume`は`Transfer`として分類される | `doeff/do.py::_analyze_resume_yields`、`python_generator_stream.rs::classify_tail_resume` | 既存の末尾位置テスト6本 |
| 同じ継続は2回利用できない | `continuation.rs::Continuation::take`、`vm/dispatch.rs::reattach_chain` | 二重再開を`RuntimeError`として確認。既存2テストも成功 |

## 混同を避けた点

- Pythonの関数本体をRustへコンパイルするという説明はしない。Rust VMが制御を処理し、Pythonのgeneratorを進める。
- `Resume`の結果と、エフェクトの戻り値を区別する。非末尾`Resume`が受け取るのは継続の完了結果。
- `Transfer`をハンドラ境界の永久削除とは説明しない。既存`tests/test_deep_handler.py`の該当コメントは、2回目のエフェクトを試していない。現行実装を読み、2回の操作を処理する実行例を追加した。
- 内部の`Fiber`はOSスレッドと区別する。
- `k`の複製と、新しいProgramを作る再実行を区別する。durable executionやTraverseに継続複製を帰属させない。
- `run`は記事の検証境界に限る。計算の合成へ`async def`や入れ子の`run`を持ち込まない。
- 観測用リストの更新は実行順を示す検証用途と本文で明記した。

## 検証

- `uv run --no-sync python publications/zenn-use-cases-v0/examples/vm_walkthrough.py` 成功。
- 本文のPython11コードブロックを抽出し、一時的な検証用Pythonファイルで順番に実行して成功。成果物の本体と例はリポジトリに保存している。
- `uv run --no-sync ruff check --fix publications/zenn-use-cases-v0/examples/vm_walkthrough.py` 成功。import順を修正した。既存例の例外節内assertは、例外の文字列を取得した後のassertへ移した。
- `uv run --no-sync pytest tests/test_do_tail_resume_ast.py tests/test_double_resume_traceback.py -q`: **8 passed**。焦点テストなのでADRファイルが未収集という既存プラグイン警告が1件出た。

## 画像と統合

`reviews/vm-visuals.json`へ、2枚のタイトル・実コード・処理構造・captionを保存した。本文のaltとcaptionはこのJSONに一致する。最終画像と共通台帳は親エージェントが制作・統合する。

- `vm-concept.png`: PythonのgeneratorとRust VMの役割の境界。
- `vm-flow.png`: 継続の切り離し、ハンドラ、再接続、`send`の順。

`published: false`。メイン記事とハンドラ合成の記事へのリンクを置いた。共通のTODO、一覧、画像台帳、検証スクリプトは変更していない。

## 追加要求：コードの各行へ目的と期待動作を書く

2026-09-16の追加要求に対応し、本文のPython全11ブロックと `examples/vm_walkthrough.py` の各実質行へ、日本語で目的・期待する動作を記載した。対象は合わせて193行。import、デコレータ、関数定義、分岐、リスト要素、閉じ括弧、検証assertも含む。コードが長い行は直前に、それ以外は行末に注釈を置いた。意図的な二重再開の誤りも、拒否されることが期待結果だと各行で説明した。

図用のコードにも短い行コメントを追加し、`vm-visuals.json` の `code_line_notes` に各行の目的・期待動作を明記した。画像のtitleとcaptionは変更していない。実行コマンドにも成功時の出力を説明するコメントを付けた。

- 本文11ブロックを順に実行し直して成功。
- 対応例を実VMで実行し直して成功。
- 各実質行に行末または直前コメントがあることを確認し、193行で成功。
- `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/vm_walkthrough.py` 成功。
- コルーチンとの比較記事 `doeff-coroutines.md` への関連リンクを追加した。
