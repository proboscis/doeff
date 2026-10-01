# doeff-cluster

doeff の Program を、k8s の Deployment のように「定義がある限り動かし続ける」ための実行基盤です。業務のコードは git の commit で
指定し、worker がその版の木(か実行環境の root)を用意して子 process で動かすので、業務のコードを入れ替えるのに image の build も
worker の再起動も要りません。

この package は業務を知りません。業務の側(アプリの repo)は job を Program の値として書き、系(`defsystem`)にまとめて宣言し、
この package の coordinator と worker を自分の manifest で動かします。job が受け取るのは Program の値 1 つだけで、handler は
Program の中の `with-handlers` で並べます(実行先は handler を 1 つも足しません — ADR-DOE-CLUSTER-001)。

## 全体の形

```
  作業者 ──HTTP──▶ coordinator(1 台・状態は追記の log + snapshot)
  (X-Actor 必須)     │  Service・Rollout の定義と詰めた Program(/programs/<sha>)を持ち、worker へ割り当てる
                    │  GET /metrics(Prometheus の text)
                    ▼ heartbeat(worker → coordinator)
                  worker(node ごとに 1 つ)── 子 process = service 1 つ・task 1 つ
```

- **coordinator** は定義(Service・Rollout)と割り当てと詰めた Program を持ちます。書き込みは永続化してから返事をします(group commit)。
- **worker** は heartbeat の返事で自分の担当を受け取り、子 process を起動・停止します。20 秒 coordinator に届かなければ lease を
  持たない担当を止め、coordinator は 45 秒連絡の無い worker の担当を他へ移します(同じ service が 2 か所で動かないための順序)。
- **service** は名前付きで動き続ける Program です。落ちたら 2・4・8…60 秒の間隔で起動し直されます。
- **task** は呼び手に寿命が縛られる短い Program です(effect `RemoteJob` で送る)。
- **切り離した task** は呼び手と寿命を切り離した Program です(effect `SubmitDetached` で送り、`AwaitDetached` で待つ)。

## 用語

| 語 | 意味 |
|---|---|
| job | service か task。中身は Program の値 1 つ(`defk` の関数を呼んだ結果) |
| 系(System) | job の組。`defsystem` で書く関数に土台を渡すと `service_model.System` の値になる |
| 土台(foundation) | 本体の Program を受け取り、自分の handler(と scheduler・時計)の下で走らせて答えを返す、module の最上位の `defk` |
| 能力(needs / provides) | job が要る能力の名(`:needs`)と、worker が提供する能力の名(`--provides`)。coordinator は needs ⊆ provides の worker に置く |
| Program の置き場 | coordinator の `/programs/<sha>`。詰めた Program(cloudpickle の文字列)を中身の sha256 をキーに置き、宣言と task は sha だけを運ぶ |
| 実行先(宿) | job の Program を走らせる所。本番の worker の子 process(`job_entry`)と、手元の `sim-cluster` の偽の実行先 |
| 置き先(placement) | Service をどの worker に置いたか(`Placement`・世代 `generation` つき) |
| Rollout | 旧(Deployment か Service)から新(同)への切り替えの定義。新が Ready になってから旧を止め、失敗したら旧を先に戻してから新を止める |
| readiness | service が「準備できた」を報告する仕組み。effect `ReportReady` を周期ごとに出し、定義の `windowSeconds` 以内の報告があれば Ready |
| 共有の保存(盤) | coordinator の `/board`。どの worker で動いても同じ値が読める key-value(compare-and-set つき)。effect `ReadShared` / `WriteShared` |
| lease | 名前付きの排他(effect `CreateNamedSemaphore` → `AcquireSemaphore`)。期限は coordinator の時計だけで書き・判じる |
| handoff | 版か環境変数が変わった時の入れ替え方。新しい process を旧と並べて起動し、新が Ready になってから旧を止める |
| drain | worker を空けてよいかを問う印。handoff の service は別の worker へ並べて Ready を待ってから移す |

## module の地図

| 役割 | module |
|---|---|
| coordinator(composition root・調停ループ) | `coordinator`・`coordinator_handler_sets`・`coordinator_inbox`・`coordinator_http` |
| coordinator の純粋な判断 | `api_policy`・`cluster_policy`・`resource_policy`・`rollout_policy`・`drain_policy`・`handoff_policy`・`program_policy`・`warm_policy`・`detached_policy`・`metrics_policy` |
| coordinator の状態と耐久 | `cluster_model`・`durable_kv`・`wal_store` |
| worker(composition root・判断・I/O) | `main`・`worker`・`worker_policy`・`worker_model`・`handlers`・`code_prepare`・`shim.py` |
| 実行環境(runtime env)の宣言と準備 | `runtime_env_model`・`runtime_env`(送り手の checkout の読み)・`env_prepare`・`env_handlers`・`env_upkeep`・`env_world` |
| 子 process の入口と実行先の契約 | `job_entry`・`job_context`・`host_contract` |
| 宣言 | `service_model`(`Job`・`System`・`system-declaration`)・`declare` |
| 手元の runner と検め | `local`(`sim-cluster`)・`foundation_check` |
| drain と readiness の口 | `drain_client`・`drain_main`・`readiness_*`・`report_client` |
| effect と handler | `shared_*`(盤)・`semaphore_*`(lease)・`metrics_*`・`kube_*`・`remote*`(task)・`detached*`(切り離した task)・`warm_*` |
| effect の記録と再生 | `effect_codec`・`record_model`・`record_handlers`・`record_store*`・`replay_main` |
| 時計の換算 | `clock`(epoch ミリ秒。時計の語彙は doeff-time ちょうど 1 つ) |
| 配備の材料 | `deploy/boot.sh`・`deploy/Dockerfile`・`deploy/base/Dockerfile`(土台だけの image)・`image_contract`(土台の image の約束の検査) |

## 業務の側から使う

### job と系を書く

```hy
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(import doeff [with-handlers DoExpr])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_cluster.host_contract [host-reader environ-reader])
(import doeff_cluster.shared.protocol.record_handlers [boundary-recorder])

;; 本番の土台: scheduler・時計・実行先の読み・環境変数の読み・クラスタに話す handler を並べる。
(defk production-foundation [body]
  {:pre [(: body DoExpr)] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "myapp" :role "foundation"}}
  "本体を本番の handler の下で走らせる。"
  (<- answer (scheduled (with-handlers [(state) (environ-reader) host-reader (sync-time-handler) …] body)))
  answer)

;; job の本体: 翻訳の handler → 記録係 → 土台 の順に自分で並べる(実行先は何も足さない)。
(defk my-writer [foundation poll]
  {:pre [(: foundation Callable) (: poll float)] :post [(: % None)] :tags {:context "myapp" :role "entry"}}
  "書き手の service。"
  (<- answer (foundation (do! (<- recorder list (boundary-recorder))
                              (<- translation list (translation-handlers))
                              (<- r (with-handlers [#* recorder #* translation] (writer-loop poll)))
                              r)))
  answer)

(defsystem my-system [foundation]
  "書き手の系"
  (my-writer (my-writer foundation 5.0)
    :needs #{"cluster-net"} :readiness {"windowSeconds" 30} :update "handoff" :environ {"WRITER_MODE" "apply"}))
```

- job は Program の値 1 つです。`defsystem` の各行は `(名 (関数 引数…) :キー literal …)` で、引数は系の引数(土台)か literal だけ
  です。形が外れれば展開の時点で `SyntaxError` です。
- 土台は module の最上位の関数で渡します(宣言には `{"ref": "module:qualname"}` の参照で載ります)。handler の値・入れ子の関数・
  lambda は宣言の時点で断ります。handler は Program の本体の中で関数を呼んで作ります(値を詰めると `UnsendableProgram`)。
- `:needs` は要る能力の名の空でない集合です(小文字・数字・`.`・`-`)。置き場所の名(`kind=k3s` の形の label・機体の名)は書きません。
- `:readiness` は `{"windowSeconds" n}`(handoff の期限 `handoffTimeoutSeconds` も書ける)、`:update` は `"recreate"`(既定)か
  `"handoff"`、`:environ` は子 process の環境変数(名は `[A-Z][A-Z0-9_]*`・`DOEFF_`・`PYTHON`・`UV_` などの予約は不可・秘密は置かない)。
  設定は Program の中の `Ask` と、宣言の `:environ` を字面どおり読む handler(`host_contract.environ-reader`)で読みます。
  `doeff_core_effects` の `env-var-ask` は `{` で始まり `}` で終わる値を `{module.path}` の import として解くので、JSON の object を
  置いた設定が本番の子でだけ落ちます(sim の実行先は字面どおり返す)— 土台には引数なしの `(environ-reader)`(子の `os.environ` を読む)を並べます。
  handler の値は Program に詰められないので、土台の `with-handlers` の中でその場で呼んで作ります。
- 旧い宣言の形(`service`・`:env`・`:config`・`:env-config`・`:requires`・image の版を追う `baseFrom`・定義だけを別の commit で重ねる
  `overlay`)は受け付けません。どの入口でも理由つきで断り、保存に残った旧い行は `status.refused` に理由を出して起動しません。

### 土台と :needs

- 本番の土台は scheduler と時計を含みます。手元の `sim-cluster` / `wall-sim-cluster` に渡す sim の土台は含みません(sim の外側の
  scheduler と時計が答えます)。同じ系の関数に違う土台を渡すだけで、本番と手元の系の値ができます。
- 土台の関数の頭の `:needs`(`__doeff_needs__`)は、その土台を使う job の `:needs` の一部でなければなりません。`declare` が宣言の前に
  検めて断ります。土台が中に並べる handler の `:needs` は集めません(土台の頭に手で書く — 漏れは doeff-linter の照合が入るまで
  見つかりません)。
- クラスタに話す handler(`readiness-http`・`metrics-http`・`shared-http`・`cluster-semaphore`・`remote-cluster`・`detached-cluster`・
  `warm-cluster`)は client を引数に取ります。土台は実行先の契約の run-context(下)から coordinator の URL と process の世代を読んで
  client を作ります。
- 本番の土台で job が閉じているか(答えの無い effect が残らないか)は、実行せずに `foundation_check` で確かめます:
  `(foundation-closure my-writer :foundation production-foundation)` → `FoundationClosure`(`gaps`・`unknown`・`unresolved`)。
  3 つとも空の時だけ `closed?` が真です(sim の土台は scheduler を含まないので、本番の土台の入れ忘れは sim では見つかりません)。

### 置き場所(能力)

- worker は `--provides a,b`(`WORKER_PROVIDES`)で提供する能力を、`--exclusive c`(`WORKER_EXCLUSIVE`・provides の一部)で専用の能力を
  名乗ります。専用の能力を持つ worker は、そのどれかを `:needs` に持つ job だけを受けます。
- `company-machine` は worker が自分で名乗れません。coordinator が worker の置かれた node(`--node`・`NODE_NAME`)の label
  `doeff.dev/company-machine=true` を読んで足します(`--naming` の `nodeCapabilities` で表を変えられます)。
- task・切り離した task・温める頼みも同じ `needs` を持ち、空の needs は断ります。

### 実行先の契約(HOST-CONTRACT)と job_entry

worker の子 process の入口は `hy -m doeff_cluster.job_entry service|task|probe --program FILE …` です。版(Python・cloudpickle・doeff)を
検めて詰めた Program を解き、`(run program)` するだけで、handler を 1 つも足しません。答えの無い effect はその場で上がり、process は
0 以外で終わります(worker が理由つきで起動し直します)。task の入口は結果を `--result` の file に書いた後、終わる前に coordinator の
`POST /tasks/<id>/result` へ結果を直接送ります。届かなかった時だけ、worker が file を読んで次の heartbeat で運びます(子が終了コード 0 で
終わった直後に worker が死んでも、結果は失われず、task は 2 回実行されません)。実行先が Program に提供するのは
`host_contract.HOST-CONTRACT` の 3 つだけです:

| 提供する物 | Program での読み方 |
|---|---|
| run-context(coordinator の URL・worker・job・process の世代) | `Ask HOST-CONTRACT.run-context-key`(`"doeff.cluster.run-context"`)→ `job_context.RunContext` |
| environ(宣言の `:environ`) | 子の環境変数。名の `Ask` に、値を字面どおりの文字列で答える(読みの定義 = `environ-reader` の 1 つ) |
| Program の path(記録の header に載せる) | `Ask HOST-CONTRACT.program-key`(`"doeff.cluster.program"`) |

本番では土台に並べる `host-reader` が 1 と 3 に、`(environ-reader)`(子の `os.environ` の上の読み)が 2 に答えます
(`host-reader` は session の値を使うので、その外側に `(state)` を置きます)。`sim-cluster` の偽の実行先は同じキーに同じ型で答え、
environ は同じ `environ-reader` を子の宣言の `:environ` の上に並べて答えます(本番と sim で同じ値 — JSON の object もそのまま)。

### 宣言する(declare)

```sh
hy -m doeff_cluster.shared.entry.declare myapp.systems:my_system --foundation myapp.foundation:production_foundation \
  --revision "$(git rev-parse HEAD)" [--only a,b] [--apply $COORD --actor $ME] [--replicas 0|1]
```

- 系の関数の module の在る git の checkout が汚れておらず push 済みで、HEAD が `--revision` と同じ commit の時だけ宣言します
  (詰める Program が参照するコードと、実行先が `--revision` で展開するコードを一致させるため)。外れれば理由つきで終了 2 です。
  土台の `:needs` が job の `:needs` に含まれない時も同じく終了 2 です。
- `--apply` を付けなければ、宣言の行(JSON)を標準出力に、job ごとの呼び出しの表示(`describe` — 関数の名と引数)を標準エラーに出します。
- `--apply` を付けると、詰めた Program を `PUT /programs/<sha>` で先に置き、Service ごとに無ければ `POST`・在れば読んだ
  `resourceVersion` を付けて `PUT` します(所有者と replicas は今の値を保ち、`--replicas` を付けた時だけ変えます)。
- 宣言の行は `{name revision needs run{kind program identity versions describe} environ readiness? update? runtimeEnv?}` です。
  同一性(入れ替えの要否を決める指紋)は、呼んだ関数の `module:qualname` と引数の正規の JSON・版・environ から作り、詰めた中身は
  比べません(cloudpickle の出力は同じ Program でも揺れるため)。
- `--config`・`--pin`・系の値(System)を直に指す形は受け付けません。

### 業務の effect を記録に載せる

記録と再生(下)は effect の型ごとの登録(`effect_codec.register`)を引きます。この package が登録するのは doeff の汎用の型
(`Ask`・doeff-time・scheduler)とこの package の型だけです。業務の型は、job の Program が import する業務の module で登録します。

```hy
(import doeff_cluster.shared.core.effect_codec [register EffectCodec READ DECISION OUTPUT])
(import myapp.effects [ReadRows WriteRow])
(register (EffectCodec ReadRows READ))
(register (EffectCodec WriteRow DECISION :subject (fn [args] (.get args "key")) :unexecuted True))
```

| 扱い | 再生での答え |
|---|---|
| `read` | 記録の答え。問い(型・引数・順番)が記録と違えば分岐 |
| `live` | 本物の scheduler が解く(順番だけ突き合わせる) |
| `decision` | 実行せず突き合わせる。対のキー `subject` と、対の無い時の答え `unexecuted` を宣言する |
| `output` | `decision` と同じ扱いで、報告の違いとして数える |

### 業務の repo の木の形(worker の引数)

worker は業務の repo の commit を 1 つ展開して子 process の cwd にします(実行環境の宣言 `runtimeEnv` を持つ job は、その root の venv で
動きます)。木の形は worker の引数で渡します(`worker_model.CodeLayout`)。

| 引数 | 意味 | 既定 |
|---|---|---|
| `--import-roots` | 子の PYTHONPATH に並べる木の中の dir(`,` で並べる・前が先)。bytecode の準備も同じ根で module 名を決める | `.` |
| `--base-pythonpath` | 木の根の後ろに並べる機体の絶対 path(image に焼かない土台の package を持つ機体の worker だけ) | 空 |

子 process の入口(`doeff_cluster.job_entry`)はこの package の物です。業務のコードの版は木が、クラスタの仕組みの版は worker の
実行環境(か実行環境の root)が決めます。

### 外の系と取り交わす名(coordinator の引数)

`--naming '<JSON>'`(`cluster_model.ClusterNaming`)。知らない欄は理由つきで断り、coordinator は起動しません:

| 欄 | 意味 | 既定 |
|---|---|---|
| `ownerAnnotation` | Rollout が台数を持つ Deployment に付ける annotation のキー | `doeff-cluster/replicas-owned-by` |
| `ownerScope` | その値の頭(`<scope>/Rollout/<名> replicas=<n>`) | `doeff-cluster` |
| `nodeCapabilities` | node の label から導く能力 `[{"label" "value" "capability"} …]` | `company-machine` を `doeff.dev/company-machine=true` から |

## 手元で確かめる(sim-cluster)

`doeff_cluster.sim.local.sim-cluster` は、sim の土台で作った系の値を、本物の coordinator と本物の worker の上で 1 process・仮想の時計で
走らせます。起動し直しの間隔・readiness の窓・handoff の期限・置き方・lease・fence は本物が決めます。

```hy
(<- answer (sim-cluster (my-system sim-foundation) (scenario)
                        :workers #((SimWorker :name "w1" :provides #{"cluster-net"}))
                        :environ {"my-writer" {"WRITER_MODE" "dry-run"}}))
```

- 偽の実行先は、`/programs` の詰めた文字列を解き直して(テストの object と共有しない)job ごとに別のスコープで走らせます。
  許可表(`host_contract.SIM-PASSABLE` — scheduler と時計の effect)の外の effect は、Program と実行先のどちらも答えなければ本番の子と
  同じ `UnhandledEffect` で落とします(テストの handler が本番に無い答えを黙って返さない)。
- `environ` は job ごとに宣言の `:environ` を上書きします(宣言に無い名は断ります)。
- `outside`(`SimOutside :handlers [...] :effects #(...)`)= sim の外の世界。本番では job の土台の handler が外の系(業務の store・外部の
  API)へ話して答える effect に、sim では系の外側に置いた模擬の handler が答えます。柵は `effects` に載った型(基底の型でよい)も外へ
  通します。job は外の世界を effect を通してだけ共有します(object を共有しない)。
- 筋書き(scenario)の中で使う effect: `Crash`・`Redeclare`・`ReportsOf`・`ReadinessOf`・`ProcessesOf`・`SharedRows`・`ReadCoordinator`・
  `StopCoordinator`・`CrashCoordinator`・`CoordinatorRuns`・`KillWorker`・`StopWorker`・`StartWorker`・`CutWorker`・`DrainWorker`・
  `PreparationsOf`・`ClientLink`。時間を進めるのは scenario の `Delay` です。
- 壁の時計で回すなら `wall-sim-cluster`(引数は `sim-cluster` と同じで、`start-ms` だけが無い — 今の時刻から始まります)。時計は
  doeff-time の `async-time-handler` と `await-handler` で、`Delay` は実時間で待ちます。外の thread の客・本物の待ち受け・実時間の遅れを
  確かめる検と、手元で系を実時間で回す道具に使います。筋書きは `Await` を出せます。job の `Await` は柵を通らないので、本物の I/O を持つ
  job は本番の土台と同じく自分の土台に `await-handler` を並べるか、その I/O を `outside` の handler に置きます。

  ```hy
  (<- answer (wall-sim-cluster (my-system sim-foundation) (scenario)
                               :workers #((SimWorker :name "w1" :provides #{"cluster-net"}))))
  ```
- 本番との既知の差: 1 process なので module の大域の状態は job の間で共有されうる・sim の土台は scheduler を含まない(本番の土台の
  入れ忘れは `foundation_check` で確かめる)・土台の `:needs` の漏れは見つからない。

## coordinator の HTTP

宛先を `COORD`、自分の名(依頼の主体の id か作業者の名)を `ME` とします。

| API | 返すもの |
|---|---|
| `GET /resources/Service`・`GET /resources/Service/<名>` | 定義(`spec`)・状態(`status`: 置き先・Ready か・drain の並べた置き先・handoff の段階・受け付けない行の `refused`)・`resourceVersion`・所有者 |
| `POST /resources/Service`・`PUT /resources/Service/<名>` | 定義を作る・書き換える(`declare --apply` が使う) |
| `PUT /programs/<sha>`・`GET /programs/<sha>` | 詰めた Program の置き場(`{"blob" "versions"}`)。中身の sha256 がキー |
| `GET /resources/Rollout/<名>` | Rollout の段階(`status.phase`)・旧の元の台数・台数の食い違い(`status.drift`) |
| `GET /resources/Worker` | worker の能力(`provides`・`exclusive`)・node・容量・版・drain |
| `GET /state` | 置き先(`placements`)・各 worker の process の様子・置けない service(`unplaced`)と理由 |
| `GET '/events?kind=Service&name=<名>&limit=50'` | 誰がいつ何を書いたか |
| `GET /metrics` | Prometheus の text(service の計器は label `service`・`worker` 付き・盤の行の数と大きさ・戻しの止まった Rollout) |
| `GET /livez`・`GET /readyz` | 調停ループが最後に要求を取りに来てからの秒だけで答える(readyz は 30 秒・livez は 120 秒止まると 503) |
| `POST /workers/<名>/drain`・`DELETE /workers/<名>/drain` | worker の drain の依頼と取り消し |
| `POST /leases/<名>` | 名前付きの lease(`op` = claim / renew / release / drop) |
| `POST /tasks`・`GET /tasks/<id>`・`DELETE /tasks/<id>` | task を出す(`{program(sha) revision needs name leaseSeconds format runtimeEnv?}`)・問い合わせる(lease を延ばす)・落とす |
| `POST /tasks/<id>/result` | task の子 process が終わる前に結果を送る(`{worker instance result format}`)。置いた worker からなら task を終える・終わった task には何もしない(200)・別の worker は 409・知らない task は 404 |
| `POST /warm`・`GET /warm/<キー>` | 実行環境の root を温める頼み(`{runtimeEnv needs ttlSeconds holder}`) |

書く時に守ること:

- 書き込みには header `X-Actor: $ME` が要ります(無いと 400)。
- 書き換えは版つきです。`GET` で読んだ `resourceVersion` を付けて `PUT` します。読んだ後に誰かが書いていれば 409 で何も書かれません。
- Service を消すのは所有者の `DELETE` だけです(所有者でなければ `?force=true`)。進行中の Rollout が扱っている Service は消せません。
- task と切り離した task の本文は Program の sha だけを運びます。先に `PUT /programs/<sha>` で置いてから送ります(置き場に無い sha は 400)。
- 盤の行に `"ttlSeconds": n` を付けると n 秒後に消えます。上限: 1 行 1 MiB・20,000 行・合計 64 MiB(越える書きは 507)。
  task は終わっていない物が 2,000 本まで(429)・lease は 1 時間まで。切り離した task の行(終わって結果を保持している物を含む)は
  10,000 本まで(429)。

## 切り離した task

`RemoteJob` の task は呼び手の問い合わせが lease を延ばし、呼び手が抜けると落ちます。呼び手より長く生きる仕事(呼び手の process が
入れ替わっても続けたい仕事)は、切り離した task として送ります。どちらも Program の値 1 つを送り、Program は自分の土台で包みます。

```hy
(import doeff_cluster.shared.intent.remote_model [RemoteJob])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached
                                      DetachedSucceeded DetachedFailed DetachedLost])
(val NET (frozenset ["cluster-net"]))      ; effect の needs は frozenset(空は断る)
(<- total (RemoteJob (add-task production-foundation n) :needs NET :name "add"))
(<- submitted (SubmitDetached (summarize production-foundation rows) :key job-id :needs NET :lease-seconds 60.0))
;; ... 呼び手が消えてもよい。別の process から同じ key で待てる ...
(<- outcome (AwaitDetached job-id))
```

| effect | 答え | 意味 |
|---|---|---|
| `SubmitDetached` | `DetachedSubmitted(key, created)` | job id(`key`)で冪等に送る。同じ key がまだ在れば何も作らない(`created` = False)。同じ key で name・needs が違えば `DetachedRefused`(409) |
| `AwaitDetached` | 答えの型か `DetachedPending` | 終わるまで待つ(`timeout-seconds` を過ぎたら `DetachedPending`)。抜けても task は落ちない |
| `CancelDetached` | `bool` | 終わっていなければ取り消して True。終わっていれば何もせず False(結果は保持) |
| `ReleaseDetached` | `bool` | 終わった task の保持を解く。以後その key は `DetachedUnknown` で、同じ key で送り直せる |

答えの型(失敗は例外ではなく値): `DetachedSucceeded`(値)・`DetachedFailed`(Program の例外)・`DetachedLost`(担い手の worker が
死んだ — 走らせ直さない)・`DetachedCancelled`・`DetachedVersionMismatch`(送り手と受け側の版が違う)・`DetachedUnrunnable`(能力の合う
worker が無い・コードを準備できない)・`DetachedUnknown`(知らない key)。

- **lease は担い手が延ばす**: 担い手の worker の heartbeat が lease を延ばします。呼び手の問い合わせは lease に触りません。worker が
  `lease-seconds` の間沈黙したら、その task は `DetachedLost` です。
- **世代**: task は置いた時の worker の process の世代(heartbeat の `boot`)に付きます。lease を延ばすのは置いた世代の heartbeat だけです。
  同じ名の別の世代(Pod を作り直した後の新しい process・preStop の間の旧い process)の heartbeat は、その task の lease を延ばしも
  lost にもしません。
- **結果の後の消失**: 結果を受け取った後に worker が死んでも、結果は変わりません。子 process は終わる前に結果を coordinator へ直接
  送るので、子が終わった直後(worker の次の heartbeat の前)に worker が死んでも結果は届いています。結果は `retain-seconds`(既定
  24 時間・30 日まで)か `ReleaseDetached` まで持ちます。
- **途絶**: worker は coordinator と途絶えても切り離した task を止めません(途絶が lease より長ければ coordinator が消失とし、再接続の
  返事から外れた時に止めます)。
- **drain**: drain は worker の上の切り離した task が 0 になるまで `Drained` になりません(task は移せないので終わるのを待つ)。
- handler: `detached-cluster`(coordinator の `/detached` の口と話す — `DetachedClient`)。手元では `sim-cluster` の偽の実行先が同じ
  要求の形で本物の coordinator へ送り、本物の worker が task を走らせます。担い手の死・停止・網の切断・drain・coordinator の停止は
  テストの effect(`KillWorker`・`StopWorker`・`StartWorker`・`CutWorker`・`DrainWorker`・`StopCoordinator`・`CrashCoordinator`)で再現します。

| HTTP | 意味 |
|---|---|
| `PUT /detached/<key>` | 送る `{program(sha) revision needs name leaseSeconds retainSeconds format runtimeEnv?}` → `{key task created phase}` |
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

記録係は job の Program の中に置きます(実行先は差し込みません)。`record_handlers.boundary-recorder` を翻訳の handler と土台の間に
並べると、`Ask "EFFECT_RECORD_MODE"` の答え(本番は宣言の `:environ` を `(environ-reader)` が読む)で選びます:

| mode | 置く物 |
|---|---|
| `off` | 何も置かない |
| `record` | 記録係。置き場は `Ask "EFFECT_RECORD_OTLP"`(OpenTelemetry の collector の URL)。記録の 1 行は log record 1 件で、header は run-context の世代と Program の置き場のキーと版 |
| `replay` | 再生係。状態は `Ask "doeff.record.replay-state"` の答え |

- 記録係より内側の handler(翻訳の handler・業務の handler)は決定的でなければなりません。時計・乱数・I/O は汎用の effect にして
  土台の handler に答えさせます(破れは再生の分岐として出ます)。
- 再生は `hy -m doeff_cluster.shared.entry.replay_main --recording FILE --program FILE [--from-ms N] [--to-ms N] --out FILE` です。`--program` は
  記録した job の詰めた Program(`/programs/<sha>` の JSON — header の `program` と同じキー)。版を検めて解き、上の 2 つの Ask にだけ
  外から答えて走らせます。版(Python・cloudpickle・doeff)と記録した commit のコードが揃う間だけ再生でき、違えば理由つきで止まります。
- 記録の形は `record_model.hy` の先頭、型ごとの扱いは上の表です。

## 配備の材料

- `deploy/boot.sh` — Pod と手元の機体で共通の起動 script。`ROLE` = `coordinator` / `worker` / `records` / `drain`(preStop)/ `ready`
  (readinessProbe)/ `access`。worker は `WORKER_PROVIDES`・`WORKER_EXCLUSIVE`・`NODE_NAME` で能力と node を名乗り(旧い
  `WORKER_LABELS` は起動しない)、`CODE_REPO_URL` の bare mirror を用意してその版を展開します。env は script の先頭の註。
- `deploy/Dockerfile` — 業務の image(doeff の venv を持つ物・この package を含む)に git と ssh を足し、`boot.sh` を置くだけの image。
  `--build-arg BASE=<業務の image>`。
- `deploy/base/Dockerfile` — 土台だけの image(OS・git・ssh・uv・Rust の toolchain・tini・`boot.sh`)。doeff も Python も持たず、
  `boot.sh` が `WORKER_DOEFF_COMMIT` の doeff を展開して `uv sync --locked --package doeff-cluster` した venv から coordinator / worker を
  起動します(自己起動)。worker のコードを変える時は commit を変えて入れ替え、image は作り直しません。root を用意した後は、起動の
  script も root の中の同じ commit の `deploy/boot.sh` へ引き継ぐので、起動の script を直した時も image は作り直しません。作り直す理由は頭の註の 2 種類
  だけで、それ以外の変更は `hy -m doeff_cluster.shared.entry.image_contract <Dockerfile>`(と `tests/test_base_image_contract.hy`)が赤にします。
  非公開の repo は `WORKER_REPOS`(url ごとの読み取り専用の deploy key)で読みます。`ROLE=access` で書かれる設定だけを確かめられます。

manifest(namespace・node・Secret・Role)は配備する側の repo が持ちます。coordinator の ServiceAccount には、Rollout が扱う
Deployment の get・scale と、能力を導くための nodes の get が要ります。

### 配備の順

coordinator を先に上げ、その後に worker を入れ替えます。worker の preStop(`ROLE=drain`)は drain の頼みに自分の process の世代
(`boot`)を載せ、coordinator は今の世代でない頼み(退いた世代・一度も見ていない世代)を同じ名の今の世代に付けません。古い版の
coordinator はこの欄を読まないので、先に worker だけを上げても効きません。

- 同じ名の Pod が並ぶ worker(node の dir の lock を持たない Deployment の worker)では、新しい coordinator を上げた後の最初の入れ替え
  だけ、新しい Pod が旧い Pod の preStop が終わってから最長 150 秒 NotReady のままになり得ます。旧い版の preStop は `boot` を載せないので、
  その頼みが新しい世代に drain を付けるためです(期限 = preStop の上限 90 秒 + 余裕 60 秒)。
- 急ぐ時は、旧い Pod が終わった後に `DELETE /workers/<名>/drain` を実行して drain を解きます。
- worker は入口の検めの間の job を phase `probing` で報告します。coordinator はこの phase を「その worker で起動しかけている」と
  数えます(他へ置かない)。旧い版の coordinator はこの phase を知らないので、ここでも coordinator を先に上げます。

## テスト

```sh
# doeff の repo の根から(日次の make test-packages と同じ形)
uv run --no-sync pytest packages/doeff-cluster/tests -q
```

検は Hy の `test_*.hy`(deftest)で、`tests/conftest.py` が doeff-adr の Hy の file の収集をこの dir に掛けます(module 名は
`tests.<名>`)。

`tests/test_no_application_vocabulary.hy` は、この package(source・検・配備の材料・文書)に業務の系の語が混ざっていないことを確かめます。
