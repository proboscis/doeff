;;; worker の生死の出来事(coordinator/intent/worker_notices の WorkerGone・WorkerBack)を、doeff-events の知らせの道へ写す表の 1 か所
;;; (#3864)。出す側(coordinator の組み立て — coordinator/entry/handler_sets)は WORKER-NOTICE-ROUTES を、受ける側は channel を読む
;;; WORKER-NOTICE-READS を使う。
;;;
;;; 届かなかった時: 2 つの型とも MarkGap — channel に欠けの印を付け、broker が戻った時か次に出す時に欠け(受け手の SourceMissed)を
;;; 知らせる。受け手はその時に coordinator の状態から生死を読み直す(doeff-events の notice_events_handler・ADR-DOE-EVENTS-002 R5)。
;;; 起動の時の欠けの知らせ(start_channels)は持たない — coordinator は起動の後の最初の歩で名簿の全部の今の生死を出す。
;;; 綴り: 本文は defwire の型の JSON(欄の名は camel)。encode / decode は doeff-events が Program の外で呼ぶ普通の関数なので、型の解き手
;;; (__doeff_wire__ の adapter — doeff_hy.wire の parse-json / dump-json と同じ物)を直に使う。形の違う本文は ValidationError で、
;;; 受け手の源を名指しで落とす(doeff-events は読めない知らせを捨てない)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff_events [MarkGap NoticeRoute])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])

;; worker の生死の出来事を運ぶ channel(1 本)。
(val WORKER-NOTICE-CHANNEL "doeff-cluster:workers")


(deff notice-text [event]  ; defk にできない: doeff-events の道の encode(Program の外で呼ぶ普通の関数)
  {:pre [(: event (| WorkerGone WorkerBack))] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "出来事を知らせの本文(defwire の JSON)へ綴るため。"
  (.decode (.dump-json (. (type event) __doeff_wire__ adapter) event :by-alias True :exclude-defaults True) "utf-8"))


(deff gone-of [body]  ; defk にできない: doeff-events の道の decode(Program の外で呼ぶ普通の関数)
  {:pre [(: body str)] :post [(: % WorkerGone)] :tags {:context "coordinator" :role "protocol"}}
  "知らせの本文を WorkerGone へ解いて確かめるため(形が違えば ValidationError)。"
  (.validate-json (. WorkerGone __doeff_wire__ adapter) body))


(deff back-of [body]  ; defk にできない: doeff-events の道の decode(Program の外で呼ぶ普通の関数)
  {:pre [(: body str)] :post [(: % WorkerBack)] :tags {:context "coordinator" :role "protocol"}}
  "知らせの本文を WorkerBack へ解いて確かめるため(形が違えば ValidationError)。"
  (.validate-json (. WorkerBack __doeff_wire__ adapter) body))



(deff notice-channel [event]  ; defk にできない: doeff-events の道の channel(Program の外で呼ぶ普通の関数)
  {:pre [(: event (| WorkerGone WorkerBack))] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "出来事を出す channel を決めるため(どの worker の出来事も 1 本の channel)。"
  WORKER-NOTICE-CHANNEL)


;; 出す側(coordinator)の道の表 — channel を読まない。
(val WORKER-NOTICE-ROUTES
  #((NoticeRoute :event-type WorkerGone :wire-name "worker-gone" :channel notice-channel
                 :encode notice-text :decode gone-of :when-unsent (MarkGap))
    (NoticeRoute :event-type WorkerBack :wire-name "worker-back" :channel notice-channel
                 :encode notice-text :decode back-of :when-unsent (MarkGap))))

;; 受ける側の道の表 — 同じ 2 行に、読む channel を付けた物。
(val WORKER-NOTICE-READS
  #((NoticeRoute :event-type WorkerGone :wire-name "worker-gone" :channel notice-channel
                 :encode notice-text :decode gone-of :when-unsent (MarkGap) :reads #(WORKER-NOTICE-CHANNEL))
    (NoticeRoute :event-type WorkerBack :wire-name "worker-back" :channel notice-channel
                 :encode notice-text :decode back-of :when-unsent (MarkGap) :reads #(WORKER-NOTICE-CHANNEL))))
