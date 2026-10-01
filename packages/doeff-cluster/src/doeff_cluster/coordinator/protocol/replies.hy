;;; coordinator の返事の本文を JSON の形に綴る 1 点(#2595)。core の判断は返事を型の値(EventsView・StateReply)で返し、ここが外の JSON の形
;;; にする。返事の型にまだしていない道の本文(JSON の object のまま)は、そのまま通す。
;;;   reply-json    返事の本文 → JSON の形(byte にするのは shared/protocol/inbox の encoded-reply)
;;;   reply-bodies  Reply の答え手: 本文を reply-json で綴って Reply を出し直す(本番と模擬の組のいちばん内側に置く)
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff_cluster.shared.intent.protocol [Reply])
(import doeff_cluster.coordinator.intent.cluster_model [EventsView StateReply])
(import doeff_cluster.coordinator.protocol.state_json [audit-event-to-json])


(defn #^ object reply-json [#^ object body]  ; defk にできない: 返事の答え手と検の入口 responded(Program の外)が呼ぶ純粋な綴り
  "返事の本文の型の値を、外へ見せる JSON の形にする(#2595 の前に core が組んでいた形と同じ)。型にしていない本文はそのまま返す。"
  (cond
    (isinstance body EventsView)
      {"revision" body.revision "seq" body.seq "events" (lfor e body.events (audit-event-to-json e))}
    (isinstance body StateReply)
      (| body.view {"audit" (lfor e body.audit (audit-event-to-json e)) "drains" body.drains})
    True body))


(defhandler reply-bodies
  ;; 引数なし: 返事の本文の型だけから綴る(返事を送るのは外側の Reply の答え手)。
  (Reply [request status body]
    (<- (Reply request status (reply-json body)))
    (resume None)))
