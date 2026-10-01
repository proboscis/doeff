;;; coordinator の受け口の要求の本文を道ごとの型に解く 1 点(#2445)。どの道がどの型か(body-type-of)をここだけが知り、core の判断は
;;; 解いた値だけを受ける。本番の調停ループは ReadBody の答え手 request-bodies で、判断を直に呼ぶ検と模擬の世界は responded で同じ解きを通る。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff [run])
(import doeff_hy.wire [parse Malformed])
(import doeff_cluster.shared.intent.protocol [Request ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.intent.request_bodies [LeaseBody TaskResultBody DrainBody BodyMalformed ReadBody RequestBody])
(import doeff_cluster.coordinator.core.api_policy [respond])


(defn #^ (| type None) body-type-of [#^ str method #^ tuple parts]  ; defk にできない: 道の振り分けの純粋な表(内包と条件の中で読む)
  "道(method と path の区切り)→ 本文の型(まだ型にしていない道は None — JSON の object のまま運ぶ)。"
  (cond
    (and (= method "POST") (= (len parts) 2) (= (get parts 0) "leases")) LeaseBody
    (and (= method "POST") (= (len parts) 3) (= (get parts 0) "tasks") (= (get parts 2) "result")) TaskResultBody
    (and (= method "POST") (= (len parts) 3) (= (get parts 0) "workers") (= (get parts 2) "drain")) DrainBody
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
          (if (isinstance parsed Malformed)
              (BodyMalformed :reason (.join "・" (gfor f parsed.fields (.format "{}: {}" (or f.field "本文") f.reason))))
              parsed))))


(defhandler request-bodies
  ;; 受けた要求の本文を道の型に解く(頭の註)。
  (ReadBody [request]
    (<- body (body-of request))
    (resume body)))


(defn #^ tuple responded [#^ ClusterState state #^ Request request #^ int now #^ ClusterTiming timing]  ; defk にできない: 判断を直に呼ぶ検と模擬の世界(Program の外)が呼ぶ
  "要求 1 件の本文を道の型に解いてから判断(api_policy.respond)に答えさせる — 本番の調停ループの ReadBody と同じ解き。"
  (respond state request now timing (run (body-of request))))
