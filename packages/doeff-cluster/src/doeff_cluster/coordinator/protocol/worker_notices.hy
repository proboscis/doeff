;;; worker の生死の出来事(coordinator/intent/worker_notices の WorkerGone・WorkerBack)を、doeff-events の知らせの道へ写す表の 1 か所
;;; (#3864)。出す側(coordinator の組み立て — coordinator/entry/handler_sets)は WORKER-NOTICE-ROUTES を、受ける側は channel を読む
;;; WORKER-NOTICE-READS を使う。
;;;
;;; 届かなかった時: 2 つの型とも MarkGap — channel に欠けの印を付け、broker が戻った時か次に出す時に欠け(受け手の SourceMissed)を
;;; 知らせる。受け手はその時に coordinator の状態から生死を読み直す(doeff-events の notice_events_handler・ADR-DOE-EVENTS-002 R5)。
;;; 起動の時の欠けの知らせ(start_channels)は持たない — coordinator は起動の後の最初の歩で名簿の全部の今の生死を出す。
;;; 綴り: 本文は defwire の型の JSON(欄の名は camel)。encode / decode は doeff-events が Program の外で呼ぶ普通の関数なので、型の解き手
;;; (__doeff_wire__ の codec — doeff_hy.wire の parse-json / dump-json と同じ物)を直に使う。形の違う本文は MalformedNotice で、
;;; 受け手の源を名指しで落とす(doeff-events は読めない知らせを捨てない)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import doeff_events [MarkGap NoticeRoute])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_hy.wire_malformed [Malformed])


(defclass MalformedNotice [ValueError]
  "知らせの本文が出来事の型の形でない(受け手の源を名指しで落とす訳 — 形の違い Malformed を持つ)。")


(deff notice-of [wire-type body]  ; defk にできない: doeff-events の道の decode(Program の外で呼ぶ普通の関数)から呼ぶ
  {:pre [(: wire-type type) (: body str)] :post [(: % (| WorkerGone WorkerBack))] :tags {:context "coordinator" :role "protocol"}}
  "知らせの本文を出来事の型へ解いて確かめるため(形が違えば MalformedNotice)。"
  (match (.decode-json (. wire-type __doeff_wire__ codec) body)
    (Malformed) :as refused (raise (MalformedNotice (repr refused)))
    notice notice))

;; worker の生死の出来事を運ぶ channel(1 本)。
(val WORKER-NOTICE-CHANNEL "doeff-cluster:workers")


;; 道の表の行の型は doeff-events の受ける NoticeRoute[object](型ごとの行を 1 つの組に並べる形)なので、channel と encode は値の型 object の
;; callback で受ける(契約 :pre が 2 つの型だけを通し、本体はその型へ絞ってから使う)。
(deff notice-text [#^ object event]  ; defk にできない: doeff-events の道の encode(Program の外で呼ぶ普通の関数)
  {:pre [(: event (| WorkerGone WorkerBack))] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "出来事を知らせの本文(defwire の JSON)へ綴るため。"
  (if (isinstance event #(WorkerGone WorkerBack))
      (.encode-json (. (type event) __doeff_wire__ codec) event)
      (raise (TypeError (repr event)))))


(deff gone-of [body]  ; defk にできない: doeff-events の道の decode(Program の外で呼ぶ普通の関数)
  {:pre [(: body str)] :post [(: % WorkerGone)] :tags {:context "coordinator" :role "protocol"}}
  "知らせの本文を WorkerGone へ解いて確かめるため(形が違えば MalformedNotice)。"
  (notice-of WorkerGone body))


(deff back-of [body]  ; defk にできない: doeff-events の道の decode(Program の外で呼ぶ普通の関数)
  {:pre [(: body str)] :post [(: % WorkerBack)] :tags {:context "coordinator" :role "protocol"}}
  "知らせの本文を WorkerBack へ解いて確かめるため(形が違えば MalformedNotice)。"
  (notice-of WorkerBack body))



(deff notice-channel [#^ object event]  ; defk にできない: doeff-events の道の channel(Program の外で呼ぶ普通の関数)
  {:pre [(: event (| WorkerGone WorkerBack))] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "出来事を出す channel を決めるため(どの worker の出来事も 1 本の channel)。"
  WORKER-NOTICE-CHANNEL)


;; 出す側(coordinator)の道の表 — channel を読まない。
(val #^ (get tuple #((get NoticeRoute object) ...)) WORKER-NOTICE-ROUTES
  #((NoticeRoute :event-type WorkerGone :wire-name "worker-gone" :channel notice-channel
                 :encode notice-text :decode gone-of :when-unsent (MarkGap))
    (NoticeRoute :event-type WorkerBack :wire-name "worker-back" :channel notice-channel
                 :encode notice-text :decode back-of :when-unsent (MarkGap))))

;; 受ける側の道の表 — 同じ 2 行に、読む channel を付けた物。
(val #^ (get tuple #((get NoticeRoute object) ...)) WORKER-NOTICE-READS
  #((NoticeRoute :event-type WorkerGone :wire-name "worker-gone" :channel notice-channel
                 :encode notice-text :decode gone-of :when-unsent (MarkGap) :reads #(WORKER-NOTICE-CHANNEL))
    (NoticeRoute :event-type WorkerBack :wire-name "worker-back" :channel notice-channel
                 :encode notice-text :decode back-of :when-unsent (MarkGap) :reads #(WORKER-NOTICE-CHANNEL))))
