;;; worker の smoke 用の job。拍を file へ書き続け、SIGTERM で後始末して終わる。
;;;
;;; 引数: <拍の file> [--ignore-term] [--grandchild] [--detached-grandchild] [--once]
(require doeff-hy.macros [deff])
(import os)
(import signal)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import types [FrameType])

(defclass Flag []
  (defn #^ None __init__ [self] (setv self.stopping False)))

;; --detached-grandchild の孫の本体(#2940): 止めの合図(TERM)を捨てる構えをしてから、自分の pid を引数の file へ書いて眠る(一時の名で
;; 書いてから名を変える — 読み手が書きかけを読まない)。hy の process の sys.executable は hy なので、本体は Hy で書く。
(setv DETACHED-SLEEPER (.join " " ["(import os pathlib signal sys time)"
                                   "(signal.signal signal.SIGTERM signal.SIG-IGN)"
                                   "(setv path (pathlib.Path (get sys.argv 1)) staged (.with-name path (+ path.name \".part\")))"
                                   "(.write-text staged (str (os.getpid)))"
                                   "(.rename staged path)"
                                   "(time.sleep 3600)"]))

(deff main []  ; defk にできない: 子の process の入口(`__main__` が素の関数として呼ぶ)
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "main"}}
  "worker の smoke 用の job の本体: 拍を file へ書き続け、SIGTERM で後始末して終わるため。"
  (setv beat (Path (get sys.argv 1)) flag (Flag))
  (defn #^ None on-term [#^ int signum #^ (| FrameType None) frame] (setv flag.stopping True))
  (signal.signal signal.SIGTERM (if (in "--ignore-term" sys.argv) signal.SIG-IGN on-term))
  (when (in "--grandchild" sys.argv)
    ;; 同じ process group に残る孫。worker は group ごと回収する必要がある。
    (setv child (subprocess.Popen [sys.executable "-c" "import time; time.sleep(3600)"]))
    (.write-text (Path (+ (str beat) ".grandchild")) (str child.pid)))
  (when (in "--detached-grandchild" sys.argv)
    ;; 別の session に起こした孫(#2940): group への合図は届かず、job が終わっても PID 1 へ逃げる — shim が引き取って片づける。孫が
    ;; pid を <拍の file>.detached へ書くまで待ってから進む(書く前に job が終わって孫が片づけられると、検が孫を名指せない)。
    (setv detached (Path (+ (str beat) ".detached")) limit (+ (time.monotonic) 30))
    (setv detached-child (subprocess.Popen [sys.executable "-c" DETACHED-SLEEPER (str detached)] :start-new-session True))
    (while (not (.exists detached))
      (when (> (time.monotonic) limit)
        (raise (RuntimeError (.format "別の session の孫 {} が 30 秒で pid を書かない" detached-child.pid))))
      (time.sleep 0.01)))
  ;; --once: 止めの合図を待たずに自分で終わる(残した孫は shim が片づける・#2940)。
  (when (in "--once" sys.argv)
    (setv flag.stopping True))
  (while (not flag.stopping)
    (.write-text beat (.format "{} {}" (time.time) (os.environ.get "DOEFF_WORKER_REVISION")))
    (time.sleep 0.1))
  (.write-text (Path (+ (str beat) ".stopped")) "clean")
  None)

(when (= __name__ "__main__")
  (main))
