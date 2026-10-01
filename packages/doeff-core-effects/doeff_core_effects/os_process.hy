;;; 汎用の子 process の effect(process_effects.hy)の本物の答え手 subprocess-handler(agora-redesign #802 便 1)。subprocess と os.environ を
;;; 呼んで値を詰め替えるだけで、判断を持たない。doeff-agents の driver-io-handler も RunProcess に同じ run-subprocess で答える(実装は 1 つ)。
;;; 子の標準入力・標準出力・標準エラーと output-path は utf-8 と surrogateescape で読み書きする(可逆 — process_effects.hy の頭の註・
;;; agora-redesign #2160)。
(require doeff-hy.macros [defhandler defk <- val])
(import fnmatch)
(import os)
(import subprocess)
(import doeff_core_effects.file_effects [FileFailed PathKind PathStat])
(import doeff_core_effects.os_file [stat-path])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory
                                            timed-out-outcome not-started-outcome executable-file-answer])


(defk os-executable-at [path]
  {:pre [(: path str)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "path に実行できる file が在るかを本物の file 系で読むため: 種類は symlink を辿った先(os.stat)、実行の許しは os.access の X_OK。
   読めない path(途中が file・入れない dir)は無い物と同じに扱う。判断は executable-file-answer(I/O なしの答え手と同じ関数)。
   doeff-agents の driver-io-handler も ExecutableAt にこれで答える。"
  (<- seen (| PathStat FileFailed) (stat-path path True))
  (val kind (match seen
              (PathStat :kind found) found
              (FileFailed) PathKind.MISSING))
  (<- answer bool (executable-file-answer kind (os.access path os.X-OK)))
  answer)


(defk decoded [value]
  {:pre [(: value (| str bytes None))] :post [(: % str)]}
  "TimeoutExpired が持つ部分出力は bytes のことがある — 答えの全部と同じ可逆の text(utf-8 と surrogateescape)にそろえるため。"
  (cond
    (is value None) ""
    (isinstance value bytes) (.decode value "utf-8" "surrogateescape")
    True (str value)))


(defk append-output [output-path stdout stderr]
  {:pre [(: output-path (| str None)) (: stdout str) (: stderr str)] :post [(: % None)]}
  "子の出力を output-path の末尾へ足すため(None なら何もしない)。surrogateescape で書くので、file には子が出した bytes がそのまま入る。"
  (when (is-not output-path None)
    (with [handle (open output-path "a" :encoding "utf-8" :errors "surrogateescape")]
      (.write handle stdout)
      (.write handle stderr)))
  None)


(defk child-environment [env env-mode env-drop]
  {:pre [(: env (| tuple None)) (: env-mode EnvMode) (: env-drop tuple)] :post [(: % (| dict None))]}
  "子の環境変数の全部を subprocess へ渡す形にするため(None = 呼び手の環境を継ぐ・REPLACE = tuple が全部・EXTEND = os.environ から env-drop の
   型に合う名を外して tuple を足す — 同じ名は tuple が勝つ)。"
  (val given (if (is env None) None (dfor e env e.name e.value)))
  (match #(given env-mode)
    #(None _) None
    #(_ EnvMode.EXTEND) (| (dfor #(k v) (.items os.environ) :if (not (any (gfor p env-drop (fnmatch.fnmatchcase k p)))) k v) given)
    _ given))


(defk run-subprocess [argv stdin timeout cwd env env-mode output-path env-drop]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None)) (: env-drop tuple)]
   :post [(: % ProcessOutcome)]}
  "子 process を 1 回走らせて ProcessOutcome にするため(env と env-mode の読み方は child-environment)。
   exit-code は returncode を丸めない。時間切れと起こせない形は値で返す(process_effects.hy の頭の註)。"
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (var outcome None)
  (try
    (val done (subprocess.run (list argv)
                               :input stdin
                               :capture-output True
                               :text True
                               :encoding "utf-8"
                               :errors "surrogateescape"
                               :timeout timeout
                               :cwd cwd
                               :env child-env
                               :check False))
    (:= outcome (ProcessOutcome :exit-code done.returncode :stdout (or done.stdout "") :stderr (or done.stderr "")))
    (except [error subprocess.TimeoutExpired]
      (<- partial-out str (decoded error.stdout))
      (<- partial-err str (decoded error.stderr))
      (<- timed-out ProcessOutcome (timed-out-outcome partial-out partial-err))
      (:= outcome timed-out))
    (except [error OSError]
      (<- refused ProcessOutcome (not-started-outcome (str error)))
      (:= outcome refused)))
  (<- (append-output output-path outcome.stdout outcome.stderr))
  outcome)


(defhandler subprocess-handler
  ;; 本物の子 process と自分の process の環境(頭の註)。
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop]
    (<- outcome (run-subprocess argv stdin timeout cwd env env-mode output-path env-drop))
    (resume outcome))
  (ExecutableAt [path]
    (<- found (os-executable-at path))
    (resume found))
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name os.environ) (EnvEntry :name name :value (get os.environ name))))))
  (WorkingDirectory []
    (resume (os.getcwd))))
