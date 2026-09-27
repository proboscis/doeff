;;; 汎用の子 process の effect(process_effects.hy)の本物の答え手 subprocess-handler(agora-redesign #802 便 1)。subprocess と os.environ を
;;; 呼んで値を詰め替えるだけで、判断を持たない。doeff-agents の driver-io-handler も RunProcess に同じ run-subprocess で答える(実装は 1 つ)。
(require doeff-hy.macros [defhandler defk <- val])
(import os)
(import subprocess)
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory])

;; 時間切れの時の exit-code(coreutils の timeout と同じ)と、起こせない時の exit-code(shell と同じ)。
(val TIMED-OUT-CODE 124)
(val NOT-STARTED-CODE 127)


(defk decoded [value]
  {:pre [(: value (| str bytes None))] :post [(: % str)]}
  "TimeoutExpired が持つ部分出力は bytes のことがある — text にそろえるため。"
  (cond
    (is value None) ""
    (isinstance value bytes) (.decode value "utf-8" "replace")
    True (str value)))


(defk append-output [output-path stdout stderr]
  {:pre [(: output-path (| str None)) (: stdout str) (: stderr str)] :post [(: % None)]}
  "子の出力を output-path の末尾へ足すため(None なら何もしない)。"
  (when (is-not output-path None)
    (with [handle (open output-path "a" :encoding "utf-8")]
      (.write handle stdout)
      (.write handle stderr)))
  None)


(defk child-environment [env env-mode]
  {:pre [(: env (| tuple None)) (: env-mode EnvMode)] :post [(: % (| dict None))]}
  "子の環境変数の全部を subprocess へ渡す形にするため(None = 呼び手の環境を継ぐ・REPLACE = tuple が全部・EXTEND = os.environ に tuple を
   足す — 同じ名は tuple が勝つ)。"
  (val given (if (is env None) None (dfor e env e.name e.value)))
  (match #(given env-mode)
    #(None _) None
    #(_ EnvMode.EXTEND) (| (dict os.environ) given)
    _ given))


(defk run-subprocess [argv stdin timeout cwd env env-mode output-path]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None))]
   :post [(: % ProcessOutcome)]}
  "子 process を 1 回走らせて ProcessOutcome にするため(env と env-mode の読み方は child-environment)。
   exit-code は returncode を丸めない。時間切れと起こせない形は値で返す(process_effects.hy の頭の註)。"
  (<- child-env (| dict None) (child-environment env env-mode))
  (var outcome None)
  (try
    (val done (subprocess.run (list argv)
                               :input stdin
                               :capture-output True
                               :text True
                               :encoding "utf-8"
                               :timeout timeout
                               :cwd cwd
                               :env child-env
                               :check False))
    (:= outcome (ProcessOutcome :exit-code done.returncode :stdout (or done.stdout "") :stderr (or done.stderr "")))
    (except [error subprocess.TimeoutExpired]
      (<- partial-out str (decoded error.stdout))
      (<- partial-err str (decoded error.stderr))
      (:= outcome (ProcessOutcome :exit-code TIMED-OUT-CODE :stdout partial-out :stderr partial-err :timed-out True)))
    (except [error OSError]
      (:= outcome (ProcessOutcome :exit-code NOT-STARTED-CODE :stdout "" :stderr "" :started False :start-error (str error)))))
  (<- (append-output output-path outcome.stdout outcome.stderr))
  outcome)


(defhandler subprocess-handler
  ;; 本物の子 process と自分の process の環境(頭の註)。
  (RunProcess [argv stdin timeout cwd env env-mode output-path]
    (<- outcome (run-subprocess argv stdin timeout cwd env env-mode output-path))
    (resume outcome))
  (ExecutableAt [path]
    (resume (and (os.path.exists path) (os.access path os.X-OK))))
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name os.environ) (EnvEntry :name name :value (get os.environ name))))))
  (WorkingDirectory []
    (resume (os.getcwd))))
