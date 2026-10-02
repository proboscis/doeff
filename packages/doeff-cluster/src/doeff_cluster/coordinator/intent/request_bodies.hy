;;; coordinator の受け口の要求の本文の型(道ごと — #2445)。受付(foundation/coordinator_inbox)が受けた JSON の本文は、protocol の 1 点
;;; (coordinator/protocol/request_bodies)で道ごとの型に解かれ、core の判断は型の値だけを受ける。まだ型にしていない道の本文は JSON の
;;; object のまま運ぶ(道の群ごとに型へ移す — #2445 の子)。
;;;   LeaseBody       POST /leases/<名>           lease の操作 1 つ(lease_rules.lease-op)
;;;   TaskResultBody  POST /tasks/<id>/result     task の子 process が直に届ける結果(#1387)
;;;   DrainBody       POST /workers/<名>/drain    worker の Pod の drain の頼み
;;;   ReadinessBody   POST /resources/Service/<名>/readiness   service の process の準備できたの報告
;;;   MetricsBody     POST /resources/Service/<名>/metrics     service の process の計器の報告
;;;   ProgramBody     PUT /programs/<sha>         詰めた Program の置き
;;;   HeartbeatBody   POST /heartbeat             worker の生存・能力・版・状態の報告・実行環境の root の名乗り
;;;   ResourceBody    POST /resources/<種類>・PUT /resources/<種類>/<名>   資源の宣言の包み(name・spec・resourceVersion)
;;;   TaskBody        POST /tasks・PUT /detached/<key>   task と切り離した task の頼み
;;;   WarmBody        POST /warm                  温める表の行
;;;   LegacyJobsBody  PUT /jobs                   旧い宣言の口(jobs の行の列と送り手)
;;;   BoardWrite      PUT /board/<鍵>             盤の行 1 つの compare-and-set(本文の型 BoardWireBody と、欄が在ったかの印)
;;; 知らない欄は読み捨てる(前の直の読みと同じ — 送り手の版が新しい欄を足しても断らない)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defwire defrecord])
(val MODULE-TAGS {:context "coordinator" :role "type"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defwire LeaseBody
  "POST /leases/<名> の本文: op = 操作(claim・renew・release・drop)・token = 担い手の印・permits = 同時の担い手の上限・
   ttl-ms = lease の長さ(0 = 操作の既定)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str op)
  (#^ str token)
  (setv #^ int permits 1)
  (setv #^ int ttl-ms 0))


(defwire TaskResultBody
  "POST /tasks/<id>/result の本文: worker = 送り手の子 process の担い手の名・result = 詰めた結果・instance = 送り手の世代の名・
   format = 本文の形の版(shared/protocol/task_result の task-result-request)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str worker)
  (#^ str result)
  (setv #^ str instance "")
  (setv #^ int format 1))


(defwire DrainBody
  "POST /workers/<名>/drain の本文: ttl-seconds = drain の期限(None = 既定 — 範囲の検めは drain_policy)・boot = 頼み手の worker の
   process の世代(None = 世代を名乗らない旧い頼み)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| int float None) ttl-seconds None)
  (setv #^ (| str None) boot None))


(defwire DurationRow
  "計器の報告の duration 1 つ: sum = 合計の秒・count = 回数。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ (| int float) sum)
  (#^ (| int float) count))


(defwire MetricsPayload
  "計器の報告の中身: counters・gauges = 名 → 値・durations = 名 → DurationRow(None = 欄が無い — 空と読む・名の綴りと数の上限の
   検めは metrics_policy)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str (| int float))) None) counters None)
  (setv #^ (| (get dict #(str (| int float))) None) gauges None)
  (setv #^ (| (get dict #(str DurationRow)) None) durations None))


(defwire ReadinessBody
  "POST /resources/Service/<名>/readiness の本文: 送り手の process の世代(worker・revision・pid・instance・attempt・spec-hash・
   placement — job_context.RunContext の identity と同じ欄)と、ready = 準備できたか・reason = 理由・role = active か standby
   (None = 旧い報告 — active と読む)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str worker)
  (#^ str revision)
  (#^ bool ready)
  (setv #^ (| int None) pid None)
  (setv #^ (| str None) instance None)
  (setv #^ (| str int None) attempt None)
  (setv #^ (| str None) spec-hash None)
  (setv #^ (| int None) placement None)
  (setv #^ str reason "")
  (setv #^ (| str None) role None))


(defwire MetricsBody
  "POST /resources/Service/<名>/metrics の本文: 送り手の process の世代(ReadinessBody と同じ欄)と metrics = 計器の中身。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str worker)
  (#^ str revision)
  (#^ MetricsPayload metrics)
  (setv #^ (| int None) pid None)
  (setv #^ (| str None) instance None)
  (setv #^ (| str int None) attempt None)
  (setv #^ (| str None) spec-hash None)
  (setv #^ (| int None) placement None))


(defwire ProgramBody
  "PUT /programs/<sha> の本文: blob = 詰めた Program(base64 の文字列)・versions = 詰めた送り手の版(名 → 版)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str blob)
  (setv #^ (| (get dict #(str str)) None) versions None))


(defwire BoardWireBody
  "PUT /board/<鍵> の本文の形: value = 書く値・expect = 比べる値(どちらも呼び手の任意の JSON — 盤は値の形を決めない)・expect-version =
   行の版で比べる(None = 比べない)・delete = 行を消す・ttl-seconds = 行の期限(None = 期限なし — 範囲の検めは board_rules)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ object value None)
  (setv #^ object expect None)
  (setv #^ (| int None) expect-version None)
  (setv #^ bool delete False)
  (setv #^ (| int float None) ttl-seconds None))


(defrecord BoardWrite
  "盤の書き 1 つ(PUT /board/<鍵> の本文を解いた値): body = 本文の型の値・value-given / expect-given = 本文に value / expect の欄が
   在ったか(null の値と欄が無いことを分ける — expect が null なら『行が無い時だけ書く』・欄が無ければ比べない)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ BoardWireBody body)
  (#^ bool value-given)
  (#^ bool expect-given))


(defwire EnvFailedRow
  "heartbeat の root の名乗りの失敗の行 1 つ: key = env のキー・kind = 失敗の種類・detail = 理由・retryable = やり直してよいか。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str key)
  (#^ str kind)
  (setv #^ str detail "")
  (setv #^ bool retryable False))


(defwire ProbeRow
  "状態の報告の行の入口の検めの姿(検めが通っていない間だけ載る — worker/protocol/heartbeat の status-row の probe): state = 検めの
   状態・elapsed-seconds = 今の検めの経過の秒・attempts = 回数・last-failure = 直前の失敗の理由。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str state)
  (setv #^ int elapsed-seconds 0)
  (setv #^ int attempts 0)
  (setv #^ str last-failure ""))


(defwire StatusRow
  "heartbeat の状態の報告の行 1 つ(worker/protocol/heartbeat の status-row と同じ形 — #2447 で dict からこの型にした): name = job の名
   (task は task/<id>)・phase・desired-revision / running-revision・pid・attempts・detail・instance = process の世代・spec-hash・
   placement = 割り当ての世代・retired-from = 入れ替えで退いた process の元の名・failure-kind / retryable = 実行環境の準備の失敗
   (env-failed の行だけ)・probe = 入口の検めの姿・result = 終わった task の詰めた結果・task = 切り離した task の写し(引き取りが
   読む — 形の検めは cluster_policy.adopted-task)。worker の載せない欄は None(黙って既定の値へ倒さない)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str name "")
  (setv #^ (| str None) phase None)
  (setv #^ (| str None) desired-revision None)
  (setv #^ (| str None) running-revision None)
  (setv #^ (| int None) pid None)
  (setv #^ int attempts 0)
  (setv #^ str detail "")
  (setv #^ (| str None) instance None)
  (setv #^ (| str None) spec-hash None)
  (setv #^ (| int None) placement None)
  (setv #^ (| str None) retired-from None)
  (setv #^ (| str None) failure-kind None)
  (setv #^ (| bool None) retryable None)
  (setv #^ (| ProbeRow None) probe None)
  (setv #^ (| str None) result None)
  (setv #^ (| dict None) task None))


(defwire EnvsReport
  "heartbeat の実行環境の root の名乗り: ready・preparing = env のキーの列・failed = 失敗の行の列(worker/protocol/heartbeat の
   env-heartbeat-part と同じ形)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (get tuple #(str ...)) ready #())
  (setv #^ (get tuple #(str ...)) preparing #())
  (setv #^ (get tuple #(EnvFailedRow ...)) failed #()))


(defwire HeartbeatBody
  "POST /heartbeat の本文(worker/protocol/heartbeat の heartbeat-body と env-heartbeat-part と同じ形): name = worker の名(空でない)・
   provides / exclusive = 能力の名の列(labels = 旧い形の名乗り — 判断が断る)・node・capacity・versions / tools = 名 → 版・platform・
   envs = root の名乗り・env-capacity = disk の条件・statuses = 状態の報告の行の列(StatusRow)・
   endpoint・boot = process の世代・boot-at = 起動時刻(epoch ms)・format = 本文の形の版・kept-when-cut-off = 途絶しても動かし続けてよい印を
   今持っている job の名の列(#2804 — None = 欄の無い古い worker = 印を知らない)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore :check [(> (len name) 0)]}
  (#^ str name)
  (setv #^ (| (get tuple #(str ...)) None) provides None)
  (setv #^ (| (get tuple #(str ...)) None) exclusive None)
  (setv #^ object labels None)
  (setv #^ str node "")
  (setv #^ int capacity 10)
  (setv #^ (| (get dict #(str str)) None) versions None)
  (setv #^ (| (get dict #(str str)) None) tools None)
  (setv #^ str platform "")
  (setv #^ (| EnvsReport None) envs None)
  (setv #^ str env-capacity "ok")
  (setv #^ (get tuple #(StatusRow ...)) statuses #())
  (setv #^ (| str None) endpoint None)
  (setv #^ (| str None) boot None)
  (setv #^ (| int None) boot-at None)
  (setv #^ int format 1)
  (setv #^ (| (get tuple #(str ...)) None) kept-when-cut-off None))


(defwire ResourceBody
  "資源の宣言(POST /resources/<種類> と PUT /resources/<種類>/<名>)の包み: name = 資源の名(POST だけ — 形の検めは resource_policy)・
   spec = 宣言の中身(JSON の object — Service の行は job-from-json・Rollout は validate-rollout-spec が読む。行の型は #2447)・
   resource-version = 読んだ時の版(PUT — 古い版の書きは 409)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| str None) name None)
  (setv #^ (| dict None) spec None)
  (setv #^ (| int None) resource-version None))


(defwire TaskBody
  "POST /tasks と PUT /detached/<key> の本文: program = 置き場に置いた Program の sha・revision = 送り手の commit・name・needs = 要る能力の
   名の列・runtime-env = 実行環境の宣言(JSON の object)・environ = 子の環境変数(名 → 文字列)・lease-seconds / retain-seconds(None = 道の
   既定)・format。旧い形の欄(requires・env・blob・versions)は判断が理由つきで断る。欄の中身の規則(sha の形と置き場に在るか・needs の名・
   環境変数の名・宣言の形)は判断が読む — 判断の理由の文を前のまま保つため、ここでは欄の在否と大きな型だけを決める。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ object program None)
  (setv #^ object revision None)
  (setv #^ str name "")
  (setv #^ object needs None)
  (setv #^ object requires None)
  (setv #^ object env None)
  (setv #^ object blob None)
  (setv #^ object versions None)
  (setv #^ object runtime-env None)
  (setv #^ object environ None)
  (setv #^ object lease-seconds None)
  (setv #^ object retain-seconds None)
  (setv #^ object format 1))


(defwire WarmBody
  "POST /warm の本文: runtime-env = 温める実行環境の宣言・ttl-seconds = 行の期限・needs(と旧い requires)= 要る能力・holder = 頼み手
   (None = 送り手)。中身の規則は warm_policy が読む。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ object runtime-env None)
  (setv #^ object ttl-seconds None)
  (setv #^ object needs None)
  (setv #^ object requires None)
  (setv #^ (| str None) holder None))


(defwire LegacyJobsBody
  "旧い PUT /jobs の本文: jobs = 宣言の行の列(行の形は判断が読む — 行の型は #2447)・actor = 送り手(header X-Actor が無い時)。"
  {:tags {:context "coordinator" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ (get tuple #(dict ...)) jobs)
  (setv #^ (| str None) actor None))


(defrecord BodyMalformed
  "道の本文が型の約束の形でない(欠けた欄・型の違う値・JSON の object でない本文)— 受け口は 400 と reason で断る。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str reason))


(defrecord BodyUnreadable
  "道の本文の読み(ReadBody)の中で例外が上がった — 調停ループが値にして判断 respond へ渡し、respond が自分の欠陥の囲みの中で上げ直して、
   送り手の誤り(BodyInvalid → 400)か coordinator の欠陥(→ 500 の Fault・上がった所つき)かを 1 か所で決める(#2796 — 本文の読みが判断の
   外へ移った後、読みの中の例外が調停ループの外まで抜けて coordinator の process ごと落ちた)。error = 上がった例外。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ Exception error))


;; 道の本文の答えの型の和(ReadBody の答え・判断 respond が受ける本文 — まだ型にしていない道は JSON の object)。
(setv RequestBody (| LeaseBody TaskResultBody DrainBody ReadinessBody MetricsBody ProgramBody BoardWrite HeartbeatBody ResourceBody TaskBody WarmBody LegacyJobsBody BodyMalformed dict))


(defclass [(dataclass :frozen True)] ReadBody [EffectBase]
  "受けた要求 1 件(shared/intent/protocol の Request)の本文を、その道の型に解く。答え = 道の型の値(LeaseBody など)・まだ型にして
   いない道は JSON の object(本文が無ければ空)・形が合わなければ BodyMalformed。答え手 = coordinator/protocol/request_bodies。"
  (#^ object request))
