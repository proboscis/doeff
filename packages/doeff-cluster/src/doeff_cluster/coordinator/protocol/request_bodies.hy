;;; coordinator の受け口の要求の本文を道ごとの型に解く 1 点(#2445)。どの道がどの型か(body-type-of)をここだけが知り、core の判断は
;;; 解いた値だけを受ける。本番の調停ループは ReadBody の答え手 request-bodies で、判断を直に呼ぶ検と模擬の世界は responded で同じ解きを通る。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff [run])
(import doeff_hy.wire [parse Malformed])
(import doeff_cluster.shared.intent.protocol [Request ClusterTiming BodyInvalid])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ServiceBody LegacyJobRow LegacyJobs])
(import doeff_cluster.coordinator.core.cluster_policy [job-from-json])
(import doeff_cluster.coordinator.core.cluster_rules [required-field])
(import doeff_cluster.coordinator.intent.request_bodies [LeaseBody TaskResultBody DrainBody ReadinessBody MetricsBody ProgramBody BoardWireBody BoardWrite HeartbeatBody ResourceBody TaskBody WarmBody LegacyJobsBody BodyMalformed BodyUnreadable ReadBody RequestBody])
(import doeff_cluster.coordinator.core.api_policy [respond])
(import doeff_cluster.coordinator.protocol.replies [reply-json])


(defn #^ (| type None) body-type-of [#^ str method #^ tuple parts]  ; defk にできない: 道の振り分けの純粋な表(内包と条件の中で読む)
  "道(method と path の区切り)→ 本文の型(まだ型にしていない道は None — JSON の object のまま運ぶ)。"
  (cond
    (and (= method "POST") (= (len parts) 2) (= (get parts 0) "leases")) LeaseBody
    (and (= method "POST") (= (len parts) 3) (= (get parts 0) "tasks") (= (get parts 2) "result")) TaskResultBody
    (and (= method "POST") (= (len parts) 3) (= (get parts 0) "workers") (= (get parts 2) "drain")) DrainBody
    (and (= method "POST") (= (len parts) 4) (= (get parts 0) "resources") (= (get parts 1) "Service") (= (get parts 3) "readiness"))
      ReadinessBody
    (and (= method "POST") (= (len parts) 4) (= (get parts 0) "resources") (= (get parts 1) "Service") (= (get parts 3) "metrics"))
      MetricsBody
    (and (= method "PUT") (= (len parts) 2) (= (get parts 0) "programs")) ProgramBody
    (and (= method "PUT") (> (len parts) 1) (= (get parts 0) "board")) BoardWireBody
    (and (= method "POST") (= parts #("heartbeat"))) HeartbeatBody
    (and (= method "POST") (= (len parts) 2) (= (get parts 0) "resources")) ResourceBody
    (and (= method "POST") (= parts #("tasks"))) TaskBody
    (and (= method "PUT") (= (len parts) 2) (= (get parts 0) "detached")) TaskBody
    (and (= method "POST") (= parts #("warm"))) WarmBody
    (and (= method "PUT") (= parts #("jobs"))) LegacyJobsBody
    (and (= method "PUT") (= (len parts) 3) (= (get parts 0) "resources")) ResourceBody
    True None))


(defk service-body-of [body parts]
  {:pre [(: body ResourceBody) (: parts tuple)] :post [(: % (| ServiceBody BodyMalformed))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "Service の資源の本文 → 宣言の型(#2448 — 前は core の資源の判断が spec の JSON を job-from-json で読んでいた)。名は PUT なら path の名・POST なら
   本文の name。宣言の行が読めなければ BodyMalformed(400)。所有者は本文の owner のまま運び、決めるのは判断。"
  (val name (if (= (len parts) 3) (get parts 2) body.name))
  (val spec (dict (or body.spec {})))
  (try
    (ServiceBody :name name :job (job-from-json (| spec {"name" (or name "")})) :owner (.get spec "owner")
                 :resource-version body.resource-version)
    (except [error BodyInvalid]
      (BodyMalformed :reason (str error)))))


(defk legacy-row-of [row]
  {:pre [(: row dict)] :post [(: % LegacyJobRow)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "旧い PUT /jobs の行 1 つ → 宣言の型。名の無い・空の行は BodyInvalid。replicas と readiness は行に在るかを印に残す(無ければ判断が今の
   宣言の値で埋める)。"
  (val name (required-field row "name"))
  (when (not (and (isinstance name str) name))
    (raise (BodyInvalid (.format "jobs の行の名前は空でない文字列: {!r}" name))))
  (LegacyJobRow :name name :version (.get row "resourceVersion") :owner (.get row "owner")
                :job (job-from-json (dfor #(k v) (.items row) :if (!= k "resourceVersion") k v))
                :replicas-given (in "replicas" row) :readiness-given (in "readiness" row)))


(defk legacy-jobs-of [body]
  {:pre [(: body LegacyJobsBody)] :post [(: % (| LegacyJobs BodyMalformed))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "旧い PUT /jobs の本文 → 行ごとの宣言の型(#2448)。1 行でも読めなければ BodyMalformed(400 — 何も書かない)。"
  (var rows #())
  (try
    (for [row body.jobs]
      (<- read LegacyJobRow (legacy-row-of row))
      (:= rows (+ rows #(read))))
    (LegacyJobs :rows rows :actor body.actor)
    (except [error BodyInvalid]
      (BodyMalformed :reason (str error)))))


(defk body-of [request]
  {:pre [(: request Request)] :post [(: % (| RequestBody ServiceBody LegacyJobs))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "要求の本文をその道の型に解くため(答えは ReadBody と同じ)。本文が JSON の object でなければ BodyMalformed(口はどれも object の本文を
   読む — 型の外の本文を読み進めない)。"
  (val raw (or request.body {}))
  (val wire (body-type-of request.method (tuple request.parts)))
  (cond
    (not (isinstance raw dict))
      (BodyMalformed :reason (.format "本文は JSON の object: {}" (. (type raw) __name__)))
    (is wire None) raw
    True
      (do (<- parsed (parse wire raw))
          (cond
            (isinstance parsed Malformed)
              (BodyMalformed :reason (.join "・" (gfor f parsed.fields (.format "{}: {}" (or f.field "本文") f.reason))))
            ;; 盤の書きは value と expect の『欄が無い』と『null』を分けるので、欄が在ったかの印を添える(この 1 点だけが本文の欄の在否を読む)。
            (isinstance parsed BoardWireBody)
              (BoardWrite :body parsed :value-given (in "value" raw) :expect-given (in "expect" raw))
            ;; Service の宣言と旧い /jobs の行は、宣言の型に読んでから判断へ渡す(#2448)。
            (and (isinstance parsed ResourceBody) (= (get request.parts 1) "Service")) (! (service-body-of parsed (tuple request.parts)))
            (isinstance parsed LegacyJobsBody) (! (legacy-jobs-of parsed))
            True parsed))))


(defhandler request-bodies
  ;; 受けた要求の本文を道の型に解く(頭の註)。
  (ReadBody [request]
    (<- body (body-of request))
    (resume body)))


(defn #^ tuple responded [#^ ClusterState state #^ Request request #^ int now #^ ClusterTiming timing]  ; defk にできない: 判断を直に呼ぶ検と模擬の世界(Program の外)が呼ぶ
  "要求 1 件の本文を道の型に解いてから判断(api_policy.respond)に答えさせ、返事の本文を JSON の形に綴る — 本番の調停ループの ReadBody と
   返事の答え手 reply-bodies と同じ解きと綴り。読みの中で上がった例外は、調停ループ(program.readable-body)と同じく値 BodyUnreadable にして
   判断に渡す(#2796)。"
  (setv read (try (run (body-of request)) (except [error Exception] (BodyUnreadable :error error))))
  (setv #(after status body) (respond state request now timing read))
  #(after status (run (reply-json body))))
