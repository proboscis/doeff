# メイン記事の精査記録

- 担当：記事別レビュー担当 `review_main_minimal_handler`
- 確認日：2026-09-16
- 対象：`doeff-main.md`、本文のPythonコード2例、悩み・解法・22詳細記事の表
- 適用した指針：`doeff-patterns`、`doeff-runtime`、記事別レビュー共通指示。スキルの旧API例ではなく現行実装を優先して照合した。
- 分担：本文と図は親担当が編集。この担当は`main-check.py`と本記録だけを更新した。

## 今回の指摘と修正確認

1. 冒頭で`Ask("name")`を依頼する`greet`と、その依頼を受ける最小ハンドラを両方提示している。`@handler`、`@do`、`def handle_ask_effect(effect, k)`、`Resume`、担当外の`Pass(effect, k)`まで本文中にある。
2. 設置には公開APIの`with_handlers([handle_ask_effect], greet())`を使う。ハンドラの中身が分からないまま`handler(program)`のような適用式だけを示す導入ではなくなった。
3. 2つ目のコードは最初のコードに続けて実行することを明記し、同じ`greet`に「太郎」を供給する別のハンドラを定義している。2つとも実行可能で、各実質行に目的・値・動作を説明する日本語コメントがある。
4. 最小の同期的な例に標準Readerやスケジューラを持ち込んでいない。掲載ハンドラが使う操作は`Resume`と`Pass`であり、この範囲の実行にスケジューラは不要。
5. 悩み、解法、詳細記事へのリンクを表にまとめている。初回確認時に表から欠けていたcoroutine・ハンドラ合成・Rust VMの3記事を親担当へ報告し、追加後に22記事すべてが表から参照できることを確認した。AIエージェントは具体用途の最初にある。
6. 旧検査は`lazy_ask`の追加とコードブロックごとに別の名前空間を使う前提だった。本文をそのまま同じ名前空間で順次実行し、APIの意味も確認する検査へ更新した。

## 実装根拠

| 説明 | 確認した実装 |
| --- | --- |
| `greet()`は本文の実行結果ではなく計算を作る | `doeff/do.py::do`は`Expand(Apply(...))`を返す。実行検査でも`Program`のインスタンスであることを確認 |
| `Ask("name")`はキー付きの値の依頼 | `packages/doeff-core-effects/doeff_core_effects/effects.py::Ask`は`EffectBase`を継承して`key`を保持 |
| `@handler`と`@do`でハンドラを定義できる | `doeff/program.py::handler`は`@do`で作ったdispatcherを`WithHandlerType`へ取り付ける関数に包む |
| `with_handlers`でハンドラを取り付ける | `doeff/program.py::with_handlers`は取り付け関数を合成。リストの先頭が外側、末尾が内側 |
| 担当外の`Ask`や別のエフェクトは外側へ進む | 掲載ハンドラを内側に置き、`Ask("language")`と独自`UnrelatedEffect`を外側へ渡す実行検査が成功 |
| `Resume(k, value)`で依頼した位置へ値を渡す | 掲載コードの`name`と外側ハンドラの返す値を、継続再開後の戻り値で確認 |
| `run()`は計算結果を返す | `doeff/run.py::run`は`PyVM().run(doexpr)`を呼ぶ。挨拶の文字列まで検証 |

## 検証

リポジトリルートで実行：

```bash
uv run --no-sync python publications/zenn-use-cases-v0/reviews/main-check.py  # 本文2例、委譲、コメント、22記事の表を検査する。
uv run --no-sync ruff check publications/zenn-use-cases-v0/reviews/main-check.py  # 検査コードの静的検査を行う。
```

結果：両方成功。本文のimportだけで2ブロックを順次実行し、「こんにちは、花子さん」と「こんにちは、太郎さん」を得た。

追加の実行検査は両方の掲載ハンドラを対象とし、次を確かめた。

- `greet()`の戻り値は`Program`。
- `Ask("name")`は掲載ハンドラが受け持ち、外側のハンドラへ届かない。
- `Ask("language")`は外側へ進み、「日本語」が元の処理へ戻る。
- `Ask`以外のエフェクトも外側へ進み、「外側で処理」が元の処理へ戻る。
- 上記の3操作を一続きの計算で実行し、再開後の戻り値と外側に届いた依頼の順番が一致する。
- 2つのハンドラはASTでも`@handler`→`@do`、`(effect, k)`の宣言である。
- 各実質行に日本語コメントがあり、22詳細記事がすべて表から参照できる。

画像の生成・目視確認、画像台帳、プレビューとの整合は親担当の検証範囲。この検査は画像内の文字の正しさを保証しない。

外部接続は実行していない。LLM、エージェントの起動、Docker、SSH、有料API、通知、公開操作は含まない。詳細記事のコードは各記事の担当が別途検証する。
