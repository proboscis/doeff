;;; declare の CLI の検(test_service_declaration.hy)が一時の git repo へ写して宣言する系の見本。写した先では module の最上位の
;;; declared_system として読まれるので、tests の他の module を import しない。
;;;
;;; net-foundation の :needs は job の :needs と同じ。wide-foundation は job の :needs に無い能力(gpu)も名乗る土台 — declare は
;;; 「土台の :needs ⊆ job の :needs」の外れとして断る(計画 9 節の P)。
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(import doeff [with-handlers EffectBase Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [scheduled])


(defk net-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "見本の土台: scheduler と、基準の値と挨拶の語を Ask で答える reader の下で本体を走らせる。"
  (<- answer (scheduled (with-handlers [(reader {"base" 100 "greeting" "hi"})] body)))
  answer)


(defk wide-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net" "gpu"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "job の :needs に無い能力(gpu)も名乗る見本の土台(土台の :needs の外れの反例)。"
  (<- answer (scheduled (with-handlers [(reader {"base" 100 "greeting" "hi"})] body)))
  answer)


(defk tally-program [foundation step]
  {:pre [(: foundation Callable) (: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の reader が答える基準の値に step を足す。"
  (<- base int (foundation (Ask "base")))
  (+ base step))


(defk greeter-program [foundation step]
  {:pre [(: foundation Callable) (: step int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の reader が答える挨拶に step を付ける。"
  (<- greeting str (foundation (Ask "greeting")))
  (+ greeting (str step)))


(defsystem pair [foundation]
  "見本の系: tally と greeter"
  (tally (tally-program foundation 2) :replicas 1 :needs #{"cluster-net"})
  (greeter (greeter-program foundation 3) :replicas 1 :needs #{"cluster-net"}))
