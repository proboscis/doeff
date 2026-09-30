;;; 本番の土台で閉じているかの検(test_foundation_closure.hy)の見本。
;;;
;;; 本体 = 業務の effect(Ping)・時計(Delay)・設定(Ask)・子の task(Spawn)を出す Program。job の本体が翻訳の handler を並べて Ping を
;;; 外の世界の effect(Raw)に訳し、土台(本体を受けて包む defk)が Raw・時計・設定に答え、scheduler が Spawn に答える。
(require doeff-hy.macros [defk defhandler <- val])
(import collections.abc [Callable])
(import doeff [EffectBase DoExpr with-handlers])
(import doeff.program [handler :as program-handler])
(import doeff_core_effects.effects [Ask Try])
(import doeff_core_effects.handlers [state try-handler])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
(import doeff_time [Delay sync-time-handler])


(defclass Ping [EffectBase]
  "業務の effect(翻訳の handler が外の世界の effect に訳す)。")

(defclass Raw [EffectBase]
  "外の世界の effect(本番の土台が答える)。")


(defhandler translate
  {:tags {:context "doeff-cluster-test" :role "protocol"}}
  (Ping [] (<- r (Raw)) (resume r)))

(defhandler raw-world
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (Raw [] (resume 1)))

(defhandler fake-settings
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (Ask [key] (resume "1")))

;; 本当に読めない handler — 節も __doeff_handles__ の宣言も持たない(source の無い組み込みの関数を包んだだけ)。
(val opaque-world (program-handler print))


(defk child []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子の task(Spawn で運ばれる — 親の handler を持ち運ぶ)。"
  (<- a (Ping))
  a)


(defk business []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "業務の本体: 業務の effect・時計・設定・子の task を出す。"
  (<- a (Ping))
  (<- (Delay 1.0))
  (<- b (Ask "ROUNDS"))
  (<- t (Spawn (child)))
  (<- c (Wait t))
  (+ a c))


(defk tried []
  {:pre [] :post [(: % "Ok | Err")] :tags {:context "doeff-cluster-test" :role "program"}}
  "業務の本体を Try に運ばせる(Try の答え手は出した所の handler の下で本体を走らせる — 本体の effect は出した所で数える)。"
  (<- r (Try (business)))
  r)


(defk tried-job [foundation]
  {:pre [(: foundation Callable)] :post [(: % "Ok | Err")] :tags {:context "doeff-cluster-test" :role "entry"}}
  "翻訳の handler の下で、Try に運ばせた本体を土台で包む job。"
  (<- r (foundation (with-handlers [translate] (tried))))
  r)


(defk untranslated-tried-job [foundation]
  {:pre [(: foundation Callable)] :post [(: % "Ok | Err")] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Try に運ばせた本体の翻訳の handler を並べ忘れた job(本体の業務の effect は Try の中にしか無い)。"
  (<- r (foundation (tried)))
  r)


(defk translated-body []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "翻訳の handler を並べた本体。"
  (<- r (with-handlers [translate] (business)))
  r)


(defk job [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台で翻訳つきの本体を包む job。"
  (<- r (foundation (translated-body)))
  r)


(defk untranslated-job [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "翻訳の handler を並べ忘れた job。"
  (<- r (foundation (business)))
  r)


(defk production-foundation [inner]
  {:pre [(: inner DoExpr)] :post [(: % "inner の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "閉じている本番の土台(scheduler・時計・設定・外の世界・Try)。"
  (<- r (scheduled (with-handlers [(state) (sync-time-handler) fake-settings raw-world try-handler] inner)))
  r)


(defk clockless-foundation [inner]
  {:pre [(: inner DoExpr)] :post [(: % "inner の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "時計を入れ忘れた本番の土台。"
  (<- r (scheduled (with-handlers [(state) fake-settings raw-world] inner)))
  r)


(defk unscheduled-foundation [inner]
  {:pre [(: inner DoExpr)] :post [(: % "inner の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "scheduler を入れ忘れた本番の土台(sim の土台と同じ形 — sim では見つからない入れ忘れ)。"
  (<- r (with-handlers [(state) (sync-time-handler) fake-settings raw-world] inner))
  r)


(defk opaque-foundation [inner]
  {:pre [(: inner DoExpr)] :post [(: % "inner の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "外の世界の handler が読めない本番の土台。"
  (<- r (scheduled (with-handlers [(state) (sync-time-handler) fake-settings opaque-world] inner)))
  r)
