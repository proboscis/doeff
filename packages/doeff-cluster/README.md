# doeff-cluster

doeff の Program を、k8s の Deployment のように「定義がある限り動かし続ける」ための実行基盤です。業務のコードは git の commit で
指定し、worker がその版の木を展開して子 process で動かすので、業務のコードを入れ替えるのに image の build は要りません。

この package は業務を知りません。業務の側(アプリの repo)は service を宣言の値(`service_model.service`)で書き、handler の組(env)と effect の記録の登録を
自分の module に置き、この package の coordinator と worker を自分の manifest で動かします。

## 全体の形

```
  作業者 ──HTTP──▶ coordinator(1 台・状態は追記の log + snapshot)
  (X-Actor 必須)     │  Service・Rollout の定義を持ち、worker へ割り当て、Rollout を 1 秒ごとに進める
                    │  GET /metrics(Prometheus の text)
                    ▼ heartbeat(worker → coordinator)
                  worker(node ごとに 1 つ)── 子 process = service 1 つ・task 1 つ
```

- **coordinator** は定義(Service・Rollout)と割り当てを持ちます。書き込みは永続化してから返事をします(group commit)。
- **worker** は heartbeat の返事で自分の担当を受け取り、子 process を起動・停止します。20 秒 coordinator に届かなければ lease を
  持たない担当を止め、coordinator は 45 秒連絡の無い worker の担当を他へ移します(同じ service が 2 か所で動かないための順序)。
- **service** は名前付きで動き続ける Program です(Program を作る関数 `defk` と、それを名指す宣言の値 `service` で書く)。落ちたら 2・4・8…60 秒の間隔で起動し直されます。
- **task** は呼び手に寿命が縛られる短い Program です(effect `RemoteJob` で送る)。
- **切り離した task** は呼び手と寿命を切り離した Program です(effect `SubmitDetached` で送り、`AwaitDetached` で待つ)。呼び手の決めた
  job id で冪等に送り、呼び手が消えても続き、後から同じ job id で結果を受け取れます(下の「切り離した task」)。

## 用語

| 語 | 意味 |
|---|---|
| 置き先(placement) | Service をどの worker に置いたか(`Placement`・世代 `generation` つき)。永続化の鍵は `placement/<名>` |
| Service | 「動かし続ける Program」の定義。関数の参照(`module:関数`)・commit・設定・`replicas`(0 か 1)・readiness・所有者を持つ |
| Rollout | 旧(Deployment か Service)から新(同)への切り替えの定義。新が Ready になってから旧を止め、失敗したら旧を先に戻してから新を止める |
| readiness | service が「準備できた」を報告する仕組み。effect `ReportReady` を周期ごとに出し、定義の `windowSeconds` 以内の報告があれば Ready |
| 共有の保存(盤) | coordinator の `/board`。どの worker で動いても同じ値が読める key-value(compare-and-set つき)。effect `ReadShared` / `WriteShared` |
| lease | 名前付きの排他(effect `CreateNamedSemaphore` → `AcquireSemaphore`)。期限は coordinator の時計だけで書き・判じる |
| 書き込みの防護(`lease-fence`) | lease を持っていて期限まで余裕がある間だけ、書きの effect を外へ通す handler |
| handoff | 版か設定が変わった時の入れ替え方。新しい process を旧と並べて起動し、新が Ready になってから旧を止める |
| 版の追随(`baseFrom`) | service の業務コードの版を、名指した Deployment の image の LABEL の commit に自動で合わせる仕組み |
| drain | worker を空けてよいかを問う印。handoff の service は別の worker へ並べて Ready を待ってから移す |

## module の地図

| 役割 | module |
|---|---|
| coordinator(composition root・調停ループ) | `coordinator`・`coordinator_handler_sets`・`coordinator_inbox`・`coordinator_http` |
| coordinator の純粋な判断 | `api_policy`・`cluster_policy`・`resource_policy`・`rollout_policy`・`drain_policy`・`base_follow_policy`・`metrics_policy` |
| coordinator の状態と耐久 | `cluster_model`・`durable_kv`・`wal_store` |
| worker(composition root・判断・I/O) | `main`・`worker`・`worker_policy`・`worker_model`・`handlers`・`code_prepare`・`job_entry`・`shim.py` |
| drain と readiness の口 | `drain_client`・`drain_main`・`readiness_*`・`report_client` |
| effect と handler | `shared_*`(盤)・`semaphore_*`(lease)・`metrics_*`・`kube_*`・`image_*`・`remote*`(task)・`detached*`(切り離した task) |
| effect の記録と再生 | `effect_codec`・`record_model`・`record_handlers`・`record_store*`・`replay_main` |
| 宣言 | `service_model`(宣言の値 `service`・`System`)・`declare` |
| 時計の換算 | `clock`(epoch ミリ秒。時計の語彙は doeff-time ちょうど 1 つ) |
| 配備の材料 | `deploy/boot.sh`・`deploy/Dockerfile`・`deploy/base/Dockerfile`(土台だけの image)・`image_contract`(土台の image の約束の検査) |

## 業務の側から使う

### service を書く

```hy
(require doeff-hy.macros [defk <-])
(import doeff_cluster.service_model [service System])

(defk my-writer-program [poll apply]
  {:pre [(: poll float) (: apply bool)] :post [(: % int)]}
  ...)

(setv my-writer (service "my-writer" my-writer-program
                         :env "myapp.envs:my_writer_env"         ; handler の組を返す関数の import path
                         :requires {"kind" "k3s"}                ; worker の label の条件
                         :readiness {"windowSeconds" 30}         ; ReportReady をこの秒数以内に出し続ける
                         :update "handoff"                       ; 版や設定の変更は新旧を並べて入れ替える
                         :base-from {"kind" "Deployment" "namespace" "prod" "name" "my-writer" "container" "my-writer"}
                         :config {"poll" 5.0 "apply" False}      ; 本体の引数(鍵と引数が 1 対 1)
                         :env-config {"token-file" "/etc/my-writer/token"}))  ; env だけが読む設定(本体の引数にしない)

(setv my-system (System "my-system" #(my-writer)))
```

- 宣言は値と関数で書きます(macro は置かない — ADR-DOE-HY-005 R5)。関数の参照(`module:attr`)は `service` が Program を作る関数から
  導くので、その関数は module の最上位に置きます(入れ子の関数・lambda は宣言の時点で断る)。`:requires`・`:config`・`:env-config`・
  `:readiness`・`:base-from` の鍵は文字列で書きます(宣言は JSON で coordinator へ渡る)。
- 設定は持ち主で分けます。`:config` は本体の引数で、鍵と引数が 1 対 1 です(本体の引数に無い鍵・`:config` に無い引数は宣言の時点で
  `TypeError`)。`:env-config` は env だけが読む設定(資格の file・lease の時間・HTTP の timeout 等)で、本体の引数にしません。
  coordinator へは 2 つを重ねた平たい `run.config` が渡り、実行先は本体へ本体の引数の名の設定だけを、env へは全体を渡します。
  テスト用の main と `declare --config` の上書きは宣言した鍵(と `record`)だけを変えられます。

- 業務の Program は file・lock・通信を effect を通してだけ触ります。共有の状態は `ReadShared` / `WriteShared`、排他は
  `CreateNamedSemaphore`、時計は doeff-time の `GetTime` / `GetMonotonic` / `Delay`、実行先の値は `Ask` で受けます。
- env の関数 `(fn [config ctx] → handler の list)` は外側が先・最後が一番内側です。`ctx` は `job_entry.RunContext`(coordinator の URL・
  worker の名・版・process の世代)。
- 宣言を coordinator へ置く: `hy -m doeff_cluster.declare myapp.services:my_system --revision <commit> --apply $COORD --actor $ME --replicas 0`

### 業務の effect を記録に載せる

記録と再生(下)は effect の型ごとの登録(`effect_codec.register`)を引きます。この package が登録するのは doeff の汎用の型
(`Ask`・doeff-time・scheduler)とこの package の型だけです。業務の型は、業務の側の env の module(か、それが import する module)で
登録します。worker の子 process(`job_entry`)と再生の入口(`replay_main`)は env の module を import してから記録・再生を始めます。

```hy
(import doeff_cluster.effect_codec [register EffectCodec READ DECISION OUTPUT])
(import myapp.effects [ReadRows WriteRow])
(register (EffectCodec ReadRows READ))
(register (EffectCodec WriteRow DECISION :subject (fn [args] (.get args "key")) :unexecuted True))
```

| 扱い | 再生での答え |
|---|---|
| `read` | 記録の答え。問い(型・引数・順番)が記録と違えば分岐 |
| `live` | 本物の scheduler が解く(順番だけ突き合わせる) |
| `decision` | 実行せず突き合わせる。対の鍵 `subject` と、対の無い時の答え `unexecuted` を宣言する |
| `output` | `decision` と同じ扱いで、報告の違いとして数える |

### 業務の repo の木の形(worker の引数)

worker は業務の repo の commit を展開して子 process の cwd にします。木の形は worker の引数で渡します(`worker_model.CodeLayout`)。

| 引数 | 意味 | 既定 |
|---|---|---|
| `--import-roots` | 子の PYTHONPATH に並べる木の中の dir(`,` で並べる・前が先)。bytecode の準備も同じ根で module 名を決める | `.` |
| `--overlay-path` | Service の `overlay`(定義だけを別の commit で動かす口)で、base の木の上に重ねる dir。空なら重ねる木を断る | 空 |

子 process の入口(`doeff_cluster.job_entry`)はこの package の物で、木の中の物ではありません。業務のコードの版は木が、クラスタの
仕組みの版は worker の実行環境(image の venv)が決めます。

### 外の系と取り交わす名(coordinator の引数)

`--naming '<JSON>'`(`cluster_model.ClusterNaming`):

| 欄 | 意味 | 既定 |
|---|---|---|
| `ownerAnnotation` | Rollout が台数を持つ Deployment に付ける annotation の鍵 | `doeff-cluster/replicas-owned-by` |
| `ownerScope` | その値の頭(`<scope>/Rollout/<名> replicas=<n>`) | `doeff-cluster` |
| `revisionLabel` | 版の追随が読む image の LABEL(40 桁の commit) | `org.opencontainers.image.revision` |
| `versionLabels` | 版と一緒に写す LABEL(`{鍵: LABEL}`)。Service の `status.base` に鍵の名で並ぶ | `{}` |

## coordinator の HTTP

宛先を `COORD`、自分の名(依頼の主体の id か作業者の名)を `ME` とします。

| API | 返すもの |
|---|---|
| `GET /resources/Service`・`GET /resources/Service/<名>` | 定義(`spec`)・状態(`status`: 置き先・Ready か・業務コードの版 `status.base`・直近の readiness)・`resourceVersion`・所有者 |
| `GET /resources/Rollout/<名>` | Rollout の段階(`status.phase`)・旧の元の台数・台数の食い違い(`status.drift`) |
| `GET /resources/Worker` | worker の label・最後の連絡からの秒 |
| `GET /state` | 置き先(`placements`)・各 worker の process の様子・置けない service(`unplaced`)と理由 |
| `GET '/events?kind=Service&name=<名>&limit=50'` | 誰がいつ何を書いたか |
| `GET /metrics` | Prometheus の text(service の計器は label `service`・`worker` 付き・盤の行の数と大きさ・戻しの止まった Rollout) |
| `GET /livez`・`GET /readyz` | 調停ループが最後に要求を取りに来てからの秒だけで答える(readyz は 30 秒・livez は 120 秒止まると 503) |
| `POST /workers/<名>/drain`・`DELETE /workers/<名>/drain` | worker の drain の依頼と取り消し |
| `POST /leases/<名>` | 名前付きの lease(`op` = claim / renew / release / drop) |

書く時に守ること:

- 書き込みには header `X-Actor: $ME` が要ります(無いと 400)。
- 書き換えは版つきです。`GET` で読んだ `resourceVersion` を付けて `PUT` します。読んだ後に誰かが書いていれば 409 で何も書かれません。
- Service を消すのは所有者の `DELETE` だけです(所有者でなければ `?force=true`)。進行中の Rollout が扱っている Service は消せません。
- 盤の行に `"ttlSeconds": n` を付けると n 秒後に消えます。上限: 1 行 1 MiB・20,000 行・合計 64 MiB(越える書きは 507)。
  task は終わっていない物が 2,000 本まで(429)・lease は 1 時間まで。切り離した task の行(終わって結果を保持している物を含む)は
  10,000 本まで(429)。

## 切り離した task

`RemoteJob` の task は呼び手の問い合わせが lease を延ばし、呼び手が抜けると落ちます。呼び手より長く生きる仕事(呼び手の process が
入れ替わっても続けたい仕事)は、切り離した task として送ります。

```hy
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached
                                      DetachedSucceeded DetachedFailed DetachedLost])
(import doeff_cluster.cluster_model [Requirement])
(<- submitted (SubmitDetached (summarize rows) :env "myapp.envs:board_env" :key job-id
                              :requires #((Requirement "kind" "k3s")) :lease-seconds 60.0))
;; ... 呼び手が消えてもよい。別の process から同じ key で待てる ...
(<- outcome (AwaitDetached job-id))
```

| effect | 答え | 意味 |
|---|---|---|
| `SubmitDetached` | `DetachedSubmitted(key, created)` | job id(`key`)で冪等に送る。同じ key がまだ在れば何も作らない(`created` = False)。同じ key で env・name・requires が違えば `DetachedRefused`(409)。`requires` は `Requirement(label, value)` の tuple(対の生の tuple は `TypeError`) |
| `AwaitDetached` | 答えの型か `DetachedPending` | 終わるまで待つ(`timeout-seconds` を過ぎたら `DetachedPending`)。抜けても task は落ちない |
| `CancelDetached` | `bool` | 終わっていなければ取り消して True。終わっていれば何もせず False(結果は保持) |
| `ReleaseDetached` | `bool` | 終わった task の保持を解く。以後その key は `DetachedUnknown` で、同じ key で送り直せる |

答えの型(失敗は例外ではなく値): `DetachedSucceeded`(値)・`DetachedFailed`(Program の例外)・`DetachedLost`(担い手の worker が
死んだ — 走らせ直さない)・`DetachedCancelled`・`DetachedVersionMismatch`(送り手と受け側の版が違う)・`DetachedUnrunnable`(label の合う
worker が無い・コードを準備できない)・`DetachedUnknown`(知らない key)。

- **lease は担い手が延ばす**: 担い手の worker の heartbeat が lease を延ばします。呼び手の問い合わせは lease に触りません。worker が
  `lease-seconds` の間沈黙するか、同じ名の worker が別の process の世代で名乗ったら、その task は `DetachedLost` です。
- **結果の後の消失**: 結果を受け取った後に worker が死んでも、結果は変わりません。結果は `retain-seconds`(既定 24 時間・30 日まで)か
  `ReleaseDetached` まで持ちます。
- **途絶**: worker は coordinator と途絶えても切り離した task を止めません(途絶が lease より長ければ coordinator が消失とし、再接続の
  返事から外れた時に止めます)。
- **drain**: drain は worker の上の切り離した task が 0 になるまで `Drained` になりません(task は移せないので終わるのを待つ)。
- handler: `detached-cluster`(coordinator の `/detached` の口と話す — `DetachedClient`)と `detached-local`(同じ VM の scheduler の
  task で走らせる fake。`SimulateRunnerLoss` で担い手の死を起こせる)。

| HTTP | 意味 |
|---|---|
| `PUT /detached/<key>` | 送る `{env blob versions revision requires name leaseSeconds retainSeconds}` → `{key task created phase}` |
| `GET /detached/<key>` | 読む(lease に触らない)→ `{key phase detail result worker}`。知らない key は `phase` = `unknown` |
| `POST /detached/<key>/cancel` | 取り消す → `{key cancelled phase}` |
| `DELETE /detached/<key>` | 終わった task の保持を解く → `{key released}`。まだ終わっていなければ 409 |

## Rollout

旧(`from`)→ 新(`to`)。どちらも `{"kind": "Deployment", "namespace", "name", "replicas"?, "dryRun"?}` か `{"kind": "Service", "name"}` です。

| 段階 | すること | 次へ進む条件 |
|---|---|---|
| Pending | 旧の今の台数を控える | すぐ |
| WaitingNewReady | 新を起動する | 新が Ready。`readyTimeoutSeconds`(既定 300)を過ぎたら戻す |
| StoppingOld | 旧を 0 にする | 旧が止まった。途中で新が NotReady・`stopTimeoutSeconds`(既定 180)超えなら戻す |
| Observing | 新を見続ける | `observeSeconds`(既定 1800)で Complete。新が続けて `failAfterSeconds`(既定 30)NotReady なら戻す |
| RollingBack | 旧を元の台数へ戻して Ready を待ち、それから新を 0 にする | RolledBack。`rollbackTimeoutSeconds`(既定 600)を過ぎたら `status.stuck` |

- どの失敗でも、`abort` を書いても、旧を先に戻して Ready を確かめてから新を止めます。
- coordinator が止まっていた時間は段階の時間に数えません。
- 完了した Rollout は、その Deployment の台数の持ち主になり、期待と違えば `status.drift` に出します(直しはしません)。
  `markDeployment` を付けると Deployment に持ち主の annotation(`--naming` の `ownerAnnotation`)を付けます。

## effect の記録と再生(backtest)

service の設定(`run.config`)に `record` 欄を足すと、`job_entry` が handler の組の一番内側に記録係を足します。`record` は組み立て側の欄で、
業務の Program の引数には入りません(テスト用の main・実行先・再生のどれも `program-arguments` で外す — 宣言の `:config` に書いてもよく、
本体の引数に `record` という名は使えない)(`{"otlp": "<collector の URL>", "chunkSeconds": 3600, "flushSeconds": 2.0}`)。記録の 1 行は OpenTelemetry の log record 1 件です。
再生は `hy -m doeff_cluster.replay_main --recording FILE --out FILE` を業務コードの版の木の中で撃ちます(外の I/O をする handler は組まず、
記録か本物の scheduler だけが答える)。記録の形は `record_model.hy` の先頭、型ごとの扱いは上の表。

## 配備の材料

- `deploy/boot.sh` — Pod と手元の機体で共通の起動 script。`ROLE` = `coordinator` / `worker` / `records` / `drain`(preStop)/ `ready`
  (readinessProbe)。worker は `CODE_REPO_URL` の bare mirror を用意してその版を展開します。env は script の先頭の註。
- `deploy/Dockerfile` — 業務の image(doeff の venv を持つ物・この package を含む)に git と ssh を足し、`boot.sh` を置くだけの image。
  `--build-arg BASE=<業務の image>`。
- `deploy/base/Dockerfile` — 土台だけの image(OS・git・ssh・uv・Rust の toolchain・tini・`boot.sh`)。doeff も Python も持たず、
  `boot.sh` が `WORKER_DOEFF_COMMIT` の doeff を展開して `uv sync --locked --package doeff-cluster` した venv から coordinator / worker を
  起こします(自己起動)。worker の code を変える時は commit を変えて入れ替え、image は作り直しません。root を用意した後は、起動の
  script も root の中の同じ commit の `deploy/boot.sh` へ引き継ぐので、起動の script を直した時も image は作り直しません。作り直す理由は頭の註の 2 種類
  だけで、それ以外の変更は `hy -m doeff_cluster.image_contract <Dockerfile>`(と `tests/test_base_image_contract.hy`)が赤にします。
  非公開の repo は `WORKER_REPOS`(url ごとの読み取り専用の deploy key)で読みます。`ROLE=access` で書かれる設定だけを確かめられます。

manifest(namespace・node・Secret・Role)は配備する側の repo が持ちます。

## テスト

```sh
# doeff の repo の根から(日次の make test-packages と同じ形)
uv run --no-sync pytest packages/doeff-cluster/tests -q
```

検は Hy の `test_*.hy`(deftest)で、`tests/conftest.py` が doeff-adr の Hy の file の収集をこの dir に掛けます(module 名は
`tests.<名>`)。

`tests/test_no_application_vocabulary.hy` は、この package(source・検・配備の材料・文書)に業務の系の語が混ざっていないことを確かめます。
