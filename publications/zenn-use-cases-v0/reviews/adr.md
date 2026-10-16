# 設計の記録の記事のレビュー

## 担当と確認対象

- 担当: 記事の取り組みの依頼を受けた会話（Claude Opus 5.5）。連載の並べ方（案M）の10本目「設計の記録をテストとして走らせる」。
- 日付: 2026-10-17
- 記事: `publications/zenn-use-cases-v0/doeff-adr.md`（新しい回。元は`doeff-tooling.md`のADRの節と`examples/adr_example.hy`）
- 完全な例: `publications/zenn-use-cases-v0/examples/adr_as_test/`
- 確かめの道具: `publications/zenn-use-cases-v0/reviews/adr-check.py`
- 画像仕様: `publications/zenn-use-cases-v0/reviews/adr-visuals.json`

## 書いたこと

| 所 | 内容 |
| --- | --- |
| 冒頭 | 悩み1つ（ADRに書いた約束が、いつの間にかコードと食い違う） |
| 最初のコード | 1件のADR（訳・規則R1・性質と反例・`deftest`・`defsemgrep`）。pytestで3つのテストになる |
| 守られる側 | `GetTime`の依頼で時刻を頼む`is_overdue`。時計はdoeff-timeの`sim_time_handler` |
| 1つ目の検査 | 今日を10月17日に固定した時計で、TrueとFalseを返すことを試す`deftest` |
| 落とし穴 | `datetime.now(due.tzinfo)`へ書き換えても、`deftest`は10月31日まで通る |
| 2つ目の検査 | `.semgrep.yaml`の規則と、ADRの`defsemgrep`の検出すべき例・検出しない例 |
| 走らせる | doeffを取り寄せて`make sync`し、自分のprojectを`uv run --project`で走らせる |
| 3通りの破り方 | コードの変更・`:enforcement`の削除・`testpaths`の外し。止める所は`semgrep scan`・ADRの形のテスト・`verify-wiring` |
| doeffでの使い方 | 台帳`docs/adr/enforcement-ledger.json`から数えたADR43件・性質189・`deftest`115・`defsemgrep`51・規則282 |

時計は自作の依頼を作らず、doeff-timeの`GetTime`と`sim_time_handler`を使った。業務のコードにOSの時計を読む行が無いので、Semgrepの規則に除外のパスが要らない。

素の`datetime.now()`へ書き換えると、`deftest`は時間帯の無い日時と有る日時の比べで`TypeError`になり、偶然落ちる。読者が実際に書きそうな、時間帯を付けた`datetime.now(due.tzinfo)`を破り方の例にした。

## 実装根拠

- `packages/doeff-adr/src/doeff_adr/macros.hy`: `defadr`が`test_<ID>_adr_contract`を作る。`:enforcement`の中の`deftest`と`defsemgrep`を先に展開し、ADRへ登録する。
- `packages/doeff-adr/src/doeff_adr/registry.py`の`assert_adr_contract`: 採用済み（`accepted`）で検査が0個なら「accepted ADR must have executable enforcement」。
- `registry.py`の`_assert_installed_semgrep_enforcement`: 規則の名で`.semgrep.yaml`の規則を引き、検出すべき例で検出し、検出しない例で検出しないことを確かめる。`semgrep`が無ければ失敗する。
- `packages/doeff-adr/src/doeff_adr/pytest_plugin.py`: 既定の集める名前`defadr_*.hy`・`test_defadr_*.hy`。`verify-wiring`はrootdirの下の全てのADRを、pytestが集めた物と比べる。
- `packages/doeff-hy/src/doeff_hy/macros.hy`の`deftest`: `doeff_interpreter`のfixtureへProgramを渡す。どう動かすかは使う側が決める。
- `packages/doeff-time/src/doeff_time/handlers/sim_time.py`の`sim_time_handler`: `start_time`で開始時刻を決める。`scheduled`の内側に付ける。

完全な例はdoeffのリポジトリの中に置くため、ADRの名前を`adr_billing_clock.hy`にし、例の`pyproject.toml`の`doeff_adr_hy_files`で集める。`defadr_*.hy`のままだと、doeff自身の`verify-wiring`と`tests/test_adr_wiring_gate.py`が「集められていないADR」として落ちる。例の`pyproject.toml`に`[tool.ruff]`を置き、読者のprojectと同じく`doeff`を外の部品として並べる。

## 実行した確かめ

```sh
uv run python publications/zenn-use-cases-v0/reviews/adr-check.py  # 記事の6枠を一時dirのprojectに置き、手順と3通りの破り方を走らせる。
uv run python publications/zenn-use-cases-v0/reviews/adr-check.py <新しく取り出したdoeffの木>  # 読者と同じく、make syncしたmainの木の環境で同じ確かめを走らせる。
uv run python publications/zenn-use-cases-v0/render_release.py doeff-adr.md --output /tmp/doeff-adr.release.md  # 公開する本文の決まりの数を測る。
uv run python publications/zenn-use-cases-v0/verify_comments.py  # Pythonの枠と完全な例の各行に日本語の注釈があるかを見る。
uv run ruff check publications/zenn-use-cases-v0/examples/adr_as_test/ publications/zenn-use-cases-v0/reviews/adr-check.py  # 例と道具の静的検査。
```

- `adr-check.py`は、手順のpytest（3 passed・集めた本数3）・`semgrep scan`（終了コード0）・`verify-wiring`（終了コード0）を通した。
- 3通りの破り方は、記事に載せた終了コード（1・1・4）と出力の行（9行目・accepted ADR must have executable enforcement・集められていないファイルの名）になった。1つ目と3つ目の変更では、普段のpytestが通った（3 passed・1 passed）。
- 完全な例のディレクトリでもpytestが3 passedになり、記事の枠と中身が同じことを確かめた。
- 走らせた環境: Python 3.14.3・Hy 1.3.1・Semgrep 1.169.0。PyPIの版では試していない（2026-10-17の時点でdoeff-adrとdoeff-timeはPyPIに無い）。
- `defsemgrep`のテスト1つの所要は10.09秒（`pytest --durations=0`）。

外部のサービス・有料のAPI・実際のCIは使っていない。製品のコードは変えていない。
