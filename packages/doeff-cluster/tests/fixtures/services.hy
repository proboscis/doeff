;;; service の宣言を値と関数で書いた見本(test_service_declaration.hy が読む)。
;;; 宣言は 2 つの定義からなる: Program を作る module の最上位の関数(defk)と、それを名指す service の値。
;;; 関数の参照(module:attr)は service が関数から導く。
(require doeff-hy.macros [defk])
(import doeff_cluster.service_model [service System])


(defk tally-program [step base]
  {:pre [(: step int) (: base int)] :post [(: % int)]}
  (+ base step))


(setv tally (service "tally" tally-program
                     :env "tests.fixtures.envs:plain_env"
                     :requires {"kind" "k3s"}
                     :config {"step" 2 "base" 1}))

(setv tally-system (System "tally-system" #(tally)))
