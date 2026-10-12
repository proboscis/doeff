# 盲検の前の主張 — worker の側で CLAUDE_PROJECT_DIR を job の子の env に置かない守り(依頼 lt-2F7ATZXHBCYA0WX1F36A6ZC1JE・card ki-222003f40484)

この file は盲検 A・B を起こす前に固定した、設計者の主張と強制の方法。盲検の後は書き換えない(結果と直しは design.md)。

## 0. 完了の範囲と版

- 完了の範囲: **実装完了まで**(検は doeff main e5b5c54c7ec3901925d5379aff5e85023857cd76 に在る・L2164)。
- 設計の基準の版: fe951a108277a99761b2c888958b827d4eebfc82(検を足す前の main)。
- 検を足した版: e5b5c54c7(差分は `packages/doeff-cluster/tests/test_worker_pass_env.hy` の +20 行だけ)。

## 1. 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | doeff の worker の雛形(Pod の env)に CLAUDE_PROJECT_DIR を足すと赤になる検が doeff main で緑(足した差分で赤を確かめる) | 受入の条 cf59e2a37 |
| R2 | doeff の worker の雛形と worker の code が、job の子(会話の CLI はその子孫)の env にこの名を書かない事を、doeff の側の検で守る | 依頼の「何をするか」 |
| R3 | 上に載る系の側の守り(agora-controllers 377464ce の `test-no-declared-value-or-controller-puts-the-wrappers-hook-marker-into-the-cli-env`)とは別に、doeff の repo の中で閉じる | 依頼の所見(上に載る系の守りは doeff を見ていない) |

この名の意味: Claude Code は hook と MCP の server を起こす時だけ env に CLAUDE_PROJECT_DIR を置き、Bash の道具の命令には置かない。上に載る系の
Bash の包み(ConfigMap worker-shell の tool-stream-shell.sh・`CLAUDE_CODE_SHELL_PREFIX` で全部の shell の命令を包む)はこの名の有無で
「hook・MCP の起動」と「Bash の道具の命令」を見分け、無い時だけ命令を SCHED_IDLE・nice で下げて出力を画面へ流す。CLI の env にこの名が
在ると、Bash の道具の命令も hook と見なされ、下げと流しが黙って外れる。

## 2. 会話の CLI の env が組まれる道(e5b5c54c7 の時点の実コード)

1. worker の Pod の env = 雛形 `deploy/k8s/worker/worker.yaml` の `env`(+ 機体の dir `deploy/k8s/nodes/<Node>/` の patch)+ `envFrom` の
   ConfigMap worker-env(上に載る系)+ image の ENV(`deploy/Dockerfile`・`deploy/base/Dockerfile`)。
2. `deploy/boot.sh` が worker を起こす(`--pass-env "${WORKER_PASS_ENV:-}"` ほか)。
3. worker が job の子を起こす: `src/doeff_cluster/worker/core/launch.hy` の `job-launch` が起こし方(argv・env・env-mode)を決める。
   - 実行環境(runtime env)を持つ job: `child-environment` = worker の env のうち許可表 `CHILD-ENV-ALLOWED` の名と頭 `CHILD-ENV-PREFIXES`(`LC_`)
     → `--pass-env` で名指した worker の env と worker の文脈(extra)→ 宣言の env-vars と environ(declared)→ worker が組む DOEFF_*(worker)。
     `EnvMode.REPLACE`(子は worker の env を継がない)。
   - 実行環境を持たない job: worker の env を丸ごと継ぎ(`EnvMode.EXTEND`)、extra・environ・PYTHONPATH・DOEFF_* を足す。
   - 子を起こす宿(`worker/protocol/process_host.hy`・待ちの子の ForkFromWarm・`worker/protocol/probes.hy`)は `job-launch`(probe は
     `probe_rules.hy` の同じ規則)の env と env-mode をそのまま使う。
4. 会話の CLI は上に載る系の service `turn-host-<機体>` の子。この service の宣言は実行環境を持つ(→ REPLACE)。CLI の env は、その job の
   process の env から上に載る系の code(agora-controllers `controllers/foundation/agent_runtime.hy`)と doeff-agents・doeff-claude-code が
   組み、settings.json の env(上に載る系の宣言)が足される。
5. 宣言の env-vars・environ の名は `shared/intent/runtime_env_model.hy` の `EnvVar` が検める(予約の頭 PYTHON・HY_・UV_・LD_・DOEFF_ と
   名 PATH・HOME・VIRTUAL_ENV を断る)。この名は予約に無いので通る。

## 3. 責務(module)

| id | 責務 | 持つ知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| worker-template | worker の Pod の宣言(env・envFrom・WORKER_PASS_ENV の雛形の分・mount)と機体ごとの差 | 雛形が直に決める env の名と値・子へ渡す名の雛形の分 | 上に載る系の名と値(ConfigMap を名で参照するだけ) | ConfigMap の名 worker-env・worker-prepare・worker-shell・env の名 WORKER_PASS_ENV_EXTRA・WORKER_PROVIDES_EXTRA |
| worker-boot | worker の起動(boot.sh・image) | 起動の env → worker の引数の写し | worker の内部 | env の名(WORKER_*)・`/opt/worker-boot.sh` |
| child-env | job の子の env の組み方(`job-launch`・`child-environment`・`passed-environment`・`worker-context-environ`) | 許可表・頭・重ねる順・REPLACE / EXTEND の分け | 子の起こし方(shim・uv の argv) | `JobLaunch`(argv・cwd・env・env-mode) |
| job-env-rules | 宣言の env-vars・environ の名と値の検め(`EnvVar`・coordinator の `environ-refusal`) | 予約の名と頭・秘密を名指す名の断り | — | 断りの文(受けられれば None) |
| upper-system | (doeff の外)ConfigMap worker-env・worker-shell の包み・job の宣言(turn-host の runtimeEnv・environ)・CLI の env と settings.json の env | この名の意味(包みの見分けの鍵) | — | ConfigMap と宣言 |
| cli-launch | (doeff の別の package)doeff-agents の headless の adapter・doeff-claude-code の process-env | CLI の起こし方 | — | session_env・process-env |
| guard | doeff-cluster の検 `test-no-template-or-worker-code-hands-the-hook-marker-to-the-child` | この名・見る dir | — | pytest の 1 本 |

共同の不変条件: Bash の道具の命令を走らせる CLI の env に CLAUDE_PROJECT_DIR が無い。意味の持ち主は upper-system の包み。env を書く全部の
module(worker-template・worker-boot・child-env・upper-system・cli-launch)がこの名を書かない事で守る。この依頼の範囲は doeff-cluster の
worker-template・worker-boot・child-env。

## 4. 変更シナリオと主張(盲検の前)

| id | 軸 | 変更 | 主張 | 予想の範囲(変える) | 変えない |
|---|---|---|---|---|---|
| S-hw | hardware | 機体を足す: `deploy/k8s/nodes/<新しい Node>/` に node.yaml・version.yaml を置き、env の patch で受ける数・能力・WORKER_PASS_ENV の差(atlas の RUSTUP_HOME の様に)を足す | 機体の差は deploy/ の下の file だけで書き、検は deploy/ を再帰で読むので、検を変えずに新しい機体へ守りが及ぶ | worker-template | child-env・worker-boot・job-env-rules・guard |
| S-eff | effects | job の子へ新しい worker の env を渡す(機体の事実や道具の置き場を WORKER_PASS_ENV の雛形の分か CHILD-ENV-ALLOWED に足す・許可表を頭で広げる) | 子へ渡す名は worker-template(WORKER_PASS_ENV)か child-env(許可表と頭)の 1 か所で足し、この名を足す変更だけが赤になる(綴る形は文字の照合、頭で広げる形は振る舞いの照合が拒む)。ほかの名を足す変更は検に触れない | worker-template か child-env の 1 つ | guard・job-env-rules・worker-boot |
| S-dist | distribution | 機体の上で直に動く worker(Pod の外・systemd — zeus-native・mac-native)が同じ worker の code で job を起こす | 子の env の規則は child-env の 1 か所で、k8s の worker と機体の worker が同じ `job-launch` を通るので、振る舞いの照合が両方に及ぶ。機体の worker の起動の env(unit)は doeff の外 | なし(doeff の側) | worker-template・worker-boot・child-env・guard |
| S-conc | concurrency | 子を起こす道を増やす(待ちの子から分ける・task を分ける宿・probe を足す) | どの宿も `job-launch` の env と env-mode をそのまま使い、宿は env を組まないので、新しい道も子の env の規則を child-env の 1 か所から受ける | 宿の module(worker/protocol)だけ | child-env・guard |
| X-st | storage | 対象外 | 守る物は process の env で、保存しない。宣言の env-vars・environ は coordinator の状態に残るが、その名を書くのは上に載る系の宣言(前提 P2) | — | — |
| X-sim | simulation | 対象外 | 検は純粋な関数(`child-environment`)と file の文字だけを読み、時間・process・模擬の世界に依らない。模擬の宿(sim)は会話の CLI を起こさない | — | — |

## 5. 強制の方法

| 守る責務 | 強制の方法 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| worker-template・worker-boot がこの名を書かない | 文字の照合: `deploy/` と `src/` の下の file(`__pycache__` を除く)の中身にこの綴りが無い | `packages/doeff-cluster/tests/test_worker_pass_env.hy` の `test-no-template-or-worker-code-hands-the-hook-marker-to-the-child` | pytest(着地の門の焦点の検・日次の全体の検) | 綴りを組み立てる形(`"CLAUDE_" + …`)は拾わない。上に載る系の ConfigMap worker-env と image の実物(tag)は見ない |
| child-env の許可表がこの名を子へ渡さない | 振る舞いの照合: worker の env にこの名と PATH が在る時、`child-environment` の答えが `{"PATH" …}` だけ | 同じ deftest | 同上 | REPLACE の道(実行環境を持つ job)だけ。EXTEND の道(実行環境を持たない job)は worker の env を丸ごと継ぐので、worker の env にこの名が無い事(雛形の文字の照合と上に載る系)に頼る |
| 宣言の env にこの名を置かない | (doeff では強制しない)上に載る系の検 377464ce | agora-controllers | 上に載る系の検 | doeff の `EnvVar` はこの名を断らない(前提 P2) |
| CLI を起こす package がこの名を書かない | (この依頼の範囲外)doeff-agents・doeff-claude-code | — | — | 今この名を書く所は無い(2026-10-12 の git grep)が、検は無い |

型で表さなかった訳: 子の env は名 → 文字列の dict で、名の集合を型に上げると、上に載る系が足す名(WORKER_PASS_ENV_EXTRA)ごとに doeff の型を
変える事になり、雛形が上に載る系の名を知らない約束(worker.yaml の頭の註)を破る。禁じる名は 1 つで、文字と振る舞いの照合で足りると判じた。

## 6. 前提

- P1: 会話の CLI は実行環境を持つ service の子(REPLACE の道)で起きる(2026-10-12 の agora-controllers の宣言の読み)。
- P2: 宣言の env-vars・environ と ConfigMap worker-env は上に載る系が書き、上に載る系の検が守る。doeff は検めて運ぶだけ。
- P3: 子を起こす宿は `job-launch`(と probe の同じ規則)の env と env-mode を変えずに使う。
