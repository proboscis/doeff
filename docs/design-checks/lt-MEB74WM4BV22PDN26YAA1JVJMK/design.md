# 設計検証 lt-MEB74WM4BV22PDN26YAA1JVJMK — task の子の環境を、待ちの子から分かれる道と冷えた道で揃える(card ki-b884608b96e4)

## この文書の目的
読み手が次の 3 つを判断できるようにする。
- 実行環境の task の子の環境(PATH・VIRTUAL_ENV)と interpreter の venv を 2 つの道で揃えた設計で、将来の変更がどこまで波及するか。
- 「同じ venv」と「分かれた子は頼みの env をそのまま置く」を、何が機械で強制しているか。
- 盲検 A・B の失敗例を受けて何を直したか。

## 完了の範囲
**実装完了まで**。経過は次のとおり。

| 段 | 版 | 中身 |
|---|---|---|
| 盲検の前の実装 | doeff ac512fc1(L2209 で main に着地) | `uv-run-environ`・`JobLaunch.entry-env`・宿が分かれる道に entry-env を送る・検 3 つ |
| 盲検の前の主張 | f8356ec98 | `design-pre-blind.md`(sha256 a3637170…)と盲検の材料 `blind-input.md`(sha256 6f54c259…) |
| 盲検の後の直し | この文書と同じ commit | 検 `test_task_venv_on_both_paths.hy` を強めた(interpreter の venv の一致・entry-env との完全一致・待ちの子の起こし方を本番の判断から)。製品の code は変えていない |

## 全体の図(直した後)

```
 worker(boot の venv で走る・PATH の頭 = boot の venv の bin)
   │ ReadEnvironment(許可表の名)
   ▼
 worker/core/launch.hy  job-launch                                   [M-judge]
   env       = 許可表 + 宣言 + DOEFF_*(VIRTUAL_ENV なし)
   entry-env = uv-run-environ(project-dir, env)
               = env + PATH の頭 <project>/.venv/bin + VIRTUAL_ENV <project>/.venv
   │                                         │
   │ warm-key なし(service・木の job)          │ warm-key あり(実行環境の task — warm_rules.forks-from-warm-child)
   ▼                                         ▼
 worker/protocol/process_host.hy start-job                           [M-host]
   StartProcess(argv, env)                     ForkFromWarm(entry-args, cwd, entry-env)
   │                                         │ unix socket
   ▼                                         ▼
 shim → uv run --project <project> → hy     待ちの子(warm_child.py)                          [M-forked]
   uv が PATH の頭と VIRTUAL_ENV を足す [M-uv]  ─ 起こしたのは warm_host が warm-launch の --project で  [M-rules・M-warm-host]
   interpreter = hy の shebang の venv          uv run した python(= root の venv)
   │                                         └ fork A: replace-environ(entry-env)・chdir → fork B
   ▼                                                                   ▼
 入口(PATH・VIRTUAL_ENV・sys.prefix)  ══ 同じ ══  入口(PATH・VIRTUAL_ENV・sys.prefix)
```

## 責務(直した後)

| id | 置き場 | 持つ判断・知識 | 口 | effects |
|---|---|---|---|---|
| M-judge | `worker/core/launch.hy` | job の子の起こし方・入口が走る環境(entry-env)・uv run が子へ足す 2 つの写し | `job-launch → JobLaunch`・`uv-run-environ` | なし(値だけの判断) |
| M-host | `worker/protocol/process_host.hy` | 道の選び(warm-key の在否)と汎用の効果への言い換え | StartJob の答え手 | 子の起動・file・ReadEnvironment |
| M-forked | `worker/entry/warm_child.py`・`foundation/child_environ.hy` | 頼みの env を A の環境としてそのまま置く | `WarmRequestWire` | os.environ の置き換え・fork |
| M-rules | `worker/core/warm_rules.hy` | どの job が分かれるか・待ちの子の起こし方(uv の --project) | `forks-from-warm-child`・`warm-launch`・`warm-child-argv` | なし |
| M-warm-host | `worker/protocol/warm_host.hy` | 待ちの子を許可表の env で uv run の下に起こす | StartWarmChild の答え手 | 子の起動 |
| M-venv-make | `worker/core/env_prepare.hy`・`worker/protocol/env_translation.hy`・`shared/core/runtime_env.hy`(project-dir) | venv を作る(`uv sync --project`)・venv の中を読む | 準備の効果 | uv・file |
| M-facts | `shared/protocol/runtime_facts.hy`(`marked-root`) | 入口の検めの材料(venv の上の root の完成の印) | `ReadRuntimeFacts` | file の読み |
| M-uv | 外(uv 0.12.19) | `--project P` → `<P を字面で正規化>/.venv` を PATH の頭と VIRTUAL_ENV に | 命令 | 子 process |

共同の不変条件(盲検 A を受けて書いた):
- **venv は 1 つ**: 待ちの子を起こす uv run の venv(M-rules の warm-launch → M-warm-host)・entry-env の VIRTUAL_ENV(M-judge)・冷えた道で uv が選ぶ venv(M-uv)が、物として同じ dir。3 か所はそれぞれ uv の既定(`<project>/.venv`)に頼る。
- **分かれた子は足さず引かない**: 分かれた子の入口が見る環境 = entry-env(M-forked は名を足さない・消さない)。

## 変更シナリオ — 盲検の前の主張と実測

| id | 軸 | 変更 | 盲検の前の主張(予測) | 実測 | 判定 |
|---|---|---|---|---|---|
| S1 | storage | venv を root の外の共有の置き場へ | 変わる = M-venv-make・M-judge | 変わる = M-venv-make・M-judge・M-rules・M-warm-host・M-facts(盲検 A の実測と読み) | 予測が狭かった。M-rules・M-warm-host は共同の不変条件「venv は 1 つ」の正当な相手(書き漏れ)。M-facts は「venv は root の下」という知識の漏れ(この直しより前からの形) |
| S2 | effects | 分かれる task にも uv の `UV` を見せる | 変わる = M-judge | 盲検 B: M-forked に置く実装が提示した検を全部通った。直した検はその実装を断る | 予測の範囲は保つ(正しい置き場は uv-run-environ)。検の穴を直した |
| S3 | effects | service も待ちの子から分ける(env の面) | 変わる = M-rules | entry-env は service にも作られる(実測)。ただし冷えた道の shim が渡す退きの知らせの fd(`DOEFF_WORKER_NOTICE_FD`)は分かれる道に無い(実測) | 予測が狭かった。service を分けるには知らせの中継(M-host・M-forked)も要る — fd は値ではなく shim が作る pipe で、M-judge の env の判断の外 |
| S4 | simulation | 模擬の答え手で分かれる道の env を確かめる | 変わる = なし | 台本の答え手の下で宿が送る頼みの env に root の venv が在る(実測)。宿が `plan.env` を送る変異は赤 | 予測どおり |
| — | hardware | 対象外 | Linux と macOS の venv の形(`bin/`・`:`)は同じ。待ちの子は fork を使い Windows を持たない | — | — |
| — | distribution | 対象外 | env は root と待ちの子を持つ worker 1 つの中で作る。coordinator は PATH・VIRTUAL_ENV を運ばない(予約の名) | — | — |
| — | concurrency | 対象外 | 分かれた A は自分の頼みの env を置くだけで、待ちの子は env を持たない | — | — |

## 強制方法(直した後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| venv は 1 つ(共同の不変条件) | 本物の uv と本物の待ちの子(本番の `warm-launch` の --project で起こす)で 2 つの道を起こし、入口の `sys.prefix` の実 path が 2 つの道で同じで VIRTUAL_ENV の実 path と同じ事を比べる。worker の PATH の頭に別の venv(boot の代わり)を置くので、待ちの子が root の venv の外で起きれば赤 | `tests/test_task_venv_on_both_paths.hy`・`tests/fixtures/warm_job.py`(`prefix`) | pytest(変更した file の検・日次の全体の検) | uv の既定に頼る 3 か所を 1 つの定義へ寄せてはいない(S1 を実装する時の仕事)。待ちの子の起動の cwd と preload は検の物に替える |
| 分かれた子は足さず引かない | 同じ検で、分かれた子の入口が見る環境と entry-env の完全一致 | 同上 | 同上 | 入口の module が自分で環境を変える物は対象外 |
| uv-run-environ = 本物の uv | 同じ検で、冷えた道の PATH の頭・VIRTUAL_ENV が uv の選ぶ venv(root の project は symlink — 綴りのまま)・分かれる道の PATH(全部)と VIRTUAL_ENV が冷えた道と同じ | 同上 | 同上 | 比べる名は PATH と VIRTUAL_ENV(他の名は「完全一致」の側で entry-env と比べる) |
| 宿は entry-env を運ぶ | 台本の答え手の下で宿が送る頼みの env を記録して比べる | `tests/test_warm_host.hy` | pytest | 宿が同じ値を自分で作っても通る(誰が作ったかは測らない) |
| entry-env の形・uv の正規化 | 純粋な判断の検 | `tests/test_job_launch.hy` | pytest | 字面の期待値 |
| 型 | `JobLaunch.entry-env : tuple[EnvEntry, ...]`・`ForkFromWarm.env`・`.pyi` | `launch.hy`・`launch.pyi` | defrecord の型の検め・pyright(日次) | 名の中身は型で表せない |

## 盲検 A・B と再現
起こし方・入力・識別子は `blind-meta.md`、返答は `blind-A-response.md`・`blind-B-response.md`(加工なし)。

| 失敗例 | 検を直す前 | 検を直した後 | 証拠 |
|---|---|---|---|
| A: 待ちの子が root の venv の外で起きる(変異 A′ = `warm-child-argv` が uv run を通さず名の python で起こす) | 通った(待ちの子の起こし方を検が手で組み、worker の PATH の頭が root の venv だった) | 赤: `sys.prefix の実 path … warm=/tmp/…/boot` | `evidence/mutant-A2-warm-child-without-uv.diff.txt`・`repro-A2-before-strengthening.log.txt`・`repro-A2-after-strengthening.log.txt` |
| A の確かめ: `warm-launch` が project を実 path に解く(同じ venv を別の綴りで指す) | 通った | 通った — 物として同じ venv なので違反ではない | `mutant-A-warm-launch-realpath.diff.txt`・`repro-A-before-strengthening.log.txt`・`repro-A-realpath-after-strengthening.log.txt` |
| A: M-facts の「venv は root の下」 | — | 盲検 A の実測(`marked-root` が root の外の venv から None)を記録。この直しの範囲外で、今の要件(venv は root の下)では正しい | `blind-A-response.md`・`evidence/blind-A-marked_root_probe.py.txt` |
| B: `replace-environ` が待ちの子の `UV`・`UV_RUN_RECURSION_DEPTH` を継ぐ | 通った | 赤: `頼みに無い名 ['UV', 'UV_RUN_RECURSION_DEPTH']` | `mutant-B-replace-environ-keeps-uv.diff.txt`・`repro-B-before-strengthening.log.txt`・`repro-B-after-strengthening.log.txt` |
| S4: 宿が `plan.env` を送る | — | 赤(頼みに VIRTUAL_ENV が無い — KeyError) | `mutant-S4-host-sends-plan-env.diff.txt`・`s4-S4-test_warm_host.log.txt`・`s4-none-test_warm_host.log.txt` |
| 正常例 | — | 緑(強めた検・test_warm_child 11 件・test_warm_host と test_job_launch 10 件) | `positive-after-strengthening.log.txt`・`positive-test_warm_child.log.txt`・`s4-none-test_warm_host.log.txt` |

## 予測と実際の差
- S1: 予測は「venv の場所を知るのは 2 つ」だったが、待ちの子の起こし方(M-rules・M-warm-host)も uv の既定に頼っていた。これは共同の不変条件で、検で縛った。入口の検め(M-facts)の「venv は root の下」は知識の漏れで、S1 を実装する時に変わる範囲として記す(今の要件では正しく、この card の範囲で直す物ではない)。
- S3: 予測は env の値だけを見ていた。退きの知らせの fd は shim が作る pipe の番号で、値の判断の外にある。service を分けるなら知らせの中継が要る。
- S2・S4: 予測どおり。S2 は検の穴(完全一致を見ていない)を盲検 B が示し、直した。

## 残る違い(測った)
冷えた道にだけ在る名は `DOEFF_WORKER_NOTICE_FD`(S3)・`UV`・`UV_RUN_RECURSION_DEPTH`(uv の印 — 分かれる task に見せるなら uv-run-environ に足す)と、検が足した `PYTHONDONTWRITEBYTECODE`。共通の名の値はすべて同じ(`evidence/env-names-diff.log.txt`)。
