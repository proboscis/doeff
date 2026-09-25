;;; worker の smoke 用の job。拍を file へ書き続け、SIGTERM で後始末して終わる。
;;;
;;; 引数: <拍の file> [--ignore-term] [--grandchild]
(import os)
(import signal)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])

(defclass Flag []
  (defn __init__ [self] (setv self.stopping False)))

(defn main []
  (setv beat (Path (get sys.argv 1)) flag (Flag))
  (defn on-term [signum frame] (setv flag.stopping True))
  (signal.signal signal.SIGTERM (if (in "--ignore-term" sys.argv) signal.SIG-IGN on-term))
  (when (in "--grandchild" sys.argv)
    ;; 同じ process group に残る孫。worker は group ごと回収する必要がある。
    (setv child (subprocess.Popen [sys.executable "-c" "import time; time.sleep(3600)"]))
    (.write-text (Path (+ (str beat) ".grandchild")) (str child.pid)))
  (while (not flag.stopping)
    (.write-text beat (.format "{} {}" (time.time) (os.environ.get "DOEFF_WORKER_REVISION")))
    (time.sleep 0.1))
  (.write-text (Path (+ (str beat) ".stopped")) "clean"))

(when (= __name__ "__main__")
  (main))
