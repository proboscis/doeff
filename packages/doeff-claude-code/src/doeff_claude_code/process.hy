;;; claude の print mode の子 process の器 — Popen・stdin の書き手の thread・stdout / stderr の読み手の thread・降ろす手順。
;;;
;;; 判断は持たない(何を書くか・手番はいつ終わるかは dialogue.hy)。handler.hy だけがこの器を使い、公開 effect の型には
;;; 1 語も出ない。移した元: doeff-agents の sessionhost/headless_process.py(HeadlessProcess・_StdinWriter・retire の梯子)。
;;;
;;; 読み手と書き手を分ける: 読みの thread が stdin へ書くと、CLI が stdin を読んでいない拍に stdout の読みまで止まる。
;;; 降ろす手順 = stdin に EOF → 猶予 → SIGTERM → 猶予 → SIGKILL → 猶予(retire は自分の thread で回し、呼び手を止めない)。
(import collections [deque])
(import contextlib)
(import queue)
(import signal)
(import subprocess)
(import threading)

(setv EOF-GRACE-SECONDS 5.0)
(setv TERM-GRACE-SECONDS 5.0)
(setv STDERR-TAIL-LINES 20)


(defclass StdinWriter []
  "stdin へ書く thread 1 本。close は積んだ行の後に EOF を出す(冪等)。"

  (defn __init__ [self stdin]
    (setv self.stdin stdin
          self.pending (queue.Queue)
          self.closed False
          self.broken False
          self.thread (threading.Thread :target self.run :name "claude-stdin" :daemon True))
    (.start self.thread))

  (defn send [self #^ str line]
    (when (not self.closed)
      (.put self.pending line)))

  (defn close [self]
    (when (not self.closed)
      (setv self.closed True)
      (.put self.pending None)))

  (defn run [self]
    (while True
      (setv item (.get self.pending))
      (when (is item None) (break))
      (try
        (.write self.stdin (if (.endswith item "\n") item (+ item "\n")))
        (.flush self.stdin)
        (except [#(BrokenPipeError OSError ValueError)]
          (setv self.broken True)
          (break))))
    (with [(contextlib.suppress BrokenPipeError OSError ValueError)]
      (.close self.stdin))))


(defclass ClaudeProcess []
  "1 つの claude の print mode の process。on-line(raw) は stdout の 1 行ごとに読み手の thread から、on-exit(exit-code stderr-tail) は
   stdout の EOF と process の終わりの後に 1 度だけ呼ぶ。"

  (defn __init__ [self #^ list argv #^ str cwd #^ dict env on-line on-exit]
    (setv self.argv (tuple argv)
          self.on-line on-line
          self.on-exit on-exit
          self.stderr-lines (deque :maxlen STDERR-TAIL-LINES)
          self.retire-lock (threading.Lock)
          self.retiring None
          self.went-down None)
    (setv self.process (subprocess.Popen argv :cwd cwd :env env
                                         :stdin subprocess.PIPE :stdout subprocess.PIPE :stderr subprocess.PIPE
                                         :text True :encoding "utf-8" :errors "replace" :bufsize 1))
    (setv self.writer (StdinWriter self.process.stdin))
    (setv self.stderr-reader (threading.Thread :target self.read-stderr :name "claude-stderr" :daemon True))
    (.start self.stderr-reader)
    (setv self.reader (threading.Thread :target self.read-stdout :name "claude-stdout" :daemon True))
    (.start self.reader))

  (defn [property] #^ int pid [self] self.process.pid)

  (defn #^ bool alive [self] (is (.poll self.process) None))

  (defn exit-code [self] (.poll self.process))

  (defn #^ str stderr-tail [self] (.strip (.join "" (list self.stderr-lines))))

  ;; -- 運ぶ ------------------------------------------------------------------------------------

  (defn send [self #^ str line] (.send self.writer line))

  (defn close-stdin [self] (.close self.writer))

  (defn #^ bool interrupt [self]
    "SIGINT(降りていれば偽)。"
    (when (not (.alive self)) (return False))
    (try
      (.send-signal self.process signal.SIGINT)
      (except [OSError] (return False)))
    True)

  (defn #^ bool drop [self]
    "SIGKILL(検の口 ClaudeDropProcess — OOM や kill と同じ形で消す)。降りていれば偽。"
    (when (not (.alive self)) (return False))
    (with [(contextlib.suppress OSError)]
      (.kill self.process))
    True)

  ;; -- 降ろす ----------------------------------------------------------------------------------

  (defn #^ bool went-down-within [self #^ float grace]
    (try
      (.wait self.process :timeout grace)
      (except [subprocess.TimeoutExpired] (return False)))
    True)

  (defn escort-down [self]
    "降りるまで付き添う梯子(有界)。結果は self.went-down に置く。"
    (setv down (or (.went-down-within self EOF-GRACE-SECONDS)
                   (do (when (.alive self) (with [(contextlib.suppress OSError)] (.terminate self.process)))
                       (.went-down-within self TERM-GRACE-SECONDS))
                   (do (when (.alive self) (with [(contextlib.suppress OSError)] (.kill self.process)))
                       (.went-down-within self TERM-GRACE-SECONDS))))
    (setv self.went-down down))

  (defn retire [self]
    "降ろし始める(冪等・呼び手を止めない): stdin に EOF を出し、梯子を自分の thread で回す。"
    (with [self.retire-lock]
      (when (is-not self.retiring None) (return None))
      (setv self.retiring (threading.Thread :target self.escort-down :name "claude-retire" :daemon True))
      (.close-stdin self)
      (.start self.retiring)))

  (defn #^ bool retire-finished [self]
    "梯子を踏み終えたか(降ろし始めていなければ偽)。"
    (and (is-not self.retiring None) (not (.is-alive self.retiring))))

  ;; -- 読み手 ----------------------------------------------------------------------------------

  (defn read-stderr [self]
    (for [raw self.process.stderr]
      (.append self.stderr-lines raw)))

  (defn read-stdout [self]
    (try
      (for [raw self.process.stdout]
        (self.on-line raw))
      (finally
        (with [(contextlib.suppress OSError)]
          (.close self.process.stdout))
        (setv code (.wait self.process))
        (.join self.stderr-reader 2.0)
        (self.on-exit code (.stderr-tail self))))))
