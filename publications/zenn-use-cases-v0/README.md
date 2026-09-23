# doeffとは？ — Zenn記事シリーズ

原稿・コード・図・検証記録をdoeffリポジトリの著作物として管理する。メイン1本＋個別22本、全23記事の未公開草稿。

公開は[最初の7日間、毎日1本の計画](publication-plan.md)で進める。メインから始め、Agents、仮想時間、HTTPとMemo、ゲーム、coroutine比較、DIの順に紹介する。

## 読む順番

まず[doeffとは？](doeff-main.md)で、短いコードとエフェクト・ハンドラの仕組みを読む。その後、自分の困りごとに合う記事へ進む。

| 悩み | 個別記事 |
| --- | --- |
| エージェントの起動・結果待ちを処理へ組み込みたい | [doeff-agentsと共同作業](doeff-agents.md) |
| LLMのプロバイダごとのSDKが処理に広がる | [LLM](doeff-llm.md) |
| 画像生成・編集をパイプラインへ入れたい | [画像](doeff-image.md) |
| 入力待ちを含むゲームのルールを書きたい | [カードゲームとゲーム進行](doeff-games.md) |
| イベントや他の処理の完了を待ちたい | [イベント・並行処理](doeff-events.md) |
| 逐次・並行・失敗の戦略を本体から分けたい | [コレクション処理](doeff-traverse.md) |
| 待機のあるテストが遅い | [時間とスケジューリング](doeff-time.md) |
| asyncの変更が呼び出し元へ広がる | [関数の色](doeff-color.md) |
| 依存を呼び出しの途中で引き回している | [DI](doeff-di.md) |
| 状態・ログ・失敗を別々の作法で扱っている | [抽象の合成](doeff-composition.md) |
| HTTPを何度も呼ばずに調査したい | [記録・再生とメモ化](doeff-replay.md) |
| 完了した仕事をやり直したくない | [永続実行の入口](doeff-durable.md) |
| 長い処理の現在地が分からない | [実行の観測](doeff-observe.md) |
| Git・通知・秘密情報を本番とテストで使い分けたい | [外部操作](doeff-operations.md) |
| 処理をコンテナや別環境へ移したい | [Docker・ML実行環境](doeff-remote.md) |
| 手元・共有・永続保存を階層として組み合わせたい | [L1・L2・L3のメモ化](doeff-memo.md) |
| 副作用かドメインAPIか、操作の境界を設計したい | [エフェクトの境界](doeff-boundaries.md) |
| 同じ計算を、マクロで簡潔に書きたい | [Hy](doeff-hy.md) |
| 定義を発見し、設計上の約束を検査したい | [開発支援・実行基盤](doeff-tooling.md) |
| coroutineとの違いを知りたい | [coroutine比較](doeff-coroutines.md) |
| ハンドラを重ねた時の意味を知りたい | [ハンドラ合成](doeff-handlers.md) |
| Rust VMの中断・再開を知りたい | [Rust VM](doeff-vm.md) |

## 編集とレビューの基準

- メインは冒頭キービジュアル→定義→Askと最小ハンドラのコード→仕組み→悩みと解法の表。AIエージェントは用途紹介の先頭に置く。
- 中心は「エフェクトの解釈はハンドラ次第」。HTTP取得、Memo保存、時計などを独立に合成する。
- 合成する補助関数は`@do`、呼び出しは`yield helper(...)`。SDKの非同期primitiveに接続する地点だけ`Await`を使う。
- 各コード行に目的と期待する値・動作の日本語コメントを付ける。Python以外のHy・CLI例も対象。
- 各記事を別のエージェントが実装へ照合し、修正・検証した。[記事別レビュー](reviews/README.md)に全23件の根拠と実行範囲を記録。
- すべての紹介機能にコードを付ける。[43機能のコード対応](feature-examples.md)、[31パッケージの棚卸し](package-coverage.md)、[全指示のTODO](TODO.md)、[完了照合](completion-audit.md)を参照。
- 非公開の実用例からは設計概念だけを使い、架空のカードゲーム・文書処理として独立に著作した。元のコード・名称・接続先・運用条件は載せない。

## 検証記録

2026-09-16、開発checkout `d4705914e39740aee98a9f57a4535c463d9479cc` の実装に照合。公開パッケージのクリーンインストール検証ではない。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/verify_examples.py  # ローカル例・原稿・図・コメントの整合を一括確認する。
uv run --no-sync ruff check publications/zenn-use-cases-v0/*.py publications/zenn-use-cases-v0/examples/*.py  # 原稿用Pythonの静的検査を行う。
```

一括検査は21本のオフライン例、SQLiteの実3プロセス、Hy・ADR・Semgrep、エージェントなしのConductor履歴再利用を扱う。メインの最小ハンドラ、担当外の委譲、22記事の表も実行検査する。原稿検査は23記事・31パッケージ・43機能・47画像・Python138ブロックの構文とリンクを確認し、本文・完全な例のPython実質4,101行の日本語コメントも検査する。最終修正後の一括検査とRuffは成功した。コメントの説明内容とHy・CLIの用法は記事別の担当が確認した。

HTTPは固定・拒否ハンドラとSQLiteの組み合わせで検証。LLMは実SDKのストリーム解析器へ固定データを渡し、各チャンクのAwaitと例外伝播まで確認した。Agentsのシナリオは本番の認証・スキーマ再試行を検証したものではない。

### 判明した既存の制約

- `doeff-notify`のtesting委譲に旧`Pass()`が残る。記事では対象を選別し、現行の`Pass(effect, k)`で外側へ渡す。
- `doeff-secret`の未対応分岐に削除済み`Delegate()`が残る。環境変数からクラウドへの自動fallbackを成功済みとは扱わない。
- `doeff-flow`は`RunResult`のimportに失敗。トレースの使用形と、今回実行できない範囲を明記した。
- `doeff-ml-nexus`の上位ファクトリはHyの契約不足でimport失敗。既存Docker/ML関連テストは11成功・7失敗で、7件はwriter外側のstate不足。記事の正しい構成・固定ハンドラ・Programのシリアライズ往復は成功した。

この原稿制作では製品runtimeを変更していない。全リポジトリのテスト成功を主張せず、記事別に実行した検査と残る不整合を記録する。

実エージェント、LLM/画像の有料API、Gitへの書き込み・通知送信、実Docker/SSH/GPU、外部操作の強制終了からの復旧は未実行。記事の掲載と本番での動作実証を区別する。

## 図とプレビュー

メイン最上部のキービジュアル1枚と、全23記事の概念図・処理図46枚を掲載。組み込みimagegenによる全47枚は、白い不透明背景の平面図で、短いコードとコメント付き。各3MB未満。コード・値・矢印を個別に目視確認した。

- 掲載PNG：リポジトリ直下の `images/zenn-use-cases-v0/generated/`。
- [制作記録](imagegen/README.md)、[採用画像と生成・編集プロンプト](imagegen/manifest.json)。
- 旧版はarchiveと旧DOT/SVG/PNGに保持。`render_visuals.py`は旧版用であり、現在の掲載画像は作らない。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/render_preview.py --output /tmp/doeff-zenn-preview.html  # 正本から画像・コードを埋め込んだ閲覧用HTMLを作る。
open /tmp/doeff-zenn-preview.html  # 生成した記事をブラウザで開く。
```

正本はリポジトリ内のMarkdown・例・画像。`/tmp`は生成プレビューと検証の一時出力だけに使う。全記事は`published: false`で、Zennへの投稿・公開は行っていない。公開時は記事間リンクを実URLへ変更する。

編集判断と戻し方は[決定記録](../../docs/design/decisions/2026-09-15-zenn-use-cases.md)へ保存した。
