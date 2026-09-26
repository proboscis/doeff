;;; 盲検 B の反例の再現: service の本体の I/O 検査(agora-controllers の ADR の service-body-report)が、本体を名指す宣言の置き場と
;;; 書き方によって本体を読むか読まないかが変わるか。
;;; 使い方: cd <agora-controllers の root>; hy <この file> <作業 dir(/tmp の下)>
;;; 作業 dir に同じ本体(直の file I/O を持つ)を 4 つの宣言の形で置いた木を作り、検査を当てる。最後に本物の repo で、宣言の値が名指す
;;; 本体の数と、検査が読めた本体の数を比べる。
(require doeff-hy.macros [<-])
(import sys shutil importlib)
(import pathlib [Path])
(import doeff [run])
(import doeff_cluster.service_model [ServiceDef System])
(import controllers.worker.adr.defadr_worker_business_code_touches_io_only_through_effects :as adr)

(setv work (Path (get sys.argv 1)))

(setv BODY (+ "(require doeff-hy.macros [defk])\n"
              "(defk keeper-program [cursor-file]\n"
              "  {:pre [(: cursor-file str)] :post [(: % int)]}\n"
              "  (.write-text (Path cursor-file) \"[]\" :encoding \"utf-8\")\n"
              "  0)\n"))
(setv SHAPES
  {"same-module"      {"keeper.hy" (+ BODY "(setv keeper (service \"keeper\" keeper-program :env \"m:e\" :config {\"cursor-file\" \"/tmp/c\"}))\n")}
   "other-module"     {"keeper.hy" BODY
                       "cluster.hy" (+ "(import x.keeper [keeper-program])\n"
                                       "(setv keeper (service \"keeper\" keeper-program :env \"m:e\" :config {\"cursor-file\" \"/tmp/c\"}))\n")}
   "wrapper"          {"keeper.hy" (+ BODY "(defn sim-service [name program] (service name program :env \"m:e\" :config {\"cursor-file\" \"/tmp/c\"}))\n"
                                          "(setv keeper (sim-service \"keeper\" keeper-program))\n")}
   "name-from-const"  {"keeper.hy" (+ BODY "(setv NAME \"keeper\")\n(setv keeper (service NAME keeper-program :env \"m:e\" :config {\"cursor-file\" \"/tmp/c\"}))\n")}})

(for [#(shape files) (.items SHAPES)]
  (setv root (/ work shape))
  (when (.exists root) (shutil.rmtree root))
  (for [#(name text) (.items files)]
    (setv p (/ root "controllers" "x" name))
    (.mkdir p.parent :parents True :exist-ok True)
    (.write-text p text :encoding "utf-8"))
  (setv report (run (adr.service-body-report root)))
  (print (.format "{:16} 読めた本体 {}・違反 {}" shape report.bodies (lfor v report.violations (get v 2)))))

;; 本物の repo: 宣言の値が名指す本体(module の最上位の ServiceDef と System の中)と、検査が読めた本体の数。
(setv here (Path.cwd))
(setv report (run (adr.service-body-report here)))
(setv factories #{})
(for [p (sorted (.rglob (/ here "controllers") "*.hy"))]
  (setv text (.read-text p :encoding "utf-8"))
  (when (not-in "service_model" text) (continue))
  (setv mod (importlib.import-module (.join "." (. (.with-suffix (.relative-to p here) "") parts))))
  (for [v (.values (vars mod))]
    (for [s (if (isinstance v System) v.services #(v))]
      (when (isinstance s ServiceDef) (.add factories s.factory)))))
(print (.format "本物の repo: 検査が読めた本体 {}・service_model を読む module の宣言の値が名指す本体 {}" report.bodies (len factories)))
