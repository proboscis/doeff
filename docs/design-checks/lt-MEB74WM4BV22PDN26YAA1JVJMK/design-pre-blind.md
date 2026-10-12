# 盲検の前の主張 — lt-MEB74WM4BV22PDN26YAA1JVJMK(card ki-b884608b96e4)

## この文書の目的
盲検 A・B を頼む前に、設計者(会話 c-2AVPGRVP07N3MJ59B23MA57R43・claude-opus-5-5)が何を主張し、何で強制しているつもりかを固定する。
盲検の後の結論でこの文書を書き換えない。要件・責務・変更シナリオの主張は `blind-input.md`(盲検へ渡した材料)と同じ。

## 完了の範囲
実装完了まで。直しは doeff ac512fc1(L2209 で main に着地)。

## 強制方法(盲検の前)
| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界(盲検の前に分かっている物) |
|---|---|---|---|---|
| 分かれる道の env は判断(M-judge)が作り、宿(M-host)はそのまま運ぶ | 契約の検: 台本の答え手の下で宿が送る頼みの env を記録して値を比べる | `tests/test_warm_host.hy` の `fork-recorded`・`test-a-warm-key-task-asks-with-the-root-venv-that-uv-run-adds-on-the-cold-path` | pytest(変更した file の検・日次の全体の検) | 値を比べるので、宿が同じ値を自分で作っても通る(誰が作ったかは測らない) |
| uv-run-environ の写しが本物の uv と同じ | 本物の uv と待ちの子で 2 つの道を起こして入口が見た値を比べる | `tests/test_task_venv_on_both_paths.hy` | pytest | 比べるのは PATH と VIRTUAL_ENV だけ。uv と根の `.venv` が在る機体で走る |
| entry-env の形 | 純粋な判断の検 | `tests/test_job_launch.hy` | pytest | 字面の期待値 |
| 型 | `JobLaunch.entry-env : tuple[EnvEntry, ...]`・`ForkFromWarm.env : tuple[EnvEntry, ...]`・`.pyi` | `launch.hy`・`launch.pyi`・`warm_effects.hy` | defrecord の型の検め・pyright(日次) | 中身(どの名か)は型で表せない |
| 層 | MODULE-TAGS の役(judgment・protocol・main)と doeff-linter の層の規則 | `architecture.hy`・`[tool.doeff-linter]` | commit の hook | 層の規則は「どの層が env の値を決めるか」を名指さない |
| 待ちの子は env を持たない | 本物の待ちの子の検 | `tests/test_warm_child.py` | pytest | — |
