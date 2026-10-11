# B に渡す検査の実体・設定・検出範囲

対象の版 74ebb50b6 で、この変更に関わる検査は次の物。どれも木の根(doeff の根)で走らせる。

| 検査 | 命令 | 何を見るか(検出範囲) |
|---|---|---|
| 振る舞いの検(pytest) | `uv run pytest packages/doeff-cluster/tests/test_group_limits_declared.hy packages/doeff-cluster/tests/test_task_priority_limits.hy packages/doeff-cluster/tests/test_defsystem_form.hy -q` | 同じ組を名乗る 2 本が宣言の値で数えられる・宣言し直しで値が変わる・perPart の数え・受け口が宣言に無い組と値や範囲を送る名乗りと part の食い違いを 400 で断る・資源の口と系の構成子が誤った宣言を断る・sim の /state の groupLimits・保存の旧い形の行・defsystem の `:task-limits` の形 |
| semgrep | `uv run semgrep --metrics=off --disable-version-check --config .semgrep.yaml <変えた file …> --error` | repo の規則(層の境・doeff の書き方・汎用 package に業務の語を書かない など)。この変更専用の規則は無い |
| doeff-linter(doeff-cluster の設定で) | `sh scripts/lint-doeff-cluster.sh <変えた file の repo の根からの path …>` | doeff-cluster の定義の書き方の規則(warning は基点 `packages/doeff-cluster/lint-warning-baseline.json` と比べて増えたら赤)。注: この版でも cluster_policy.hy・resource_policy.hy の DOEFF172 は基点を 1 越えている(変更の前から) |
| code-quality の module の契約 | `.agents/code-quality.d/doeff-cluster-1541.json` の modules の allow(module ごとに読んでよい module の名) | import の向き(allow に無い module への依存) |

型の検査(pyright と Hy の型の検査)は、この検証では提示しない(未提示の検査として扱う)。
