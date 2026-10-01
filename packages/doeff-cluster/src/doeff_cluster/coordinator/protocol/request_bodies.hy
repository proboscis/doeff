;;; coordinator の受け口の要求の本文を道ごとの型に解く 1 点(#2445)。どの道がどの型か(body-type-of)をここだけが知り、core の判断は
;;; 解いた値だけを受ける。本番の調停ループは ReadBody の答え手 request-bodies で、判断を直に呼ぶ検と模擬の世界は responded で同じ解きを通る。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff [run])
(import doeff_hy.wire [parse Malformed])
(import doeff_cluster.shared.intent.protocol [Request ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.intent.request_bodies [LeaseBody TaskResultBody DrainBody ReadinessBody MetricsBody ProgramBody BoardWireBody BoardWrite HeartbeatBody BodyMalformed ReadBody RequestBody])
(import doeff_cluster.coordinator.core.api_policy [respond])


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
    True None))


(defk body-of [request]
  {:pre [(: request Request)] :post [(: % RequestBody)]}
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
            True parsed))))


(defhandler request-bodies
  ;; 受けた要求の本文を道の型に解く(頭の註)。
  (ReadBody [request]
    (<- body (body-of request))
    (resume body)))


(defn #^ tuple responded [#^ ClusterState state #^ Request request #^ int now #^ ClusterTiming timing]  ; defk にできない: 判断を直に呼ぶ検と模擬の世界(Program の外)が呼ぶ
  "要求 1 件の本文を道の型に解いてから判断(api_policy.respond)に答えさせる — 本番の調停ループの ReadBody と同じ解き。"
  (respond state request now timing (run (body-of request))))
