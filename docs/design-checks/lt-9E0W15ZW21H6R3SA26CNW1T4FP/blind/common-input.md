# 盲検の共通の材料(A・B に同じ物を渡す)

## 対象

- repo: doeff(Python と Hy の monorepo)。対象は package `packages/doeff-cluster`(task を worker へ置く coordinator と、系の宣言)と、`packages/doeff-hy/src/doeff_hy/system_form.hy`(系の宣言の macro defsystem の形の読み)。
- 対象のコードの版: doeff の commit 74ebb50b6c3c5de9a652088d11e16371d954abb1。
- 固定した要件・責務・公開契約・変更シナリオと主張・強制方法: 同じ dir の `../design-pre-blind.md`(§1〜§4b)。これを主張として読む。

## 読んでよい物(読むだけ)

あなたの実験の木(下の「実験の決まり」)の中の次の file。

| 責務 id(design-pre-blind.md §2) | file |
|---|---|
| group-declaration | `packages/doeff-cluster/src/doeff_cluster/shared/intent/service_model.hy`・`shared/entry/service_build.hy`・`packages/doeff-hy/src/doeff_hy/system_form.hy` |
| limit-rules | `packages/doeff-cluster/src/doeff_cluster/shared/core/detached_rules.hy`・`shared/intent/detached_model.hy`(GroupLimit・TaskLimit) |
| declare-writer | `shared/entry/declare.hy`・`shared/protocol/declaration_requests.hy`・`sim/local.hy` の `apply-declaration` |
| coordinator-resource | `coordinator/core/resource_policy.hy`・`coordinator/intent/cluster_model.hy`(ClusterState・GroupLimitView・GroupHeld・TaskUnplacedKind)・`coordinator/protocol/state_json.hy`・`coordinator/protocol/durable_kv.hy`・`coordinator/protocol/cluster_json.hy` |
| coordinator-placement | `coordinator/core/cluster_policy.hy`(limit-slot・limit-holdings・hold-limits・task-misfit・misfit-reason・limits-undeclared-refusal・place-tasks・submit-task)・`coordinator/core/detached_policy.hy`(submit-detached) |
| state-view | `coordinator/core/cluster_policy.hy`(group-limit-views・state-view)・`coordinator/protocol/replies.hy`(state-view-body の groupLimits) |
| 検 | `packages/doeff-cluster/tests/test_group_limits_declared.hy`・`tests/test_task_priority_limits.hy`・`tests/test_defsystem_form.hy` |

上の path は `packages/doeff-cluster/src/doeff_cluster/` からの相対(system_form.hy と検を除く)。repo の `AGENTS.md` は読んでよい。`docs/design-checks/` の下の他の file・この dir の他の file(design-pre-blind.md と、B なら blind-b-checks.md を除く)は読まない。

## 実験の決まり

- 実験はあなた専用の木だけで行う(下の path)。その木の file は変えてよい(実験の差分)。共有の checkout(`~/repos/doeff`)と他の木は変えない。git の commit・push・登記はしない。
- 環境は作ってある(木の根で `uv run pytest <file>` が走る)。走らせてよいのは、渡された検の file と、自分が足した検の file だけ(1 回 数分まで)。全数の検は走らせない。
- 他の会話への連絡・GitHub への書き込みはしない。
- 実験の差分と走らせた命令・出力は、木の中の `/tmp` でない所(例 木の根の `blind-artifacts/`)に置き、返答にその path を書く。
