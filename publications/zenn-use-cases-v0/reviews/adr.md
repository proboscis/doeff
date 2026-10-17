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
| 落とし穴 | `datetime.now(due.tzinfo)`へ書き換えた時の`deftest`は、走らせた日で変わる。UTCで10月30日までは通り、10月31日からは落ちる |
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
- 3通りの破り方は、記事に載せた終了コード（1・1・4）と出力の行（9行目・accepted ADR must have executable enforcement・集められていないファイルの名）になった。3つ目の変更では、普段のpytestが通った（1 passed）。1つ目の変更のpytestは走らせた日で変わるので、下の「11月以後の振る舞いへの追記」のとおり日時を固定して確かめた。
- 完全な例のディレクトリでもpytestが3 passedになり、記事の枠と中身が同じことを確かめた。
- 走らせた環境: Python 3.14.3・Hy 1.3.1・Semgrep 1.169.0。PyPIの版では試していない（2026-10-17の時点でdoeff-adrとdoeff-timeはPyPIに無い）。
- `defsemgrep`のテスト1つの所要は10.09秒（`pytest --durations=0`）。

外部のサービス・有料のAPI・実際のCIは使っていない。製品のコードは変えていない。

## 11月以後の振る舞いへの追記（2026-10-17・card ki-043780f30716の依頼lt-RD5AJB54D1AEARGB1ZWJM02KMM）

- 訳: 1つ目の破り方（`datetime.now(due.tzinfo)`）のpytestは、OSの時計がUTCで2026年10月31日0時を過ぎると、期限10月31日の`early`が`True`になって`deftest`が落ちる。10本目は11月より後に出るので、読者の手元では落ちる側が起きる。
- 旧本文の「10月31日までは通る」は、31日の当日にもう落ちる点で1日ずれていた。「10月30日までは通り、10月31日から落ちる」に直した。
- 本文: 落とし穴の節・「決まりを破ると、どこで止まるか」の出力・その後の文・図の説明・図`adr-flow`・参考を、日付で変わる事実に直した。落ちた時にpytestが指すのは`assert`の行で、`datetime.now()`の行ではない。指すのは`semgrep scan`だけ。
- 確かめの道具: `adr-check.py`は、実際の日付で分岐するのをやめた。`datetime.now()`が返す日時を、環境変数`ADR_CHECK_NOW`と`sitecustomize.py`（`PYTHONPATH`に置く）で固定し、期限の直前（10月30日23:59:59・3 passed）・直後（10月31日0:00:01・1 failed, 2 passed）・記事に載せた11月1日（同）の3通りで走らせる。いつ走らせても同じ結果になるので、日々の`verify_examples.py`が10月31日に急に落ちる事も無い。記事の出力の枠の行（`3 passed`・`E   AssertionError`・落ちた行の番号・`FAILED`の行・`1 failed, 2 passed`）と、枠に書いた日付が実際の出力にある事も照らす。
- 走らせた結果（いずれも終了コード0・「記事の6枠・3通りの破り方・完全な例」まで通った）:
  - `adr-check.py --pypi`をPython 3.12.14で（実際の日付は10月17日）。
  - 同じ道をPython 3.14.7で、外側の環境ごとOSの時計を2026年11月20日に固定して。
  - 日々の`verify_examples.py`と同じ道（引数に`~/repos/doeff`）で。
- 版: doeff・doeff-vm・doeff-core-effects・doeff-hy・doeff-hy-pytestはPyPIの0.6.0、doeff-timeとdoeff-adrはGitHub（`9adc4f8`）、Hy 1.3.1、Semgrep 1.181.0。
- 本文と図の検査: `render_release.py`（60字を越える文0・です・ます以外0・枠6・画像2）・`verify_comments.py`・`verify_publication.py`が通った。`ruff check`は`adr-check.py`で0件（変更前は`import datetime`のICN001が1件）。
- 図: `visuals/adr-flow.svg`の1行目の枠を「走らせた日で変わる／10月30日まで 3 passed／10月31日から 1 failed」に直し、`visuals/render_svg.mjs`（playwright 1.62.1のchromium）でPNGを撮り直した。下の注記は、実際の走らせ方（PyPIのdoeff 0.6.0・1行目のpytestは日時を固定）に合わせた。目視で、はみ出しと字形を確かめた。
