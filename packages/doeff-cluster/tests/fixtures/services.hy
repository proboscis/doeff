;;; service の宣言を値と関数で書いた見本(test_service_declaration.hy が読む)。
;;; 宣言は 2 つの定義からなる: Program を作る module の最上位の関数(defk)と、それを名指す service の値。
;;; 関数の参照(module:attr)は service が関数から導く。
(require doeff-hy.macros [defk <-])
(import doeff_core_effects.effects [Ask])
(import doeff_cluster.service_model [service System])


(defk tally-program [step base]
  {:pre [(: step int) (: base int)] :post [(: % int)]}
  (+ base step))


(setv tally (service "tally" tally-program
                     :env "tests.fixtures.envs:plain_env"
                     :requires {"kind" "k3s"}
                     :config {"step" 2 "base" 1}))

(setv tally-system (System "tally-system" #(tally)))


;; 本体の引数に、実行先が本体へ渡さない組み立て側の欄の名(record)を使う関数(宣言の時点で断られる見本)。
(defk flagged-program [record step]
  {:pre [(: record bool) (: step int)] :post [(: % int)]}
  (if record (+ step 100) step))


;; 本体は設定 step-size だけを引数に取り、挨拶は env が設定 greeting から組む reader で答える(env だけが読む設定の見本)。
(defk greeter-program [step-size]
  {:pre [(: step-size int)] :post [(: % str)]}
  (<- greeting str (Ask "greeting"))
  (+ greeting (str step-size)))
