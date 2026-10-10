;;; codex の app-server の子 process の器 — Popen・stdin の書き手の thread・stdout / stderr の読み手の thread・降ろす手順。
;;;
;;; この package の handler(次の単位で足す — 公開 effect に答える側)だけが session の資源として持つ内部の器で、公開 effect の型には
;;; 1 語も出ない(doeff-claude-code の ClaudeProcess と同じ置き方)。
;;; 判断は持たない(何を書くか・ターンはいつ終わるかは上の層)。1 つの process が thread とターンをまたいで生き、stdin の 1 行が
;;; JSON-RPC の message 1 つ(rpc.hy が作る)、stdout の 1 行が message 1 つ(lines.hy が分ける)。ターンの途中の止めは JSON-RPC の
;;; turn/interrupt で送るので、信号の止めは持たない(信号は降ろす梯子と drop だけ)。
;;;
;;; 読み手と書き手を分ける: 読みの thread が stdin へ書くと、CLI が stdin を読んでいない拍に stdout の読みまで止まる。
;;; 降ろす手順 = stdin に EOF → 猶予 → SIGTERM → 猶予 → SIGKILL → 猶予(retire は自分の thread で回し、呼び手を止めない)。
;;; 同じ形の器が doeff-claude-code の process.hy(claude の print mode)に在る — 2 つの CLI の package が共に使う器へ寄せるのは
;;; 別の単位(上の層の effect と handler が揃った後)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex" :role "process"})
(import contextlib)
(import queue)
(import subprocess)
(import threading)

(val EOF-GRACE-SECONDS 5.0)
(val TERM-GRACE-SECONDS 5.0)
(val STDERR-TAIL-LINES 20)


(defclass StdinWriter []
  "stdin へ書く thread 1 本。close は積んだ行の後に EOF を出す(冪等)。"

  (defn __init__ [self stdin]
    (setv self.stdin stdin
          self.pending (queue.Queue)
          self.closed False
          self.broken False
          self.thread (threading.Thread :target self.run :name "codex-stdin" :daemon True))
    (.start self.thread))

  (defn send [self #^ str line]
    "1 行を書く順番に並べるため(閉じた後の行は捨てる)。"
    (when (not self.closed)
      (.put self.pending line)))

  (defn close [self]
    "並べた行を書き終えた後に stdin を閉じさせるため(app-server は EOF で降りる)。"
    (when (not self.closed)
      (setv self.closed True)
      (.put self.pending None)))

  (defn run [self]
    "並んだ行を 1 つずつ stdin へ書くため(書き手の thread の入口 — pipe が壊れたら書くのをやめる)。"
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


(defclass CodexProcess []
  "1 つの codex の app-server の process。on-line(raw) は stdout の 1 行ごとに読み手の thread から、on-exit(exit-code stderr-tail) は
   stdout の EOF と process の終わりの後に 1 度だけ呼ぶ。on-down() は process が終わった時(stdout の EOF を待たない)と、降ろす梯子を
   踏み終えた時に呼ぶ(待つ側が alive・retire-finished を読み直す合図 — 2 度呼ぶことがある)。env は呼び手が全部渡す(os.environ を
   読まない — 資格の置き場 CODEX_HOME も呼び手が決める)。"

  (defn __init__ [self #^ list argv #^ str cwd #^ dict env on-line on-exit on-down]
    (setv self.argv (tuple argv)
          self.on-line on-line
          self.on-exit on-exit
          self.on-down on-down
          self.stderr-lines #()
          self.retire-lock (threading.Lock)
          self.retiring None
          self.went-down None)
    (setv self.process (subprocess.Popen argv :cwd cwd :env env
                                         :stdin subprocess.PIPE :stdout subprocess.PIPE :stderr subprocess.PIPE
                                         :text True :encoding "utf-8" :errors "replace" :bufsize 1))
    (setv self.writer (StdinWriter self.process.stdin))
    (setv self.stderr-reader (threading.Thread :target self.read-stderr :name "codex-stderr" :daemon True))
    (.start self.stderr-reader)
    (setv self.reader (threading.Thread :target self.read-stdout :name "codex-stdout" :daemon True))
    (.start self.reader)
    (setv self.exit-watcher (threading.Thread :target self.watch-exit :name "codex-exit" :daemon True))
    (.start self.exit-watcher))

  (defn [property] #^ int pid [self]
    "上の層が process を名指して記録するため。"
    self.process.pid)

  (defn #^ bool alive [self]
    "process が降りていないかを知るため。"
    (is (.poll self.process) None))

  (defn exit-code [self]
    "降りた process の終了の code を知るため(降りていなければ None)。"
    (.poll self.process))

  (defn #^ str stderr-tail [self]
    "降りた訳を名乗るため、stderr の最後の STDERR-TAIL-LINES 行を 1 つの文字列で。"
    (.strip (.join "" self.stderr-lines)))

  ;; -- 運ぶ ------------------------------------------------------------------------------------

  (defn send [self #^ str line]
    "JSON-RPC の message 1 行を stdin へ書くため。"
    (.send self.writer line))

  (defn close-stdin [self]
    "stdin に EOF を出し、app-server に降りるよう伝えるため。"
    (.close self.writer))

  (defn #^ bool drop [self]
    "SIGKILL で消すため(OOM や kill と同じ形で消える時の上の層の振る舞いを確かめる口)。降りていれば偽。"
    (when (not (.alive self)) (return False))
    (with [(contextlib.suppress OSError)]
      (.kill self.process))
    True)

  ;; -- 降ろす ----------------------------------------------------------------------------------

  (defn #^ bool went-down-within [self #^ float grace]
    "grace 秒の内に process が降りたかを知るため(降ろす梯子の 1 段)。"
    (try
      (.wait self.process :timeout grace)
      (except [subprocess.TimeoutExpired] (return False)))
    True)

  (defn escort-down [self]
    "降りるまで付き添う梯子(有界 — 降ろしの thread の入口)。結果は self.went-down に置く。"
    (setv down (or (.went-down-within self EOF-GRACE-SECONDS)
                   (do (when (.alive self) (with [(contextlib.suppress OSError)] (.terminate self.process)))
                       (.went-down-within self TERM-GRACE-SECONDS))
                   (do (when (.alive self) (with [(contextlib.suppress OSError)] (.kill self.process)))
                       (.went-down-within self TERM-GRACE-SECONDS))))
    (setv self.went-down down)
    (self.on-down))

  (defn retire [self]
    "降ろし始めるため(冪等・呼び手を止めない): stdin に EOF を出し、梯子を自分の thread で回す。"
    (with [self.retire-lock]
      (when (is-not self.retiring None) (return None))
      (setv self.retiring (threading.Thread :target self.escort-down :name "codex-retire" :daemon True))
      (.close-stdin self)
      (.start self.retiring)))

  (defn #^ bool retire-finished [self]
    "降ろす梯子を踏み終えたかを知るため(降ろし始めていなければ偽)。"
    (and (is-not self.retiring None) (not (.is-alive self.retiring))))

  ;; -- 読み手 ----------------------------------------------------------------------------------

  (defn watch-exit [self]
    "process の終わりを待つ側へ知らせるため(thread の入口 — stdout の EOF を待たずに on-down を呼ぶ)。"
    (.wait self.process)
    (self.on-down))

  (defn read-stderr [self]
    "降りた訳に使う stderr の末尾を持っておくため(thread の入口 — 最後の STDERR-TAIL-LINES 行だけを持ち替える)。"
    (setv stream self.process.stderr)
    (when (is stream None) (raise (RuntimeError "codex の process の stderr を pipe で開いていない")))
    (for [raw stream]
      (setv self.stderr-lines (cut (+ self.stderr-lines #(raw)) (- STDERR-TAIL-LINES) None))))

  (defn read-stdout [self]
    "stdout の行を 1 行ずつ上の層へ渡し、EOF の後に終わりの code と stderr の末尾を 1 度だけ知らせるため(thread の入口)。"
    (setv stream self.process.stdout)
    (when (is stream None) (raise (RuntimeError "codex の process の stdout を pipe で開いていない")))
    (try
      (for [raw stream]
        (self.on-line raw))
      (finally
        (with [(contextlib.suppress OSError)]
          (.close stream))
        (setv code (.wait self.process))
        (.join self.stderr-reader 2.0)
        (self.on-exit code (.stderr-tail self))))))
