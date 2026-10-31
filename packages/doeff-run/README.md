# doeff-run

「image を、この設定(命令・環境変数・volume・資源の上限)で走らせる」ための doeff の effect と型と、memory の上で答える模擬(fake)の handler。

利用者の Program は下の effect だけを使う。走らせる先が docker か process か k3s かは、composition root が選ぶ handler で決まる。
container・Pod・process の id は handler の内側に閉じ、Program が持つ身元は取っ手(`RunHandle`)だけ。ここに在るのは effect と型と、
fake の handler と docker の handler と k3s の handler と、全部の handler が通す契約の検(`tests/test_contract.hy`)。process の handler は別の変更で足す。

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

- 受け付けは「走り始めた」ではない。起きたことは出来事 `RunLive` で、起きずに止まったことは `RunStopped(cause=NEVER_BEGAN)` で届く。
- 失敗は例外ではなく答えの型で返す。取っ手の run を handler が知らない時(片付けた・消えた・覚えが無い)は、どの effect も「消えた」として答える。
- 出来事 `RunEvent(seq, at, state)` は run の中で 1 から欠けずに増え、読みは新しい出来事が起きるか上限の秒が過ぎるまで待つ。
- 状態は `RunPending`(受け付けた)→ `RunLive`(起きた)→ `RunStopped(exit_code, cause, detail)`(止まった — `cause` は `EXITED`・`REQUESTED`・`OUT_OF_MEMORY`・`NEVER_BEGAN`)。
  `RunGone` は backend から消えた run で、跡(終了 code)は何も残らない。

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
- `DockerRunHost` が覚えるのは docker が持たない 2 つ — 止めを頼んだ刻(止まった刻が後なら `REQUESTED`)と、container を作れなかった
  run。作り直すと忘れる。
- 受け付けは container を作って起こしてから答える。image が手元に無い時は `docker create` が取り寄せを待つ(取り寄せの間は名を docker が
  持たないため)。

## k3s の handler

```hy
(import doeff_core_effects [scheduled offloaded-subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_run [K3sRunHost k3s-run-handler])

(run (scheduled (with_handlers [(sync-time-handler) offloaded-subprocess-handler (k3s-run-handler (K3sRunHost :namespace "agent-env"))] program)))
```

本番の handler。run 1 つ = Pod 1 つ(container 1 つ・`restartPolicy: Never`)を `kubectl` で操る。Pod の名 = run の名で、取っ手の run-id は
Pod の label `doeff-run.run-id` に置く。状態と出来事は毎回 Pod の答え(JSON)から引き(起きた刻 `startedAt` に `RunLive`・止まった刻
`finishedAt` に `RunStopped`。k8s の刻は秒まで)、handler は run を覚えない — `K3sRunHost` が持つのは kubectl の呼び方(`kubectl`・別の
cluster や替え身の身元は `--kubeconfig`・`--as` を足す)・namespace・片付けの印だけで、handler を作り直しても run は読める。子 process は
`RunProcess` で出すので、外側に `offloaded-subprocess-handler` と `scheduled` が要る。

- 止めた Pod の跡 = Pod は finalizer `doeff-run/keep-trace` つきで作る。止める(delete)と Pod は終了 code を持ったまま Terminating で残り、
  `RunRemove` が finalizer を外して初めて消える(名は片付けるまで空かない)。
- 止めの訳 = k8s の Pod は「止めを頼まれて止まった」「自分で終わった」「起きずに消された」を残さないので、handler が止める前に Pod の
  annotation `doeff-run/ended` へ訳と刻を書く。頼みは最初の 1 つが勝つ(上書きしない)。止めの猶予は `kubectl delete --grace-period` で、
  SIGTERM → 猶予 → SIGKILL は kubelet が行う。
- env = Pod の宣言に値を書かず、Secret に置いて Pod が名と key で引く(`secretKeyRef`)。会話は ServiceAccount で Pod を読めるので、
  平文で書くと他の会話の資格が読める。Secret は Pod が持ち主(ownerReference)なので Pod と一緒に消える。Pod を先に作り Secret を後に作る
  (逆だと、Pod の前に落ちた時に持ち主の無い Secret が残る)。宣言は kubectl の標準入力で渡し、命令の引数に値を載せない。
- 読みの待ち = 読んだ版(resourceVersion)からのその Pod の変化の最初の 1 行で起きる。`kubectl get -w` は版を渡せず、読みとの間の変化を
  取りこぼすので、API の watch を `kubectl get --raw` で頼む。時間で見に行かない。
- 断る設定(`UNSUPPORTED`): PVC の名でない volume の source(path は hostPath に読めるが、node の dir を会話に見せてしまうので作らない)・
  Secret に入らない大きさの env(512 KiB まで)・doeff-run の作っていない Pod が持つ名。割り当ての上限を越えた時は `NO_CAPACITY`。
  空きが足りなくて置けない Pod は後で置かれるので `RunPending` のまま。
- 起きなかった run(`NEVER_BEGAN`): 取り寄せられない image(`ErrImagePull`・`ImagePullBackOff` など)と無い PVC は、k8s では backoff で
  待ち続けて終わらない。読みで見つけたら annotation に訳を書いて Pod を止め、起きなかった止まりとして答える。命令の無い Pod は
  kubelet が `StartError` で終わらせる。
- 資源の上限 = `requests` と `limits` を同じ値にする(置く時の見積もりと殺す線を同じに)。メモリを越えた run は `OOMKilled`(`OUT_OF_MEMORY`)。
- Pod に ServiceAccount の token を渡さない(`automountServiceAccountToken: false`)— 中の process が cluster の API を呼べない。
- 要る権限(namespace の中だけ): `pods` の `create`・`get`・`patch`(止めの訳と finalizer)・`delete`・`watch`(読みの待ちと削除の待ち)と、
  `secrets` の `create`。権限の宣言は handler の外(配備)が持つ。

## 契約の検

`tests/test_contract.hy` は、どの handler の下でも同じ筋書き(起動できない image・止めを無視する run・メモリの上限・消えた run・名の重なり・出来事の頁と待ち・止めの冪等)を
公開 effect だけで撃つ。負荷の中身は image の中の `contract-workload` の命令(`live`・`exits`・`stubborn`・`hog`)で選ぶ(`tests/workloads.hy`)。
新しい handler を足す変更は、`tests/interpreters.hy` に解釈器の名と組を足し、各 deftest の `:interpreters` に名を足して、筋書きは変えずに同じ契約を通す。

解釈器は `fake` と `docker` と `k3s`。`docker` は環境変数 `DOEFF_RUN_DOCKER=1` の時だけ走り(無ければ skip)、負荷の image を `tests/workload/` から
その場で build する。docker の在る機体で `DOEFF_RUN_DOCKER=1 uv run pytest packages/doeff-run` と走らせる。docker を呼ばない訳
(設定の断り・create の命令・inspect の答えの読み)は `tests/test_docker_translation.py` が確かめる。

`k3s` は `DOEFF_RUN_K3S=1` の時だけ走り(無ければ skip)、`DOEFF_RUN_K3S_NAMESPACE`(検専用の namespace)と `DOEFF_RUN_K3S_IMAGE`(`tests/workload/` の
Dockerfile で組んだ、node が取り寄せられる image)を読む。`DOEFF_RUN_K3S_AS` に身元(`system:serviceaccount:<ns>:<sa>`)を渡すと kubectl を
その替え身で呼ぶので、本番に渡す権限だけで契約が通る事を確かめられる。検ごとに新しい `K3sRunHost` を作り、検の終わりにその host の印の Pod を消す。
cluster を呼ばない訳(設定の断り・Pod と Secret の宣言・Pod の答えの読み)は `tests/test_k3s_translation.py` が確かめる。
