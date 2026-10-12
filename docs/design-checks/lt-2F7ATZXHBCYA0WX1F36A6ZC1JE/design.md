# 設計検証 — worker が job の子の env に CLAUDE_PROJECT_DIR を置かない守り(依頼 lt-2F7ATZXHBCYA0WX1F36A6ZC1JE・card ki-222003f40484)

## この文書の目的

読み終えた人が次の 4 つを言えるようにする。

- doeff の worker の側で、会話の CLI の env に CLAUDE_PROJECT_DIR が届く道はどこにあり、どの module が何を守るか。
- 最初の守り(e5b5c54c7)を盲検 A・B がどう破ったか。
- それをどう直し、どう確かめたか(正常例・失敗例の実行記録)。
- 残る限界。

## 完了の範囲と commit

完了の範囲: **実装完了まで**。

| 段 | doeff の commit | 着地 |
|---|---|---|
| 最初の守り(検だけ) | e5b5c54c7ec3901925d5379aff5e85023857cd76 | L2164 |
| 盲検の前の主張・入力・盲検の返答 | 7253d8d64(branch `wt/design-check-lt-2f7a`) | この記録と同じ登記 |
| 盲検 A・B を受けた直し | 80e76d32a(同上) | 同上 |
| 品質検査の許可表・直した版の実行記録 | 93c938215(同上) | 同上 |

§5・§6 の実行記録は、載せ直す前の版(上の sha)で取った。登記で sha が変わっても中身は同じ。

書いた順:

1. e5b5c54c7 を着地させた(依頼の検)。完了の門が設計検証を求めたので、この記録を作った。
2. 盲検の前の主張を `design-pre-blind.md` に固定した(sha256 `041f88c0a2d6cdddf1f8236e2e79aafcef557a67bf249272b4149445873cc38c`・2026-10-12T03:23:35Z)。**主張は検を書いて着地させた後に書いた**(盲検の前ではあるが、実装の前ではない)。盲検の入力 `blind-input.md`(sha256 `28d9b1d455b0f755eb7d7fbe8de0fda1886ee03699cd70fafc06443d445a7549`)は、その主張の写しに、読む材料と実験の決まりを足した物。
3. 盲検 A・B を同時に起こした(2026-10-12T03:24:15Z)。
4. 基準の版 e5b5c54c7 で A・B の失敗例を再現した(`evidence/repro-on-e5b5c54c7.txt`)。
5. 直した(80e76d32a・93c938215)。
6. 直した版で正常例と失敗例を走らせた(`evidence/validate-on-fixed.txt`)。触った module の近くの検(`evidence/focused-tests-on-fixed.txt`)と品質検査(`evidence/code-quality-on-fixed.txt`)も走らせた。

## 1. 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | doeff の worker の雛形(Pod の env)に CLAUDE_PROJECT_DIR を足すと赤になる検が doeff main で緑(足した差分で赤を確かめる) | 受入の条 cf59e2a37 |
| R2 | doeff の worker の雛形と worker の code が、job の子(会話の CLI はその子孫)の env にこの名を書かない事を、doeff の側の検で守る | 依頼の「何をするか」 |
| R3 | 上に載る系の側の守り(agora-controllers 377464ce)とは別に、doeff の repo の中で閉じる | 依頼の所見 |

この名の意味は `design-pre-blind.md` §1 のとおり。Claude Code は hook と MCP の起動だけにこの名を置きます。上に載る系の Bash の包みは、この名の有無で両者を見分けます。そのため、CLI の env にこの名が在ると、Bash の道具の命令の下げ(SCHED_IDLE・nice)と出力の流しが、黙って外れます。

## 2. 責務(module)— 直した後

| id | 責務 | 持つ知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| worker-template | worker の Pod の宣言と機体ごとの差(`deploy/k8s/`) | 雛形が直に決める env・子へ渡す名の雛形の分 | 上に載る系の名と値 | ConfigMap の名・env の名 |
| worker-boot | worker の起動(`deploy/boot.sh`・Dockerfile) | 起動の env → 引数の写し | worker の内部 | env の名 |
| job-env-rules | 子の環境変数の名の約束と、宣言の env の検め(`shared/intent/runtime_env_model.hy`) | 予約の名と頭・秘密を名指す名・**worker が子へ置かない名 `WITHHELD-ENV-NAMES`**(直しで足した) | — | 定数と `EnvVar` の断り |
| child-env | job の子の env の組み方(`worker/core/launch.hy`) | 許可表・頭・重ねる順・REPLACE / EXTEND の分け・**重ねた後に `WITHHELD-ENV-NAMES` を外す** | shim・uv の argv | `JobLaunch` |
| upper-system | (doeff の外)ConfigMap worker-env・worker-shell の包み・job の宣言・CLI の env | この名の意味 | — | ConfigMap と宣言 |
| cli-launch | (doeff の別の package)doeff-agents・doeff-claude-code | CLI の起こし方 | — | session_env・process-env |
| guard | `tests/test_worker_pass_env.hy` の 2 本 | この名・名の置き場 | — | pytest |

直しの要点は 2 つです。

- 「子へ置かない名」の判断は job-env-rules の 1 つの定数に置く。名を綴るのもその 1 か所だけ。
- 外すのは child-env の 1 か所。全部の層を重ねた後で外す。

guard は「置き場の外が綴らない事」と「どの層から入れても子に届かない事」を見ます。

## 3. 子の env が組まれる道(実コード)

`design-pre-blind.md` §2 のとおり。盲検と自分の読みで足した事実は次の 2 つ。

- 待ちの子(warm_host)は、自分の env を許可表から組みます(`ReadEnvironment` に CHILD-ENV-ALLOWED と頭を渡す)。
- 分かれた子は、`worker/entry/warm_child.py:201` の `replace_environ` で `os.environ` を空にし、頼みの env(= `job-launch` の値)で置き換えます(`foundation/child_environ.hy:10-15`)。よって分かれた子の env は `job-launch` が決めます。

## 4. 盲検の前の主張(`design-pre-blind.md` §4 の写し — 書き換えていない)

| id | 軸 | 変更 | 主張 | 予想の範囲(変える) | 変えない |
|---|---|---|---|---|---|
| S-hw | hardware | 機体を足す(nodes/<新しい Node>/ に env の patch) | 機体の差は deploy/ の下の file だけで書き、検は deploy/ を再帰で読むので、検を変えずに新しい機体へ守りが及ぶ | worker-template | child-env・worker-boot・job-env-rules・guard |
| S-eff | effects | job の子へ新しい worker の env を渡す(WORKER_PASS_ENV か許可表に名を足す・許可表を頭で広げる) | 名を足すのは 1 か所で、この名を足す変更だけが赤(綴る形は文字の照合、頭で広げる形は振る舞いの照合が拒む)。ほかの名を足す変更は検に触れない | worker-template か child-env の 1 つ | guard・job-env-rules・worker-boot |
| S-dist | distribution | 機体の上で直に動く worker が同じ worker の code で job を起こす | 子の env の規則は child-env の 1 か所で、両方の worker が同じ `job-launch` を通る | なし(doeff の側) | 全部 |
| S-conc | concurrency | 子を起こす道を増やす(待ちの子・task の宿・probe) | どの宿も `job-launch` の env と env-mode をそのまま使い、宿は env を組まない | 宿の module だけ | child-env・guard |
| X-st | storage | 対象外 | 守る物は process の env で、保存しない | — | — |
| X-sim | simulation | 対象外 | 検は純粋な関数と file の文字だけを読む | — | — |

## 5. 盲検 A・B

- **起動の仕方**: Agent tool(subagent_type general-purpose)の新しい文脈(fork・resume なし)。A と B は 1 つの返事の中で同時に起こした。互いの返答も親の会話も渡していない。
- **要求した model**: `opus`(claude-opus-5-5)。**要求した effort**: `xhigh`(Agent tool の effort の引数で渡した)。
- **優先 1 の gpt-6-astra(low)**: この環境の `codex exec -m gpt-6-astra -c model_reasoning_effort=low` で 1 回試し、401 Unauthorized で起きなかった(`evidence/blind-model-probe-gpt-6-astra.log`)。Agent tool では gpt-6-astra を起こせない。
- **実際に応答した model と effort**: 観測できていない(要求値だけ)。
- **機体・profile**: agora の cluster の Pod(会話 c-X2MB5NMGKKVJWG1VD57144P5MN の作業場)。
- **入力**: `blind-input.md`。依頼文は skill design-change-validation の references/blind-counterexamples.md の A・B の文そのまま。それに、入力の path・「その dir の blind-input.md と design-pre-blind.md 以外の file、git の log・branch の題を読まない」・実験の物の置き場を足した。B には、提示した検査が §5 の表と守りの検である事を足した。
- **未加工の返答**: `blind-A-response.md`・`blind-B-response.md`。実験の差分・probe・log は `evidence/blind-A/`・`evidence/blind-B/` に写した(実験の木そのものは写していない)。

| | 識別子 | 所要 | 失敗例の要点 |
|---|---|---|---|
| A | abcb7952a4cf1daf9 | 1127 秒 | 宣言の側でこの名を断る(`EnvVar`)・子の env から外す、という**守りを強める**変更が、guard の文字の照合(src/ の全部)で赤になる。guard は「書かない」ではなく「doeff-cluster のどこもこの名を知らない」を強いていた |
| B | a4b011b332928b138 | 1517 秒 | `--pass-env` に `*`(宣言に置ける名を全部)を足す差分(`worker/entry/main.hy` だけ)が、guard を緑のまま通し、worker の env のこの名を REPLACE の子へ渡す。guard の振る舞いの照合は `child-environment` の base の層しか見ていなかった |

## 6. 再現と修正

### 6.1 基準の版で再現した(`evidence/repro-on-e5b5c54c7.txt`)

命令は `evidence/scripts/repro.sh` です。木は e5b5c54c7 の detached な作業木で、盲検の差分(`evidence/blind-A/diffs/`・`evidence/blind-B/failure.diff`)を当てて走らせ、戻しました。

| 失敗例 | 結果 | 成立 |
|---|---|---|
| 基準の版 | guard 1 passed。B の probe(雛形の名 + `*`)は worker の起動が止まる(`*` は名でないので ValueError) | — |
| A-Q1(`EnvVar` がこの名を断る) | 断りは効く。guard は `AssertionError: ['src/doeff_cluster/shared/intent/runtime_env_model.hy']` で赤 | 成立 |
| A-Q2(child-env がどの層からも外す) | guard が `['src/doeff_cluster/worker/core/launch.hy']` で赤 | 成立 |
| B(`--pass-env` の `*`) | guard 1 passed(緑)。probe: `env_mode replace`・`marker_in_child_env: true`・値 `/work`。雛形の名だけなら false | 成立 |

**分類**:

- A は、守りの検が隠すはずの知識に依っていた失敗です。名をどの module が知るかは、各 module の内部の事です。guard は src/ の全部の文字に依っていたので、守りを強める変更ほど guard の変更が要りました。
- B は、公開契約を侵す実装が検を通る穴です。侵された契約は「worker の env から REPLACE の子へ届く名は、許可表と名を綴った --pass-env だけ」です。共同の不変条件(この名が子に無い)が、child-env の 1 つの層でしか強制されていませんでした。

### 6.2 直し(80e76d32a・93c938215)

| 直し | 場所 | 訳 |
|---|---|---|
| `WITHHELD-ENV-NAMES`(この名 1 つ)を置く | `shared/intent/runtime_env_model.hy`(RESERVED-ENV-* の隣)・stub `runtime_env_model.pyi` に 1 行 | 「子の環境変数の名の約束」の置き場を 1 つにする。coordinator の `EnvVar` も worker の `child-environment` も読める intent の層(A-Q1 の多層防御を、guard を変えずに入れられる) |
| `child-environment` が 4 つの層を重ねた後で外す | `worker/core/launch.hy` | 許可表・--pass-env・宣言のどこを広げても子へ届かない(B を閉じる) |
| 木の job(EXTEND)の足す分からも外す | 同上 | EXTEND で継ぐ worker の env そのものは外せない(StartProcess に名を落とす口が無い)— 限界に書いた |
| 守りを 2 本にする | `tests/test_worker_pass_env.hy` | (1) 振る舞い: `child-environment` の 4 つの層それぞれ・`job-launch` の REPLACE と EXTEND(worker の文脈・宣言の environ・宣言の envVars・worker の env)。(2) 名の置き場: deploy/ は綴らない・src/ は置き場の 1 file だけが綴る(置き場は `runtime_env_model.__file__` から引く — 検は path を写さない) |
| 品質検査の許可表 | `.agents/code-quality.d/doeff-cluster-1541.json` の doeff-cluster-worker-launch に doeff-cluster-runtime-env-model を足す | 依存の宣言(下の層の型と定数を読む) |

stub について: `doeff_hy.static_stub --write --replace` を試すと、手書きの `runtime_env_model.pyi` の 180 行が書き換わりました。しかも NAME_PATTERN ほか 4 つが Incomplete になったので、使っていません。手書きの stub に 1 行を足しました。`launch.pyi` は道具の作った物なので、`--write` で作り直しました。

### 6.3 直した版の正常例と失敗例(`evidence/validate-on-fixed.txt`)

版 80e76d32a。命令は `evidence/scripts/validate.sh` です。走らせたのは guard 2 本を含む `test_worker_pass_env.hy` と `test_job_launch.hy`(8 本)です。変更を 1 つずつ当て、当てた分(`evidence/mutations/*.patch`)を逆当てで戻しました。

| id | 種類 | 変更 | 結果 |
|---|---|---|---|
| P0 | 正常 | なし | 8 passed |
| P-hw | 正常 | 機体の dir を足す(この名を書かない env の patch) | 8 passed |
| N-hw | 失敗 | 機体の dir を足す(この名を env に書く patch) | 赤 `['deploy/k8s/nodes/zz-probe/env.yaml']` |
| N-tmpl | 失敗 | worker.yaml の Pod の env にこの名 | 赤 `['deploy/k8s/worker/worker.yaml']` |
| N-boot | 失敗 | boot.sh で export | 赤 `['deploy/boot.sh']` |
| P-eff | 正常 | 許可表に別の名(XDG_CACHE_HOME) | 8 passed |
| P-eff2 | 正常 | 許可表を頭 `CLAUDE_` で広げる | 8 passed(この名は外される — 振る舞いの照合が緑のまま) |
| N-eff | 失敗 | `child-environment` の外しを抜く | 赤 `{'CLAUDE_PROJECT_DIR': '/work', 'PATH': '/bin'}` |
| N-conc | 失敗 | 木の job の足す分の外しを抜く | 赤 `(<EnvMode.EXTEND: 'extend'>, ['CLAUDE_PROJECT_DIR', …])` |
| N-own | 失敗 | 置き場の外の src(launch.hy)がこの名を綴る | 赤 `['src/doeff_cluster/worker/core/launch.hy']` |
| B | 正常(閉じた事) | 盲検 B の差分をそのまま当てる | 8 passed、かつ probe(雛形の名 + `*`)は `marker_in_child_env: false` |
| A-Q1 | 正常(多層防御が 1 module で入る) | `EnvVar` が `WITHHELD-ENV-NAMES` を読んで断る | 断りは効く(ほかの名は None)。8 passed |

検が読んだ `doeff_cluster` は、それぞれの作業木の src でした(`doeff_cluster.__path__` で確かめた — 基準の版は `doeff-at-e5b5c54c7`、直した版は `doeff-wt-design-check-lt-2f7a`)。触った module の近くの検(13 file)は 84 passed です(`evidence/focused-tests-on-fixed.txt`)。中身は、子の env・宣言の env の検め・stub の型の検・package の独立・probe の子・待ちの宿の検。品質検査(変更範囲・fast の段)は、許可表を足す前は `launch.hy:15: dependency-not-allowed` で failed、足した後は passed でした(`evidence/code-quality-on-fixed.txt`)。型検査はこの段では走っていません。

## 7. 予測と実際の比べ

| シナリオ | 盲検の前の予想 | 実際 | 差の訳 |
|---|---|---|---|
| S-hw | worker-template だけ | 予想どおり: 機体の dir を足すだけで、検を変えずに赤・緑が分かれた(P-hw・N-hw) | 差なし |
| S-eff | worker-template か child-env の 1 つ。頭で広げる形は振る舞いの照合が拒む | 直す前: 盲検 B が `passed-environment`(extra の層)を広げる形で検を通った。盲検 A は、守りを強める変更(job-env-rules・child-env)が guard を巻き込む事を示した。直した後: 名を足す・広げる変更は 1 module で入り(P-eff・P-eff2・B)、守りを強める変更も 1 module で入る(A-Q1) | 主張(c)は、検が base の層しか見ていなかったので誤り。A は、名を知る module を guard が src の全部の文字から推した漏れ |
| S-dist | doeff の側は変えない | 予想どおり。機体の worker も同じ `job-launch` を通り、振る舞いの照合が両方に及ぶ(P0・N-eff) | 差なし。ただし機体の worker の起動の env(systemd の unit)は doeff の外 |
| S-conc | 宿は env を組まない | 一部ずれ(盲検 A の読み・自分の確かめ): 待ちの宿は自分の env を許可表から組む。ただし分かれた子は頼みの env(`job-launch` の値)で丸ごと置き換わる(`warm_child.py:201`)ので、子の env は `job-launch` が決める。直した後は外しを抜くと赤(N-conc)、B の probe(process_host の順)は false | 主張の「宿は env を組まない」は言い過ぎ。結論の「子の env は child-env の 1 か所」は保たれた |

## 8. 強制の方法(直した後)

| 守る責務 | 強制の方法 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| 子の env にこの名が無い(REPLACE) | code: `child-environment` が全部の層を重ねた後で外す。検: 4 つの層それぞれから入れて外れる事 | `launch.hy`・`test-the-worker-withholds-the-hook-marker-from-every-layer-of-the-child-env` | 着地の門の焦点の検・日次の全体の検 | worker の版を上げた時に効く(今の機体の worker は前の版) |
| 子の env にこの名が無い(EXTEND) | code: 足す分から外す。検: 木の job の `job-launch` | 同上 | 同上 | 継ぐ worker の env そのものからは外せない。worker の env に置かない事は雛形の照合と上に載る系に頼る |
| 名の置き場は 1 つ | 検: deploy/ は綴らない・src/ は置き場の 1 file だけ | `test-only-the-child-env-name-rules-spell-the-hook-marker` | 同上 | 綴りを組み立てる形は文字の照合を通る。ただし子の env への書き込みは振る舞いの照合と code の外しが拒む |
| 依存の向き | 品質検査の module の許可表 | `.agents/code-quality.d/doeff-cluster-1541.json` | 変更範囲の品質検査(commit の hook・登記) | — |
| 宣言の env にこの名を置かない | (doeff では断らない — 外すだけ)上に載る系の検 377464ce | agora-controllers | 上に載る系の検 | 断る形(A-Q1)は 1 module の変更で入れられる事を確かめた。入れるかは上に載る系との決め(今は入れていない) |

型で表さなかった訳は `design-pre-blind.md` §5 のとおり。名の集合を型に上げると、上に載る系の名ごとに doeff の型が変わります。

## 9. 限界・確かめていない事

- worker の振る舞い(外す)が本番で効くのは、機体の worker の版(WORKER_DOEFF_COMMIT)を次に上げた時。今この名を worker の env・宣言に置く所は無い(2026-10-12 の doeff・agora・agora-controllers の main の grep・盲検 B の grep)ので、Pod の作り直しは頼まない。
- EXTEND の子が継ぐ worker の env からは外せない。会話の CLI は実行環境を持つ service の子(REPLACE)で起きる(前提 P1 — agora-controllers の宣言の読み・`deploy/environ/turns.json`)。
- doeff の外の道は、この検の範囲外。次の 3 つは doeff を通らずに CLI の env へ足されるので、この直しでは外れない。
  - 上に載る系の ConfigMap worker-env
  - 機体の worker の unit
  - CLI の env を組む上に載る系の code(agora-controllers `agent_runtime.hy`)と settings.json の env
- doeff-agents・doeff-claude-code(CLI を起こす package)がこの名を書かない事の検は無い(今この名を書く所は 0)。
- 盲検の実際の model と effort は観測できていない。
- 走らせていない検: doeff-cluster の全部の検・lint の全部・型検査(fast の段は撃たない)。触った module の近くの 13 file だけを走らせた。
- 盲検の前の主張は、検を書いて着地させた後に書いた。主張を書いた時点で、設計者は実装の形を知っていた。
