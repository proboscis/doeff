;;; scenarios.hy が宣言する本体と env(module の最上位 — 実行先は module:attr で引く)。
(require doeff-hy.macros [defk <-])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])

(defk tally-program [step base]
  {:pre [(: step int) (: base int)] :post [(: % int)]}
  (+ base step))

(defk greeter-program [step-size]
  {:pre [(: step-size int)] :post [(: % str)]}
  ;; 挨拶は env(実行先)か差し替えた handler(テスト用の main)が答える。本体は設定 greeting を知らない。
  (<- greeting str (Ask "greeting"))
  (+ greeting (str step-size)))

(defk keeper-program [cycles]
  {:pre [(: cycles int)] :post [(: % int)]}
  ;; 周回の数を答える(模擬で周期を有限にする上書きの見本)。
  cycles)

(defn greeting-env [config ctx]  ; defk にできない: job_entry が (env config ctx) で呼び、handler の list を直に受ける
  "設定 greeting(env だけが読む設定)から reader を組む。無ければ \"env\"。"
  [(reader {"greeting" (.get config "greeting" "env")})])

(defk flagged-program [record step]
  {:pre [(: record bool) (: step int)] :post [(: % int)]}
  ;; 本体の引数に、実行先が本体へ渡さない組み立て側の欄の名(record)を使う(S-DIST の反例)。
  (if record (+ step 100) step))
