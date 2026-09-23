# games記事の精査記録

- 担当: 記事別レビューエージェント review_games
- 日付: 2026-09-16
- 対象: `doeff-games.md`、`examples/game_replay.py`
- 適用した基準: doeff-patterns、doeff-runtime、記事別レビュー共通指示。

## 指摘と修正

1. 「対局保存は実装していない」という説明と、後半のJSON保存例が矛盾していた。完了履歴の保存・最初からの再構成は実装済み、入力の逐次永続化・プロセス強制終了からの復旧は未実装、と境界を明確にした。
2. イベント待機の例と、ドメインAPIの例で同じ `battle` 名を再定義していた。前者を `event_battle` にし、記事のコードを掲載順に実行できるようにした。
3. 冒頭・本文11コードブロック・専用例の各実質行に、目的と期待する値や動作を日本語コメントで加えた。多行構築の閉括弧は周囲のコメントで説明する。
4. 仮想時間のイベント待機も専用例へ統合し、HP10に3・4・5の入力が届いて、仮想3秒後に勝利することを検証した。
5. 再生時に履歴が不足するとき・履歴が余るとき・操作の引数が一致しないときを明示的に拒否するよう、記事専用ハンドラを修正した。
6. 入力の供給と記録を別ハンドラへ置く説明を明確にした。記録ハンドラは `yield effect` で外側の入力ハンドラへ依頼し、返った結果だけを記録する。下位クライアントの注入を要求しない。
7. 合成する処理は `@do` と `yield helper(...)` に統一。ハンドラやProgramを組み立てる通常関数は、実行時の計算とは異なることを説明した。`run` は記事の検証コードと専用例の `verify` に限定している。
8. `WaitUntil`・`Race`・AIメモ化など、その場に実コードのない機能の列挙を除いた。応用や別記事へのリンクとして範囲を示す。
9. 画像仕様を白背景の平面図＋コメント付きのコード抜粋へ更新した。図は親担当がimagegenで生成する。画像そのものの差し替え完了は、このレビューの完了主張に含めない。

## 実装根拠

- `doeff/program.py::handler`: raw handlerを `WithHandler` として取り付けるProgram構築関数を返す。
- `packages/doeff-core-effects/doeff_core_effects/handlers.py::state`: ハンドラの生成ごとに辞書を持ち、`Get`・`Put`を解釈する。対局ごとの生成で状態を分けられる。
- `packages/doeff-events/src/doeff_events/handlers/memory.py::event_handler`: `WaitForEvent` がPromiseを登録し、`Publish`は既存の待ち受けだけを完了させる。未購読入力の永続キューではない。
- `packages/doeff-time/src/doeff_time/handlers/sim_time.py::SimTimeRuntime`: `Delay`を時刻待ちへ変換し、待機タスクをスケジューラ上で再開する。仮想時計を進める補助タスクは低優先度で動く。
- `doeff/run.py::run`: 実行境界。不正履歴の検証で捕まえる例外についても、呼び出し時にはdoeffの例外トレースを出す。
- `DrawCard`・`ChooseAction`・`DealDamage`・記録/再生ハンドラは今回の例専用で、製品の組み込みAPIとして紹介していない。

## 検証

専用例:

```sh
PYTHONPATH=.:packages/doeff-core-effects:packages/doeff-events/src:packages/doeff-time/src uv run --no-sync python publications/zenn-use-cases-v0/examples/game_replay.py
```

成功時の表示は `ゲーム進行・記録再生・履歴の整合性・独立した2対局: OK`。欠落・余剰・引数不一致の3ケースは意図した `ValueError` を発生させ、検証側で捕まえる。したがってこの検証では3件の例外トレースが標準エラーに出るが、終了コードは0である。

追加で、記事から11個のPythonコードブロックを掲載順に抽出し、同じPYTHONPATHで連結実行した。終了コード0、標準エラーなし。ブロックごとの `ast.parse` も成功した。`ruff check` は専用例について成功。

検証済みの期待値:

- イベント例: `("勝利", 0, 3.0)`。
- 記録例: `{"turns": 3, "hp": 0}` と6件の入力。
- JSON保存からの再構成: 記録例と同じ結果。
- 不正履歴: 欠落・余剰・引数不一致をすべて拒否。
- 二つの対局: 3ターンと2ターンで、それぞれ独立にHP0へ到達。

## 実行していない範囲

外部API、実AI、画面、ネット対戦、クラッシュ復旧には接続・実行していない。製品コードと共通ファイルは変更していない。コミット・外部公開はしていない。

## 図の本文対応

`games-visuals.json`を図の正本にする。conceptのタイトルと説明、flowの説明を本文側で変更したため、親担当は共通 `visuals/captions.json` へ同じ値を反映する。
