;;; 境目の記録係と再生の道具の検(test_boundary_recorder.hy)の業務の Program の見本(ADR-DOE-CLUSTER-001 R5・R5b)。
;;;
;;; Program は自分の with-handlers で、外側から 土台(外の世界の fake)→ 境目の記録係(record_handlers.boundary-recorder)→ 翻訳の
;;; handler → 業務の本体 の順に並べる。記録係は翻訳の handler が出した汎用の effect(Ask・ReadShared・WriteShared)だけを見る。
;;; scheduler は土台(本体を包む module の最上位の関数 — 計画 10.1)が並べる(実行先 — job_entry・replay_main — は何も足さない)。
;;;
;;; 土台の関数と翻訳の組の関数は module の最上位の関数なので、Program には参照で詰まる(handler の値は本体の中で関数を呼んで作る —
;;; R3b)。子の process(job_entry・replay_main)は Program を解く時にこの module を import する。
(require doeff-hy.macros [defk defhandler defeffect <- val var])
(import collections.abc [Callable])
(import os)
(import doeff [with-handlers DoExpr EffectBase Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.foundation.host_contract [host-reader environ-reader])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared])
(import doeff_cluster.shared_handlers [shared-memory])
(import doeff_cluster.shared.protocol.record_handlers [boundary-recorder])


;; --- 業務の effect と翻訳 ------------------------------------------------------------------------------

(defeffect CountVisit
  "業務の effect: 名 name の訪問を 1 つ数え、数えた後の数を返す。"
  {:fields [(: name str)]
   :answer int
   :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect DrawTicket
  "業務の effect: 札を 1 枚引く。答え = 札の文字列。"
  {:fields [(: label str)]
   :answer str
   :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler ledger-translation
  {:tags {:context "doeff-cluster-test" :role "protocol"}}
  ;; 決定的な翻訳: 訪問の数は共有の盤(汎用の ReadShared / WriteShared — 記録係がこの 2 つを記録する)の行、札は label から作る。
  (CountVisit [name]
    (val key (+ "visits/" name))
    (<- rows dict (ReadShared key))
    (val counted (+ 1 (.get rows key 0)))
    (<- (WriteShared key counted))
    (resume counted))
  (DrawTicket [label]
    (resume (+ "ticket-" label))))


(defhandler drifting-tickets
  {:tags {:context "doeff-cluster-test" :role "protocol"}}
  ;; R5b の破れの見本: 記録係より内側で乱数を直に読んで札を作る(汎用の effect にしていないので記録に載らず、再生では違う札になる)。
  (DrawTicket [label]
    (resume (+ "ticket-" (.hex (os.urandom 6))))))


(defk ledger-translation-layer []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "protocol"}}
  "決定的な翻訳の handler の組。"
  [ledger-translation])


(defk drifting-translation-layer []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "protocol"}}
  "翻訳の組の一番内側に、札を乱数で作る handler を足した組(記録係より内側の非決定 — R5b の反例)。"
  [ledger-translation drifting-tickets])


;; --- 土台 ---------------------------------------------------------------------------------------------

(defk world-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の土台(外の世界の fake): scheduler と、宿の契約の Ask(host-reader — session の値を使うので外側に state)・environ を読む Ask
   (本番の土台と同じ (environ-reader) — 宣言の :environ の EFFECT_RECORD_MODE・EFFECT_RECORD_OTLP・業務の設定に字面どおり答え、環境に
   無い鍵は外へ通す)・共有の盤(memory — process ごとに空から始まる)の下で本体を走らせる。"
  (<- answer (scheduled (with-handlers [(state) host-reader (environ-reader) (shared-memory {})] body)))
  answer)


;; --- 業務の Program --------------------------------------------------------------------------------------

(defk ledger-body [names]
  {:pre [(: names tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "業務の本体: 周回の数を設定(Ask LEDGER_ROUNDS)で読み、周回ごとに名ごとの訪問を数え、最後に札を 1 枚引いてその札の訪問も数える。
   答え = 名(と札)→ 最後に数えた数。"
  (<- rounds str (Ask "LEDGER_ROUNDS"))
  (var totals {})
  (for [_ (range (int rounds))]
    (for [name names]
      (<- counted int (CountVisit name))
      (:= totals (| totals {name counted}))))
  (<- ticket str (DrawTicket "last"))
  (<- ticket-count int (CountVisit ticket))
  (| totals {"ticket" ticket-count}))


(defk ledger-inside [translation names]
  {:pre [(: translation Callable) (: names tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の下で境目の記録係の組を選び、翻訳の handler の外側(土台との間)に置いて業務の本体を走らせる。"
  (<- recorder list (boundary-recorder))
  (<- inner list (translation))
  (<- totals dict (with-handlers [#* recorder #* inner] (ledger-body names)))
  totals)


(defk ledger-program [foundation translation names]
  {:pre [(: foundation Callable) (: translation Callable) (: names tuple)] :post [(: % dict)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "job の Program: 土台(scheduler と外の世界の fake)で ledger-inside を包んで走らせる(実行先は何も足さない)。"
  (<- totals dict (foundation (ledger-inside translation names)))
  totals)
