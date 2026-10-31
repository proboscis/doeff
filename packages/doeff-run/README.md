# doeff-run

「image を、この設定(命令・環境変数・volume・資源の上限)で走らせる」ための doeff の effect と型と、memory の上で答える模擬(fake)の handler。

利用者の Program は下の effect だけを使う。走らせる先が docker か process か k3s かは、composition root が選ぶ handler で決まる。
container・Pod・process の id は handler の内側に閉じ、Program が持つ身元は取っ手(`RunHandle`)だけ。ここに在るのは effect と型と、
fake の handler と docker の handler と process の handler と、全部の handler が通す契約の検(`tests/test_contract.hy`)。k3s の handler は別の変更で足す。

effect と型と fake は入口 `doeff_run` から読む(`from doeff_run import RunStart, RunSpec` / `(import doeff_run [RunStart RunSpec])`)。
中身は名に `_` の付いた module に在り、submodule の path は読まない(ADR-DOE-API-001 R5)。

## 公開 effect

| effect | 入力 | 答え |
|---|---|---|
| `RunStart(name, spec)` | run の名(小文字・数字・`-` の 1〜63 字 — docker の container 名と k8s の資源名の両方が受ける形)・設定 `RunSpec` | `RunAccepted(handle)`・`RunNameInUse(handle)`(その名を使っている run の取っ手)・`RunRefused(name, why, detail)`(空きが無い `NO_CAPACITY`・handler が守れない設定 `UNSUPPORTED`) |
| `RunStop(handle, grace_seconds)` | 取っ手・合図から強制で止めるまでの猶予 | `RunStopped`・`RunGone`・`StopFailed(handle, detail)`。冪等 |
| `RunRemove(handle)` | 取っ手 | `RunGone`・`RunStillAlive(handle)`(止まっていない run は片付けない)。volume は消さない |
| `RunStatus(handle)` | 取っ手 | `RunPending`・`RunLive`・`RunStopped`・`RunGone` |
| `RunReadEvents(handle, after_seq, wait_up_to)` | 取っ手・この番号より後・待つ秒の上限 | `RunEventPage(events, next_seq, state)` |
| `VolumeEnsure(name)` | volume の名(`VolumeMount.source` に書く名) | `VolumeCreated(name)`(この頼みで作った)・`VolumeExisted(name)`(頼まれる前から在った)・`VolumeRefused(name, why, detail)`(作れなかった — 名が backend の規則に合わない・この handler は volume を持たない `UNSUPPORTED`、backend が作らなかった・作ったと言うが見えない `NO_CAPACITY`) |
| `VolumeStatus(name)` | volume の名 | `VolumePresent(name)`(在る)・`VolumeAbsent(name)`(無い)・`VolumeRefused(name, why, detail)`(`UNSUPPORTED` — 名が backend の規則に合わない・この handler は volume を持たない) |

- 受け付けは「走り始めた」ではない。起きたことは出来事 `RunLive` で、起きずに止まったことは `RunStopped(cause=NEVER_BEGAN)` で届く。
- 失敗は例外ではなく答えの型で返す。取っ手の run を handler が知らない時(片付けた・消えた・覚えが無い)は、どの effect も「消えた」として答える。
- 出来事 `RunEvent(seq, at, state)` は run の中で 1 から欠けずに増え、読みは新しい出来事が起きるか上限の秒が過ぎるまで待つ。
- 状態は `RunPending`(受け付けた)→ `RunLive`(起きた)→ `RunStopped(exit_code, cause, detail)`(止まった — `cause` は `EXITED`・`REQUESTED`・`OUT_OF_MEMORY`・`NEVER_BEGAN`)。
  `RunGone` は backend から消えた run で、跡(終了 code)は何も残らない。

### volume の効果

volume は run の寿命より長い(run を止めても `RunRemove` で片付けても残る)。**volume を消す効果は無く、足さない**(worktree は消さない。消すのはマージを知る側が別に決める)。

- `RunStart` は volume を作らない。`RunSpec.volumes` が指す volume が backend に無ければ、container を作らずに受け付け、`RunStopped(cause=NEVER_BEGAN)` で止める
  (docker は無い named volume を黙って作るので、typo で空の volume が立つ事故を防ぐ)。volume は先に `VolumeEnsure` で作る。
- `VolumeEnsure` は無ければ作り、在れば在るまま(中身を作り直さない)。同じ名の作成が重なっても volume は 1 つで、`VolumeCreated` と答えるのは作った 1 つだけ(残りは `VolumeExisted`)。
  作ったと答えたなら、`VolumeStatus` は `VolumePresent` と答え、その volume を指す run が起きる(backend が成功と言っても、問い直して見えなければ `VolumeRefused(NO_CAPACITY)`)。
- `VolumeStatus` は問うだけで作らない。消えた volume は `VolumeAbsent`(空で作り直さない)。
- 名が backend の規則に合わなければ(docker の named volume の名: 英数字で始まる 2 字以上の英数字と `_` `.` `-`。path・空白・`,` は不可)、backend を呼ぶ前に `VolumeRefused(UNSUPPORTED)` で断る。問いも同じ。
- volume を持たない handler(process)は、作る効果も問う効果も `VolumeRefused(UNSUPPORTED)` で断る(「無い」と答えると、置き場が消えた事と見分けがつかない)。
- 使い分けは呼び手が決める: 会話の最初の「開く」だけが `VolumeEnsure` を撃つ。開き直しは先に `VolumeStatus` で確かめ、`VolumeAbsent` なら作らずに「置き場が消えた」と断る
  (消えた volume へ黙って戻って空になる事故を防ぐ。消えた volume に `VolumeEnsure` を撃つと、空の volume を作って `VolumeCreated` と答えるので、呼び手が見分けられる)。
  最初か開き直しかを handler は覚えない — 呼び手が効果を選ぶ。

## 設定の型

`RunSpec(image, command=(), env=(空), volumes=(), limits=ResourceLimits())` — `env` は凍らせた写像で、資格を運びうるので repr に出さず断りの文にも値を写さない。
`VolumeMount(source, target, read_only=False)`・`ResourceLimits(cpu_millis=None, memory_bytes=None)`。型は作る時に誤り(shell の名でない環境変数・重なる volume の
target・正でない上限)を断るので、本物の handler は検めずに値を信じて backend の呼びへ訳せる。

## fake の handler

```hy
(import doeff_run [FakeBehavior FakeRunWorld fake-run-handler])
(import doeff_time [SimClock sim-time-handler])

(setv world (FakeRunWorld :behavior-of (fn [spec] (FakeBehavior :runs-for 2.0 :exit-code 7))))
(with_handlers [(sim-time-handler :clock (SimClock)) (fake-run-handler world)] program)
```

状態の変わり目は仮想の時計(doeff-time)の刻で決まるので、sim-time の下では待ちも一瞬で進む。`FakeBehavior` は起きるまでの秒・自分で終わるまでの秒と終了 code・
メモリで殺されるか・止めの合図への応じ方・起きずに止まる訳を決め、`FakeRunWorld` は RunSpec から振る舞いを引く関数と、同時に持てる数の上限(`capacity`)と、
守れない設定を断る関数(`unsupported`)を持つ。

検の口として effect `RunVanish(handle)` がある(公開 effect ではない)— run を handler の知らない所で消す。

volume は世界が名の集まりで持つ(`FakeRunWorld.volumes`)。`VolumeEnsure` が足し、消す効果は無いので `RunStop`・`RunRemove` の後も残る。
docker と同じく、世界に無い volume を指す `RunStart` は volume を作らず、受け付けた直後に `NEVER_BEGAN` で止まる(訳に volume の名)。
消えた volume の検は、世界の `volumes` から名を外して作る。

## docker の handler

```hy
(import doeff_core_effects [scheduled offloaded-subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_run [DockerRunHost docker-run-handler])

(run (scheduled (with_handlers [(sync-time-handler) offloaded-subprocess-handler (docker-run-handler (DockerRunHost))] program)))
```

docker の在る機体(zeus)で本物の container を走らせる handler(本番は k3s)。run 1 つ = container 1 つで、container の名 = run の名、
取っ手の run-id は container の label `doeff-run.run-id` に置く。状態と出来事は毎回 `docker container inspect` から引き(起きた刻 `StartedAt`
に `RunLive`・止まった刻 `FinishedAt` に `RunStopped`)、読みの待ちは `docker events`(その container の start・die・destroy)の最初の 1 行で
起きる。子 process は `RunProcess` で出すので、外側に `offloaded-subprocess-handler` と `scheduled` が要る。

- env の値は命令の引数に載せず(ps に写る)、docker の CLI の環境変数で渡して `--env 名` で container へ写す。
- メモリの上限は swap も同じ値にする(上限を越えた run は swap へ逃げずに `OUT_OF_MEMORY` で止まる)。
- 断る設定(`UNSUPPORTED`): named volume の名でない volume の source(docker は path を bind mount に読む)・`,` を含む target・docker の
  下限 6 MiB より小さいメモリの上限・doeff-run の作っていない container が持つ名。
- 起きなかった run(`NEVER_BEGAN`): 取り寄せられない image と無い volume は container を作らずに受け付けて覚え、命令の無い container は
  docker が残す `State.Error` から読む。
- volume: `VolumeEnsure` は `docker volume inspect` で在るかを見て(在れば create を撃たない)、無ければ label(`doeff-run.host` = host の印・`doeff-run.volume-token` =
  この作成だけの印)つきで `docker volume create` し、問い直す。見えなければ作れなかった(`NO_CAPACITY`)・印が自分の物なら作った・他の作り手の物なら在った
  (docker は在る名への create を黙って受けるので、重なった作成のうち作ったと答えるのは 1 つだけ)。create が誤りでも問い直して在れば在った。
  名が規則に合わなければ docker を呼ばずに断る。`inspect` が「no such volume」でない誤りで失敗したら、無いと読み違えず例外で上げる。volume を消す手は持たない。
- `DockerRunHost` が覚えるのは docker が持たない 2 つ — 止めを頼んだ刻(止まった刻が後なら `REQUESTED`)と、container を作れなかった
  run。作り直すと忘れる。
- 受け付けは container を作って起こしてから答える。image が手元に無い時は `docker create` が取り寄せを待つ(取り寄せの間は名を docker が
  持たないため)。

## process の handler

```hy
(import doeff_core_effects [scheduled offloaded-subprocess-handler await-handler process-exit-handler])
(import doeff_time [sync-time-handler])
(import doeff_run [ProcessRunHost process-run-handler])

(setv host (ProcessRunHost :images {"local/toolbox" "/opt/toolbox/bin"}))
(run (scheduled (with_handlers [(sync-time-handler) await-handler offloaded-subprocess-handler process-exit-handler (process-run-handler host)] program)))
```

docker の無い手元の開発だけの handler(本番は k3s・本物の検は docker)。run 1 つ = 子 process 1 つで、新しい process group に立て(止めは group へ
送るので孫も止まる)、立てた process が終われば子も終わる。image は「実行 file の入った dir」と読み、host の表 `images`(image の名 → 絶対 path の dir)で引いた
dir を子の `PATH` の先頭に置く(`RunSpec.env` が `PATH` を持てばそれが勝つ — docker で env の `PATH` が image の `PATH` を置き換えるのと同じ)。
読みの待ちは `AwaitProcessExit`(pidfd・kqueue)で子の終わりに起き、時間で見に行かない。外側に `offloaded-subprocess-handler`・`process-exit-handler`・
`await-handler`・時計・`scheduled` が要る。

- 断る設定(`UNSUPPORTED`): volume(process の子は host の file system を見る)・資源の上限(cpu・メモリ — 子に課す手立てが無い)。黙って無視せず、走らせる前に断る。
  volume の効果(`VolumeEnsure`・`VolumeStatus`)も同じ訳で `VolumeRefused(UNSUPPORTED)` と答える。
- 起きなかった run(`NEVER_BEGAN`): host の `images` に無い image・命令の無い run(process に image の既定の命令は無い)・起こせない命令(実行 file が `PATH` に無い など)。
- 止まった訳は `EXITED` と `REQUESTED` の 2 つ(`OUT_OF_MEMORY` は無い)。signal で死んだ子の終了 code は shell・docker と同じ 128 + signal の番号。
- 子は立てた時にもう走っているので、受け付けは `RunLive` まで進む(`RunPending` は無い)。止まった刻は handler が気づいた刻 — 読んでいる間の止まりは終わりの刻、
  誰も見ていない間の止まりは次に見た刻(OS は子の終わりの刻を残さない)。
- `ProcessRunHost` が覚えるのは名ごとの run(handle・pid・出来事・止めを頼んだ刻)。作り直すと忘れ、子は立てた process と一緒に終わる。
  `live_pids` は覚えている走っている子の pid(検の後始末が止める)。

## 契約の検

`tests/test_contract.hy` は、どの handler の下でも同じ筋書き(起動できない image・止めを無視する run・メモリの上限・消えた run・名の重なり・出来事の頁と待ち・止めの冪等)を
公開 effect だけで撃つ。負荷の中身は image の中の `contract-workload` の命令(`live`・`exits`・`stubborn`・`hog`)で選ぶ(`tests/workloads.hy`)。
新しい handler を足す変更は、`tests/interpreters.hy` に解釈器の名と組を足し、各 deftest の `:interpreters` に名を足して、筋書きは変えずに同じ契約を通す。

解釈器は `fake`・`docker`・`process`。`docker` は環境変数 `DOEFF_RUN_DOCKER=1` の時だけ走り(無ければ skip)、負荷の image を `tests/workload/` から
その場で build する。docker の在る機体で `DOEFF_RUN_DOCKER=1 uv run pytest packages/doeff-run` と走らせる。docker を呼ばない訳
(設定の断り・create の命令・inspect の答えの読み)は `tests/test_docker_translation.py` が確かめる。`process` は切り替え無しで走り(`sh` の在る機体)、
host の `images` に負荷の dir `tests/workload/` を引かせる。process だけの約束(volume の断り・命令の無い run・env・signal で死んだ子・止めの同時の頼み)は
`tests/test_process_handler.hy`、子を立てない訳(断り・終了 code・PATH・host の検め)は `tests/test_process_translation.py`。

volume の契約は `tests/test_volume_contract.hy` が同じ作りで撃つ。volume を持つ handler(`fake`・`docker`)は、作っていない volume を指す run が起きない事・
作ったと答えた volume が本当に在る事・同じ名の作成が重なっても 1 つ・消えた volume を黙って作り直さない事・規則に合わない名を作る前に断る事・
作った volume が `RunStop`・`RunRemove` の後も残る事を通し、持たない handler(`process`)は断りの側を通る。消えた volume の検は、解釈器の横の口
(`Settings.vanish_volume` — backend から直に消す。DoeffRun に消す効果は無い)で作る。docker を呼ばずに handler の訳(作る前の断り・命令・「作った」と「在った」の
見分け・作ったつもりで無い事)を確かめる検は、docker の CLI を台本で模す `tests/test_docker_volume.hy` と `tests/test_docker_translation.py`。

資源の上限は「守るか断るか」で、黙って無視する handler は無い。守る handler(`fake`・`docker`)は上限を越えた run を `OUT_OF_MEMORY` で止め、
守れない handler(`process`)は走らせる前に `UNSUPPORTED` で断る — 契約の検は筋書きを 2 つに分けて両方を置く。
