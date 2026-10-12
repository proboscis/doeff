# 盲検の材料 — doeff-cluster の task の子の環境(待ちの子から分かれる道と冷えた道)

## 固定した要件(card ki-b884608b96e4 の受入)
1. 実行環境(runtime env)を宣言した task の子の PATH の先頭と VIRTUAL_ENV が、次の 2 つの道で同じになる事を、
   doeff-cluster の検(2 つの道を両方通す)で確かめて main で緑。
   - 冷えた道: worker が `shim -- uv run --no-sync --frozen --project <root の project> hy -m <入口> …` を起こす。
   - 待ちの子から分かれる道: root ごとに 1 つ常駐する「待ちの子」(module を読み込み済みの python)へ unix socket で頼み、
     待ちの子が fork した子 A が環境変数を頼みの env で丸ごと置き換え、さらに fork した子 B が読み込み済みの入口を同じ process で走らせる。
2. どちらの値に揃えたか(root の venv か worker の boot の venv か)と訳を書く。決めた値 = root の venv(冷えた道の値)。

## 利用状況
- worker は k8s の Pod の中の常駐 process。worker 自身は boot の venv(`/work/boot/roots/<sha>/.venv`)で走り、PATH の先頭がその bin。
- task の root = 宣言(repo・commit・uv の lock)から worker が作る dir。venv は `uv sync --project <root の project>` で `<project>/.venv` に作る。
- 名で `hy`・`python` を起こす task がある(子 process を PATH で引く)。

## 責務(module)と公開契約
| id | 置き場 | 持つ判断・知識 | 口(公開契約) | effects |
|---|---|---|---|---|
| M-judge | `packages/doeff-cluster/src/doeff_cluster/worker/core/launch.hy` | job の子の起こし方(argv・cwd・env・env-mode)・入口が走る環境(entry-env)・uv run が子へ足す 2 つの写し(uv-run-environ) | `job-launch` → `JobLaunch`(argv・cwd・env・env-mode・work-dir・last-used・entry-args・entry-env)・`uv-run-environ(project-dir, env) -> dict` | なし(値だけから決める判断) |
| M-host | `…/worker/protocol/process_host.hy` の `start-job` | 道の選び(判断が付けた warm-key の在否)・汎用の効果への言い換え | StartJob の答え手。冷えた道 = `StartProcess(:env plan.env :env-mode plan.env-mode)`・分かれる道 = `ForkFromWarm(:env plan.entry-env :args plan.entry-args :cwd plan.cwd)` | MakeDirectory・WriteText・RemoveTree・ReadEnvironment・StartProcess・ForkFromWarm・slog |
| M-forked | `…/worker/entry/warm_child.py`(`run_forked`)と `…/foundation/child_environ.hy`(`replace-environ`) | 頼みの env を A の環境としてそのまま置く(足さない・引かない) | 頼みの 1 行 `WarmRequestWire`(doeff_core_effects.os_warm_process)の env | os.environ の置き換え・chdir・fork |
| M-rules | `…/worker/core/warm_rules.hy` | どの job が待ちの子から分かれるか(`forks-from-warm-child` = 実行環境を宣言した task)・分かれ元のキー・待ちの子の起動の argv | `forks-from-warm-child`・`warm-key-of`・`warm-child-argv` | なし |
| M-venv-make | `…/worker/core/env_prepare.hy`・`…/worker/protocol/env_translation.hy` | root の venv を作る(`uv sync --project`)・venv の中を読む(site-packages・python) | 実行環境の準備の効果 | uv の命令・file の読み書き |
| M-uv | 外(uv 0.12.19) | `uv run --project P` は子の PATH の頭に `<P を字面で正規化>/.venv/bin` を足し、`VIRTUAL_ENV=<…>/.venv` を置く(他に `UV`・`UV_RUN_RECURSION_DEPTH`) | 命令 | 子 process |

不変条件:
- 実行環境の job では `entry-env == uv-run-environ(project-dir, env)`。冷えた道の `env` は VIRTUAL_ENV を持たない(uv が足す)。
- 木の job(実行環境を持たない job)の `entry-env` は空(待ちの子から分かれない)。
- 待ちの子そのものは env の値を持たない(頼みの env は A の中でだけ置く)。

## 将来の変更についての主張(盲検の前に固定)
| id | 軸 | 変更 | 主張 | 予測の範囲(変わる) | 変わらない |
|---|---|---|---|---|---|
| S1 | storage | root の venv を root の外の共有の置き場へ移す(例 `UV_PROJECT_ENVIRONMENT=<置き場>/<キー>` で uv sync と uv run を揃える) | venv の場所を知るのは M-venv-make(作る・読む)と M-judge(冷えた道の env に uv への指定を足す・uv-run-environ の venv の決まり)だけ | M-venv-make・M-judge | M-host・M-forked・M-rules |
| S2 | effects | 分かれる task にも uv の足す `UV` を見せる(task が `$UV` で uv を呼ぶ) | uv の足し方の写しは uv-run-environ 1 か所 | M-judge | M-host・M-forked・M-rules・M-venv-make |
| S3 | effects | service も待ちの子から分ける(`forks-from-warm-child` を広げる) — env の面の変更 | entry-env は実行環境の job 全部に作るので、env の面で変わるのは道の選びだけ | M-rules | M-judge・M-host・M-forked |
| S4 | simulation | 模擬の宿(I/O の無い答え手)で分かれる道の task の env を確かめる | env は純粋な判断が作り ForkFromWarm の欄で運ぶので、模擬の答え手は env の規則を持たない | なし(答え手を差し替えるだけ) | M-judge・M-host・M-forked |
| — | hardware | 対象外 — Linux と macOS の worker は venv の形(`bin/`・区切り `:`)が同じ。fork を使う待ちの子は Windows を範囲に持たない | | | |
| — | distribution | 対象外 — env は root と待ちの子を持つ worker 1 つが自分の中で作る。coordinator は PATH・VIRTUAL_ENV を運ばない(宣言で予約の名) | | | |
| — | concurrency | 対象外 — 分かれた A は fork の後に自分の頼みの env を置くだけで、待ちの子は env を持たない。同時の頼みは状態を分け合わない | | | |

## 検査(実体と設定・検出範囲・実行の命令)
いずれも doeff の根で `uv run pytest <path>` で走る(日次の全体の検と、変更した file の検)。commit の hook は semgrep・doeff-linter(`scripts/lint-doeff-cluster.sh` — `packages/doeff-cluster/pyproject.toml` の `[tool.doeff-linter]` と `architecture.hy`)を当てる。
- `packages/doeff-cluster/tests/test_job_launch.hy`: job-launch の答え(entry-env = env + root の venv の 2 つ・木の job は空)と uv-run-environ の正規化。
- `packages/doeff-cluster/tests/test_warm_host.hy` の `test-a-warm-key-task-asks-with-the-root-venv-that-uv-run-adds-on-the-cold-path`: 台本の答え手の下で process_host が待ちの子へ送る頼みの env を記録し、PATH と VIRTUAL_ENV が root の venv である事。
- `packages/doeff-cluster/tests/test_task_venv_on_both_paths.hy`: 1 つの job-launch の答えから、本物の uv の冷えた道と、本物の待ちの子(宿 warm_host が起こす)への頼み(`plan.entry-env`)で入口 `tests/fixtures/warm_job.py` の inspect を走らせ、入口が見た PATH(全部)と VIRTUAL_ENV が同じ事。
- `packages/doeff-cluster/tests/test_warm_child.py`: 待ちの子は頼みの env を持たない・分かれた子の env は頼みの env。

## 読める実コード(読み取りだけ・変更しない)
作業木 `/home/ubuntu/.worktrees/doeff-wt-ki-b884-design-check`(doeff main d6ebc3db9 — 直しの commit ac512fc1 を含む)。上の path は全部この木の中。
