;;; 変更シナリオごとの正常例と反例を、doeff-cluster の service の宣言の公開の口(service・System・system-main・system-declaration・
;;; job_entry service)で確かめる。修正前と修正後の doeff-cluster の上で同じ file を走らせ、1 行ずつ「OK <答え>」か「失敗 <型>: <文>」を印字する。
;;; 使い方: cd <この dir>; hy scenarios.hy
;;; 本体と env は scenarios_fixture.hy(module の最上位の defk)。宣言は各行の中で組む(宣言の時点の拒否も 1 行に出す)。
(import json subprocess sys)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.service_model [service System system-main system-declaration])
(import scenarios_fixture [tally-program greeter-program keeper-program flagged-program])

(defn outcome [thunk]
  (try
    (+ "OK " (repr (thunk)))
    (except [e Exception]
      (+ "失敗 " (. (type e) __name__) ": " (get (+ (.splitlines (str e)) [""]) 0)))))

(defn declared [thunk]
  "宣言を組み、coordinator へ渡る 1 行(run と update・requires)を返す。"
  (setv #(row) (system-declaration (System "s" #((thunk))) "rev1"))
  row)

(defn in-process [thunk overrides]
  "テスト用の main(handler は env を使わず、ここで reader を差し替える)。"
  (run (scheduled (with_handlers [(reader {"greeting" "sim"})] (system-main (System "s" #((thunk))) overrides)))))

(defn at-worker [thunk]
  "実行先の入口(coordinator が worker に起こさせる命令と同じ形)で走らせ、stderr の最後の行を返す。"
  (setv run-spec (get (declared thunk) "run"))
  (setv done (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.job_entry" "service"
                              "--factory" (get run-spec "factory") "--env" (get run-spec "env")
                              "--config" (json.dumps (get run-spec "config"))]
                             :capture-output True :text True :timeout 120))
  (setv lines (lfor line (.splitlines done.stderr) :if (.strip line) line))
  (.format "exit={} {}" done.returncode (if lines (get lines -1) "")))

(setv ENV "scenarios_fixture:greeting_env")
(setv CASES
  [;; S-DIST: 1 process で確かめた宣言を、そのまま実行先へ出す(組み立て側の欄 record を宣言に書いた service を含む)。
   #("S-DIST 正常例: 同じ宣言をテスト用の main と実行先で走らせる(record を宣言に書く)"
     (fn [] (service "tally" tally-program :env ENV :config {"step" 2 "base" 1 "record" {"otlp" "http://127.0.0.1:9" "flushSeconds" 0.1}}))
     #("main" "worker"))
   #("S-DIST 反例: 本体が record という名の引数を取る(実行先は record を本体へ渡さない)"
     (fn [] (service "flagged" flagged-program :env ENV :config {"record" True "step" 1}))
     #("main" "worker"))
   ;; S-CONC: 書き手の入れ替えを handoff にする。
   #("S-CONC 正常例: :update handoff が coordinator への宣言に写る"
     (fn [] (service "tally" tally-program :env ENV :config {"step" 1 "base" 0} :update "handoff"))
     #("update"))
   #("S-CONC 反例: 語彙の外の入れ替えの形"
     (fn [] (service "tally" tally-program :env ENV :config {"step" 1 "base" 0} :update "rolling"))
     #("update"))
   ;; S-HW: 置き場の条件を足す。
   #("S-HW 正常例: :requires の条件が中身を解釈されずに写る"
     (fn [] (service "tally" tally-program :env ENV :config {"step" 1 "base" 0} :requires {"kind" "k3s" "gpu" "a100"}))
     #("requires"))
   #("S-HW 反例: 文字列でない鍵の条件"
     (fn [] (service "tally" tally-program :env ENV :config {"step" 1 "base" 0} :requires {1 "a100"}))
     #("requires"))
   ;; S-STORE / S-EFF: env だけが読む設定(置き場・外への口の設定)を足す。本体は変えない。
   #("S-STORE 正常例: env だけが読む設定を :env-config に書く(本体は step-size だけを取る)"
     (fn [] (service "greeter" greeter-program :env ENV :config {"step-size" 2} :env-config {"greeting" "hi"}))
     #("main" "worker"))
   #("S-STORE 反例(修正前の書き方): env だけが読む設定を :config に書く — 本体の引数に無い鍵"
     (fn [] (service "greeter" greeter-program :env ENV :config {"step-size" 2 "greeting" "hi"}))
     #("main" "worker"))
   #("S-STORE 反例: 本体の引数を :env-config に書く"
     (fn [] (service "greeter" greeter-program :env ENV :config {} :env-config {"step-size" 2 "greeting" "hi"}))
     #("main"))
   ;; S-SIM: 同じ宣言を handler の差し替えと上書きだけで回す。
   #("S-SIM 正常例: 上書きで周期を変えてテスト用の main で回す"
     (fn [] (service "keeper" keeper-program :env ENV :config {"cycles" 3}))
     #("main-override"))
   #("S-SIM 反例: 宣言に無い鍵の上書き(綴りの違い cycle)"
     (fn [] (service "keeper" keeper-program :env ENV :config {"cycles" 3}))
     #("main-typo"))
   #("S-SIM 反例: 本体の引数が設定に無い宣言"
     (fn [] (service "tally" tally-program :env ENV :config {"step" 1}))
     #("main"))])

(for [#(label thunk ways) CASES]
  (print "==" label)
  (setv made (outcome thunk))
  (print "  宣言:" (if (.startswith made "OK") "OK" made))
  (when (.startswith made "OK")
    (for [way ways]
      (print (.format "  {}:" way)
             (match way
               "main" (outcome (fn [] (in-process thunk {})))
               "main-override" (outcome (fn [] (in-process thunk {(. (thunk) name) {"cycles" 1}})))
               "main-typo" (outcome (fn [] (in-process thunk {(. (thunk) name) {"cycle" 1}})))
               "worker" (at-worker thunk)
               "update" (outcome (fn [] (.get (declared thunk) "update" "recreate")))
               "requires" (outcome (fn [] (get (declared thunk) "requires")))
               _ (raise (ValueError (+ "未知の道 " way))))))))
