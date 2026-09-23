# tooling 記事別レビュー

- 担当: `/root/review_tooling`
- 日付: 2026-09-16
- 対象: `doeff-tooling.md`、`examples/domain_check.py`、`examples/adr_example.hy`、`examples/static_check.hy`
- 適用したスキル: doeff-patterns、doeff-hy-macros、doeff-defadr、doeff-defsemgrep

## 指摘と修正

1. **索引の型フィルタの意味が違っていた。** `find-kleisli --type-arg`は戻り値の型ではなく、唯一の必須引数の型を照合する。さらに現行の`find-kleisli`は`@do`だけを対象にせず、`# doeff: kleisli`という明示的な印が必要。文字列を受け取る`read_title`を追加し、定義行に印を付け、CLIを`--type-arg str`へ修正した。本文でこの条件を明示した。
2. **ADRのpytest呼び出し例が、既定の収集対象名と合っていなかった。** `adr_example.hy`をそのままpytestへ渡す例を削除。教材では生成された2種類のテスト関数を明示的に実行する。実プロジェクトでは`defadr_*.hy`または`doeff_adr_hy_files`設定と、`doeff_interpreter`フィクスチャが必要であることを説明した。
3. **対応宣言と実行時の設置を分けた。** `@handles`は対応範囲を宣言するだけであり、`handler(title_handler)(program)`が実際の設置を担当する。Domain検査を通すだけでは実装の正しさを証明できないため、返る見出しも実行テストする。
4. **`isolated_registry`を教材から取り除いた。** 現行の`doeff_domain.__init__`には公開APIとして実在するが、この例では`assert_domain_covered(Domain値)`で登録表を使わず正常例・反例を検査できる。登録表の一時初期化を主題に持ち込む必要がない。所属漏れを調べる節でだけ、通常の`register_domain`と`assert_no_orphan_effects`を使う。
5. **合成をProgramに保った。** Pythonの見出し取得は`@do`で定義して`yield ReadTitle(...)`を使う。Hy例は`defk`、`do!`、`<-`で合成し、`deftest`へハンドラ付きのテスト実行環境を渡す。制御全体をasync関数へ移していない。
6. **全機能のコードと実行範囲を明示した。** indexer、analyzer、linterには実在するCLI引数を載せたが、バイナリ未導入なので実行済みとは記さない。Hy、ADR、Semgrep、Domainは具体的な入力と期待結果まで実行した。VMは公開`run`の例と専用記事へのリンク、test-targetは実在する解析対象のコマンド、旧agentdは現行の紹介可能な起動APIがないこととagentsへの導線を記載した。
7. **各行の日本語コメントを追加した。** Python/Hy/shellの各実質行に役割と期待結果を記した。複数行importの各要素もコメント付き。閉括弧のみの行、空行、here-document終端は構文上の区切りとして除外した。図用コードにもコメントを含めた。

## 実装根拠

| 対象 | 確認箇所 |
|---|---|
| Domain値の直接検査、未対応例外、所属漏れ | `packages/doeff-domain/src/doeff_domain/checks.py` の `assert_domain_covered`、`_assert_domains_covered`、`assert_no_orphan_effects` |
| 登録の重複拒否とisolated_registryの実在 | `packages/doeff-domain/src/doeff_domain/registry.py`、`__init__.py` |
| handlesが宣言に限られること | `packages/doeff-domain/src/doeff_domain/introspect.py` の `handles`、`handled_effects` |
| 明示マーカーと引数型フィルタ | `packages/doeff-indexer/src/indexer.rs` の `find_kleisli`、`find_kleisli_with_type`、`kleisli_parameter_matches` |
| indexerのコマンド引数 | `packages/doeff-indexer/src/main.rs` |
| sedaのCLI名とanalyze/hy引数 | `packages/doeff-effect-analyzer/Cargo.toml`、`src/main.rs` |
| linterのJSON出力とログ抑止 | `packages/doeff-linter/src/main.rs` の `Args` |
| ADRの既定収集名と追加設定 | `packages/doeff-adr/src/doeff_adr/pytest_plugin.py` の `DEFAULT_FILE_PATTERNS`、`_file_patterns`、`pytest_collect_file` |
| ADR/Semgrepの生成テスト | `packages/doeff-adr/src/doeff_adr/macros.hy`、`registry.py` |
| VMの分割 | `packages/doeff-vm/Cargo.toml`、`packages/doeff-vm-core/Cargo.toml` |
| test-targetの実在する解析入力 | `packages/doeff-test-target/src/doeff_test_target/core/alpha.py` |

## 検証

再現用の検証スクリプトを`reviews/tooling-check.py`へ保存した。ワークスペースの各パッケージをPYTHONPATHへ追加し、既存のPython環境で実行する。検証のために新しい環境の作成や依存更新は行わない。

```bash
.venv/bin/python publications/zenn-use-cases-v0/reviews/tooling-check.py # 本文と専用例の実際の結果を確認する。
.venv/bin/ruff check publications/zenn-use-cases-v0/examples/domain_check.py # 専用Python例のlintを確認する。
```

結果は両方成功。

- Domain専用例: 対応宣言の成功、ハンドラ未指定時の`DomainCoverageError`と操作名、実際の戻り値`"見出し"`、登録後の所属検査を確認。
- 本文Python 4ブロック: 定義を先にロードし、抜粋を含む全コードを実行。
- 本文Hy 3ブロック: ADRの挙動テストと構造契約、Semgrepのbad/good、ハンドラを設置したHyのあいさつテストを実行。
- 掲載したADR/Semgrep呼び出し用2ブロック: here-documentのPython本体を既存Pythonで実行。シェルの`uv run`による環境更新は行っていない。
- Semgrepは`SEMGREP_SEND_METRICS=off`で実行。
- `static_check.hy`は既存の教材用検査へコメントを付けた変更で、パターン・bad/goodの意味は変更していない。実運用のSemgrep規則、ADR、台帳は変更していない。

## 未実行と限界

indexer・seda・doeff-linterの実行バイナリは環境に存在しない。CLI実装の照合に留め、本文でも未実行と明記した。外部API、LLM、エージェント、Docker、SSHへの接続は行っていない。

図のコード・流れ・キャプションは`reviews/tooling-visuals.json`を正本として保存し、本文の画像altとキャプションも合わせた。imagegenによる画像生成と共通の画像管理ファイル更新は親担当が行う。
