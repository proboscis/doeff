;;; coordinator の返事の本文を JSON の形に綴る 1 点(#2595)。core の判断は返事を型の値(EventsView・StateReply)で返し、ここが外の JSON の形
;;; にする。返事の型にまだしていない道の本文(JSON の object のまま)は、そのまま通す。
;;;   reply-json    返事の本文 → JSON の形(byte にするのは shared/protocol/inbox の encoded-reply)
;;;   reply-bodies  Reply の答え手: 本文を reply-json で綴って Reply を出し直す(本番と模擬の組のいちばん内側に置く)
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff_cluster.shared.intent.protocol [Reply])
(import dataclasses [asdict])
(import json)
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.coordinator.intent.cluster_model [EventsView StateReply HeartbeatReply TaskOffer])
(import doeff_cluster.coordinator.protocol.state_json [audit-event-to-json])


(defn #^ dict spec-json [#^ JobSpec spec]
  (| {"name" spec.name "entry" spec.entry "args" (list spec.args) "revision" spec.revision "once" spec.once}
     ;; Program の job だけ(改訂 1 の F・G): 詰めた Program の置き場のキー(worker が /programs/<sha> から取る)と子の環境変数。
     (if spec.program {"program" spec.program} {})
     (if spec.environ {"environ" (dict spec.environ)} {})
     (if (is spec.placement None) {} {"placement" spec.placement})
     ;; 実行環境の job だけ: 宣言の JSON(worker が env の root を準備し、版を env のキーへ置き換える)。
     (if (is spec.runtime-env None) {} {"runtimeEnv" (json.loads spec.runtime-env)})
     ;; 入れ替え(handoff)の job だけ: 形と、coordinator が Ready と数えている process の世代の名(worker は旧をこの後に止める)。
     (if spec.handoff {"handoff" True "readyInstance" spec.ready-instance} {})
     ;; 入れ替えを諦めた job だけ(2026-09-26 — handoff_policy の期限): worker は新を止めて起こし直さず、旧を動かし続ける。
     (if (and spec.handoff spec.handoff-abandoned) {"handoffAbandoned" True} {})))


(defn #^ dict task-offer-json [#^ TaskOffer offer]
  "返事の task 1 つ → JSON の形(#2595 の前に cluster_policy.tasks-for が組んでいた形と同じ — 切り離した task の欄・実行環境・環境変数は在る時だけ)。"
  (| {"id" offer.id "name" offer.name "revision" offer.revision "versions" (dict offer.versions) "program" offer.program}
     (if offer.detached {"detached" True "key" offer.key "leaseMs" offer.lease-ms "retainMs" offer.retain-ms "needs" (list offer.needs)} {})
     (if (is-not offer.runtime-env None) {"runtimeEnv" offer.runtime-env} {})
     (if offer.environ {"environ" (dict offer.environ)} {})))


(defn #^ dict heartbeat-reply-json [#^ HeartbeatReply reply]
  "heartbeat の返事 → JSON の形(superseded は退いた世代への返事の時だけ書く)。"
  (| {"jobs" (lfor s reply.jobs (spec-json s)) "tasks" (lfor t reply.tasks (task-offer-json t))
      "warm" (lfor w reply.warm {"key" w.key "runtimeEnv" w.runtime-env}) "timing" (asdict reply.timing)
      "draining" reply.draining "formats" (list reply.formats) "revision" reply.revision}
     (if reply.superseded {"superseded" True} {})))


(defn #^ object reply-json [#^ object body]  ; defk にできない: 返事の答え手と検の入口 responded(Program の外)が呼ぶ純粋な綴り
  "返事の本文の型の値を、外へ見せる JSON の形にする(#2595 の前に core が組んでいた形と同じ)。型にしていない本文はそのまま返す。"
  (cond
    (isinstance body EventsView)
      {"revision" body.revision "seq" body.seq "events" (lfor e body.events (audit-event-to-json e))}
    (isinstance body StateReply)
      (| body.view {"audit" (lfor e body.audit (audit-event-to-json e)) "drains" body.drains})
    (isinstance body HeartbeatReply) (heartbeat-reply-json body)
    True body))


(defhandler reply-bodies
  ;; 引数なし: 返事の本文の型だけから綴る(返事を送るのは外側の Reply の答え手)。
  (Reply [request status body]
    (<- (Reply request status (reply-json body)))
    (resume None)))
