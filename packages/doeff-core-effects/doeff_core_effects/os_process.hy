;;; 汎用の子 process の effect(process_effects.hy)の本物の答え手 subprocess-handler(agora-redesign #802 便 1)。subprocess と os.environ を
;;; 呼んで値を詰め替えるだけで、判断を持たない。doeff-agents の driver-io-handler も RunProcess に同じ run-subprocess で答える(実装は 1 つ)。
;;; ReadMachineName は socket.gethostname をそのまま答える(agora-redesign #3050)。
;;; 子の標準入力・標準出力・標準エラーと output-path は utf-8 と surrogateescape で読み書きする(可逆 — process_effects.hy の頭の註・
;;; agora-redesign #2160)。
;;;
;;; process-group・stream-output(agora-redesign #2184)を使う呼びだけ、子を Popen で起こし、標準出力と標準エラーを thread 2 本で行ごとに
;;; 読みながら待つ(run-watched)。使わない呼びは subprocess.run と同じ待ち方(Popen の communicate — run-communicated)の道を通る。
;;; offloaded-subprocess-handler は同じ実装を、呼び 1 つに thread 1 本(offloaded_call.hy の ThreadPerCall)で回す — 子を待つ間も
;;; scheduler の他の task が回る。外側に scheduled が要る。
;;;
;;; 待っている task が取り消された時(agora-redesign #2847): 答えは捨て(keep-nothing)、走っている子は止める。止めるのは offloaded の
;;; 止め方(interrupt)で、別の thread の新しい VM で回す(scheduler の thread を猶予の間塞がない)。process-group なら group へ、そうでなければ
;;; 子へ SIGTERM → RunProcess の stop-grace 秒の内に(group なら孫も)居なくなるのを待つ → 残れば SIGKILL を送って回収する。子と取り消しの
;;; 待ち合わせは ChildWatch(仕事の thread が起こした子を置き、取り消しが取り出す — 子を置く前の取り消しは、置いた仕事の thread が止める)。
;;; 止めた子は log の 1 行(logger doeff_core_effects.os_process の warning — argv の頭・pid・止め方)で名指し、計器の答え手を渡した
;;; metered-offloaded-subprocess-handler では counter process_cancel_terminated(SIGTERM で止まった)・process_cancel_killed
;;; (SIGKILL まで要った — 描くと名に _total が付く)に CountMetric で 1 つ数える。数えるのは要求の run の外なので、計器は handler を入れる所が渡す(要求の run の答え手の
;;; 積みは届かない)。止める VM は新しいので計器の外側に state を置く — run をまたいで数えが残るのは process に 1 つの置き場の答え手
;;; (process-meter-handler)で、1 つの run の中の答え手(memory-meter-handler)を渡しても要求の run の断面には出ない。
;;; offloaded-subprocess-handler は計器の無い形(数えず、log にだけ出す — 計器の答え手の無い使い手が「答え手が無い」で落ちない)。
;;; 取り消しの前に子が終わっていれば何も送らず数えない。subprocess-handler(thread で回さない)の待ちは取り消しで抜けない(子を待ち切る)。
;;; StartProcess・PollProcess・StopProcess(agora-redesign #2223)・SignalProcess(#2461)は、立てた子を process に 1 つの表 STARTED-CHILDREN で
;;; 持つ。立てる・問う・signal を送るは待たないので、どちらの答え手もその場で答える。止めるは猶予の間だけ待つので、offloaded-subprocess-handler
;;; では thread で回す。
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import collections.abc [Callable])
(import contextlib)
(import fnmatch)
(import importlib.util)
(import io)
(import logging)
(import os)
(import signal)
(import socket)
(import subprocess)
(import sys)
(import threading)
(import time)
(import doeff [with-handlers])
(import doeff_core_effects.file_effects [FileFailed PathKind PathStat])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.meter_effects [CountMetric])
(import doeff_core_effects.os_file [stat-path])
(import doeff_core_effects.offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory
                                            ProcessAlive StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted
                                            ProcessRunning ProcessExited ProcessNotChild SignalProcess ProcessSignal ProcessSignalled
                                            ReadInterpreter ReadMachineName ResolveModule InterpreterFacts ModuleFound ModuleNotFound
                                            timed-out-outcome not-started-outcome executable-file-answer environment-answer])

;; offloaded-subprocess-handler の thread(呼び 1 つに 1 本 — 同時の数の上限は呼び手が並べる数)。
(val PROCESS-THREADS (ThreadPerCall))

;; 取り消しで止めた子を数える counter の名(頭の註): SIGTERM で止まった数・猶予の後に SIGKILL まで要った数。名に _total を付けない —
;; 描き手(meter_prometheus・doeff-cluster の coordinator の /metrics)が counter の名に _total を足す(#2938)。
(val CANCEL-TERMINATED-METRIC "process_cancel_terminated")
(val CANCEL-KILLED-METRIC "process_cancel_killed")
;; 取り消しで止めた group に残った process(孫)が居なくなるのを問う間隔(秒)。
(val GROUP-POLL 0.02)


;; 取り消しで子を止めた結末(頭の註): NOT-RUNNING = 止める前に終わっていた(何も送らない)・TERMINATED = SIGTERM で猶予の内に止まった・
;; KILLED = 猶予の後も残り SIGKILL で止めた。
(defenum CancelStop NOT-RUNNING TERMINATED KILLED)


(defrecord WatchedChild
  "取り消しで止める子 1 つ(ChildWatch の置き場の中身): child = 仕事の thread が起こした子・process-group = 子を自分の group で起こしたか
   (group ごと止める)。"
  {:tags {:context "process" :role "type"}}
  (#^ subprocess.Popen child)
  (#^ bool process-group))


(defclass ChildWatch []
  "RunProcess 1 回の子と、その要求の取り消しの待ち合わせ(頭の註)。仕事の thread は起こした子を置き(attach)、終わりを見届けたら外す
   (release)。取り消しの手当ては印を付けて、置かれている子を取り出す(cancel)。どちらが先でも、走っている子は 1 度だけ止める側へ渡る —
   子を置く前に取り消されていれば、attach が置かずに知らせ、置こうとした仕事の thread が止める。stop-grace = SIGTERM から SIGKILL までの
   猶予の秒(RunProcess の stop-grace)・meter = 止めた子を数える計器の答え手(None = 数えない)。置き場の読み書きは 1 つの錠の下(仕事の
   thread と取り消しの thread が並んで触る)。資源の係なので値の型ではない。"

  (deff __init__ [self stop-grace meter]  ; defk にできない: 資源の class の初期化
    {:pre [(: self ChildWatch) (: stop-grace float) (: meter (| Callable None))] :post [(: % None)]}
    "止め方の猶予と計器を持ち、子の置き場を空で始めるため。"
    (setv self.stop-grace stop-grace
          self.meter meter
          self.lock (threading.Lock)
          self.cancelled False
          self.running None))

  (deff attach [self child process-group]  ; defk にできない: 資源の class の錠の口(仕事の thread と取り消しの thread の間の同期 — VM の外の錠)
    {:pre [(: self ChildWatch) (: child subprocess.Popen) (: process-group bool)] :post [(: % bool)]}
    "起こした子を置くため。答え = もう取り消されていたか(True なら置かない — 呼び手がその場で止める)。"
    (with [self.lock]
      (if self.cancelled
          True
          (do (setv self.running (WatchedChild :child child :process-group process-group))
              False))))

  (deff release [self]  ; defk にできない: 資源の class の錠の口(仕事の thread と取り消しの thread の間の同期 — VM の外の錠)
    {:pre [(: self ChildWatch)] :post [(: % None)]}
    "子の終わりを見届けた後に置き場を空けるため(この後の取り消しは止める子を持たない)。"
    (with [self.lock]
      (setv self.running None)))

  (deff cancel [self]  ; defk にできない: 資源の class の錠の口(仕事の thread と取り消しの thread の間の同期 — VM の外の錠)
    {:pre [(: self ChildWatch)] :post [(: % (| WatchedChild None))]}
    "取り消しの印を付け、置かれている子を取り出すため(None = まだ置かれていないか、もう外した — 子を置く時に仕事の thread が止める)。"
    (with [self.lock]
      (setv self.cancelled True)
      (setv found self.running)
      (setv self.running None)
      found)))


(defclass StartedChildren []
  "StartProcess で立てた子の表(pid → #(Popen process-group reap-group))を process に 1 つ持つため(agora-redesign #2223 — 子は OS の process ごとの資源で、
   答え手を積み直しても同じ子を問える)。Popen を捨てると subprocess の後始末が子を回収して終了 code を奪うので、終わりを答えるまで表で持つ。
   表の読み書きは 1 つの錠の下(offloaded の thread と本体が並んで触る)。"
  (defn __init__ [self]
    (setv self.lock (threading.Lock))
    (setv self.children {}))

  (defn add [self child process-group reap-group]
    "立てた子を表に置く。"
    (with [self.lock]
      (setv (get self.children child.pid) #(child process-group reap-group))))

  (defn find [self pid]
    "pid の子の #(Popen process-group reap-group)— 立てていない pid は None。"
    (with [self.lock]
      (.get self.children pid)))

  (defn forget [self pid]
    "終わりを答えた子を表から外す(この後その pid は ProcessNotChild)。答え手が握っていた子の標準入力の pipe(hold-stdin)を閉じ、
     reap-group の子は group に残った process へ SIGKILL を送る(#2471)。"
    (with [self.lock]
      (setv found (.pop self.children pid None)))
    (when (is-not found None)
      (setv #(child process-group reap-group) found)
      (when (is-not child.stdin None)
        (with [(contextlib.suppress OSError)] (.close child.stdin)))
      (when (and process-group reap-group)
        (with [(contextlib.suppress ProcessLookupError PermissionError)] (os.killpg pid signal.SIGKILL))))))


;; StartProcess で立てた子の表(process に 1 つ — StartedChildren の註)。
(val STARTED-CHILDREN (StartedChildren))


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


(defk os-process-alive [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "pid の process が生きているかを signal 0 で確かめるため(着地の列の窓が、死んだ走者の行を見分ける — agora-redesign #2184)。送る権限が
   無いだけの process は生きている。0 以下の pid は group への signal になるので送らずに生きていないと答える。"
  (if (<= pid 0)
      False
      (try
        (do (os.kill pid 0) True)
        (except [ProcessLookupError] False)
        (except [PermissionError] True))))


(defk os-interpreter-facts []
  {:pre [] :post [(: % InterpreterFacts)] :tags {:context "process" :role "foundation"}}
  "自分の process の Python の interpreter の事実(sys.prefix を symlink まで解いた絶対 path・pid)を読むため — venv の上の root を辿る
   入口の検め(doeff-cluster)が、interpreter の置き場を直に読まずに効果で問う(agora-redesign #2347)。"
  (InterpreterFacts :prefix (os.path.realpath sys.prefix) :pid (os.getpid)))


(defk os-module-location [name]
  {:pre [(: name str)] :post [(: % (| ModuleFound ModuleNotFound))] :tags {:context "process" :role "foundation"}}
  "module の名を、この process の import が解く置き場(file・submodule を探す dir — symlink まで解いた絶対 path)にするため — どの木の
   code を動かしているかを確かめる入口の検め(doeff-cluster)が、import の仕組みを直に読まずに効果で問う(agora-redesign #2347)。
   import はしない(点の付いた名は親の package を import する — importlib.util.find_spec と同じ)。解けない・名が壊れている・親を読めない
   = ModuleNotFound。file を持たない module(__init__ の無い package・built-in・frozen)の origin は None。"
  (val spec (try (importlib.util.find-spec name)
                 (except [[ImportError ValueError]] None)))
  (if (is spec None)
      (ModuleNotFound :name name)
      (ModuleFound :name name
                   :origin (if (and spec.origin (not-in spec.origin #("built-in" "frozen"))) (os.path.realpath spec.origin) None)
                   :search-locations (tuple (gfor d (or spec.submodule-search-locations []) (os.path.realpath d))))))


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


(defk signal-group [pgid sig]
  {:pre [(: pgid int) (: sig int)] :post [(: % None)] :tags {:context "process" :role "foundation"}}
  "子の process group へ signal を 1 回送るため — 消えた group と、送る権限の無い group は触らない。"
  (with [(contextlib.suppress ProcessLookupError PermissionError)]
    (os.killpg pgid sig))
  None)


(defk group-alive [pgid]
  {:pre [(: pgid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "子の process group に process が残っているかを signal 0 で問うため(取り消しで止めた group の孫を見届ける)。送る権限が無いだけの
   process は残っている。"
  (try
    (do (os.killpg pgid 0) True)
    (except [ProcessLookupError] False)
    (except [PermissionError] True)))


(defk group-gone-by [child deadline]
  {:pre [(: child subprocess.Popen) (: deadline float)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "SIGTERM を送った group が期限(monotonic の秒)の内に空になったかを答えるため: group の頭の子の終わりを期限まで待ち(回収する)、その後
   group に残った process(背景の孫)が居なくなるのを GROUP-POLL 秒ごとに期限まで問う。"
  (val leader-gone (try
                     (do (.wait child :timeout (max 0.0 (- deadline (time.monotonic)))) True)
                     (except [subprocess.TimeoutExpired] False)))
  (var left True)
  (when leader-gone
    (<- first bool (group-alive child.pid))
    (:= left first)
    (while (and left (< (time.monotonic) deadline))
      (time.sleep GROUP-POLL)
      (<- again bool (group-alive child.pid))
      (:= left again)))
  (not left))


(defk stop-for-cancel [child process-group stop-grace]
  {:pre [(: child subprocess.Popen) (: process-group bool) (: stop-grace float)] :post [(: % CancelStop)]
   :tags {:context "process" :role "foundation"}}
  "要求が取り消された子を止めるため(頭の註): process-group なら group へ、そうでなければ子へ SIGTERM を送り、stop-grace 秒の内に(group
   なら孫も)居なくなれば TERMINATED、残れば SIGKILL を送って回収し KILLED。止める前に終わっていた子(group なら残りも無い)には何も
   送らず NOT-RUNNING。子だけの SIGTERM・SIGKILL は Popen の口で送る(回収済みの子の pid へは送らない)。"
  (val deadline (+ (time.monotonic) stop-grace))
  (val leader-done (is-not (.poll child) None))
  (if process-group
      (do
        (<- leftover bool (group-alive child.pid))
        (if (and leader-done (not leftover))
            CancelStop.NOT-RUNNING
            (do
              (<- (signal-group child.pid signal.SIGTERM))
              (<- gone bool (group-gone-by child deadline))
              (if gone
                  CancelStop.TERMINATED
                  (do
                    (<- (signal-group child.pid signal.SIGKILL))
                    (.wait child)
                    CancelStop.KILLED)))))
      (if leader-done
          CancelStop.NOT-RUNNING
          (do
            (.terminate child)
            (try
              (do (.wait child :timeout stop-grace) CancelStop.TERMINATED)
              (except [subprocess.TimeoutExpired]
                (.kill child)
                (.wait child)
                CancelStop.KILLED))))))


(defk reported-stop [child how metric meter]
  {:pre [(: child subprocess.Popen) (: how str) (: metric str) (: meter (| Callable None))] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "取り消しで止めた子を log の 1 行で名指し(argv の頭・pid・止め方)、meter が在れば metric の counter に 1 つ数えるため(頭の註 — 止める
   VM は新しいので計器の外側に state を置く)。"
  (.warning (logging.getLogger __name__) "要求が取り消された子 process を止めた: argv の頭 %r・pid %d・%s" (get child.args 0) child.pid how)
  (when (is-not meter None)
    (<- (with-handlers [(state) meter] (CountMetric metric))))
  None)


(defk stop-cancelled-child [child process-group watch]
  {:pre [(: child subprocess.Popen) (: process-group bool) (: watch ChildWatch)] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "要求が取り消された子を watch の猶予で止め、止めた事を watch の計器へ名指して数えるため(止める前に終わっていた子は数えない)。"
  (<- stopped CancelStop (stop-for-cancel child process-group watch.stop-grace))
  (match stopped
    CancelStop.TERMINATED (<- (reported-stop child "SIGTERM で止まった" CANCEL-TERMINATED-METRIC watch.meter))
    CancelStop.KILLED (<- (reported-stop child (.format "猶予 {} 秒の後も残り SIGKILL で止めた" watch.stop-grace) CANCEL-KILLED-METRIC
                                         watch.meter))
    CancelStop.NOT-RUNNING None)
  None)


(defk watched-child [watch child process-group]
  {:pre [(: watch ChildWatch) (: child subprocess.Popen) (: process-group bool)] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "起こした子を watch に置くため(取り消しの手当てが止められるように)。置く前に要求が取り消されていれば、置かずにここで止めて数える
   (ChildWatch の註)。"
  (when (.attach watch child process-group)
    (<- (stop-cancelled-child child process-group watch)))
  None)


(defk stop-on-cancel [watch]
  {:pre [(: watch ChildWatch)] :post [(: % None)] :tags {:context "process" :role "foundation"}}
  "要求の取り消しの手当てのため(offloaded の止め方 — 仕事の thread とは別の thread の新しい VM で回る): 取り消しの印を付け、置かれている子
   が在れば止めて数える。子がまだ置かれていなければ、置く時に仕事の thread が止める(ChildWatch の註)。"
  (match (.cancel watch)
    None None
    (WatchedChild :child child :process-group process-group) (<- (stop-cancelled-child child process-group watch)))
  None)


(defclass ChildPipes []
  "子 1 つの標準入力を thread で渡し、標準出力と標準エラーを thread 2 本で行ごとに読んで、それぞれの全文を溜めながら sink(開いた
   output-path — None なら書かない)へ届いた順に書くため(run-watched の部品 — 書きは 1 つの錠の下)。sink に書けない時は、写しを失う
   だけで読みは続ける(子の出力を止めない)。"
  (defn __init__ [self child sink]
    (setv self.child child)
    (setv self.sink sink)
    (setv self.lock (threading.Lock))
    (setv self.out (io.StringIO))
    (setv self.err (io.StringIO))
    (setv self.readers (tuple (gfor #(buffer stream) #(#(self.out child.stdout) #(self.err child.stderr))
                                (threading.Thread :target self.take :args #(buffer stream) :daemon True)))))

  (defn take [self buffer stream]
    "1 本の流れを EOF まで行ごとに読む(読み手の thread の本体)。"
    (for [line (iter stream.readline "")]
      (with [self.lock]
        (.write buffer line)
        (when self.sink
          (with [(contextlib.suppress OSError)]
            (.write self.sink line)
            (.flush self.sink))))))

  (defn give [self text]
    "標準入力に text を書いて閉じる(書き手の thread の本体 — 子が読まずに終わっても止まらない)。"
    (with [(contextlib.suppress BrokenPipeError OSError)]
      (.write self.child.stdin text)
      (.close self.child.stdin)))

  (defn start [self stdin]
    "読み手を立て、stdin が在れば書き手も立てる。"
    (for [reader self.readers]
      (.start reader))
    (when (is-not stdin None)
      (.start (threading.Thread :target self.give :args #(stdin) :daemon True))))

  (defn drained [self until]
    "読み手を期限(monotonic の秒・None = 待ち切る)まで待つ。答え = 両方とも EOF まで読み終えたか。"
    (for [reader self.readers]
      (.join reader (if (is until None) None (max 0.0 (- until (time.monotonic))))))
    (not (any (gfor reader self.readers (.is-alive reader)))))

  (defn texts [self]
    "溜めた標準出力と標準エラーの全文。"
    (with [self.lock]
      #((.getvalue self.out) (.getvalue self.err)))))


(defk stop-child [child pipes process-group stop-grace]
  {:pre [(: child subprocess.Popen) (: pipes ChildPipes) (: process-group bool) (: stop-grace float)] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "時間切れの子を止めるため: process-group なら group へ SIGTERM → 猶予 → SIGKILL(孫も止まる)、そうでなければ子だけを kill する
   (subprocess.run と同じ)。止めた後は出力の EOF と子の終わりを猶予の間だけ待つ。"
  (if process-group
      (do
        (<- (signal-group child.pid signal.SIGTERM))
        (when (not (.drained pipes (+ (time.monotonic) stop-grace)))
          (<- (signal-group child.pid signal.SIGKILL))
          (.drained pipes (+ (time.monotonic) stop-grace)))
        (try
          (.wait child :timeout stop-grace)
          (except [subprocess.TimeoutExpired]
            (<- (signal-group child.pid signal.SIGKILL))
            (.wait child))))
      (do
        (.kill child)
        (.drained pipes (+ (time.monotonic) stop-grace))
        (.wait child)))
  None)


(defk run-watched [argv stdin timeout cwd child-env output-path process-group stop-grace stream-output watch]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: child-env (| dict None))
         (: output-path (| str None)) (: process-group bool) (: stop-grace float) (: stream-output bool) (: watch ChildWatch)]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "子を Popen で起こし、出力を thread で読みながら待つため(process-group か stream-output を使う呼び — 頭の註)。待ち方は communicate と
   同じ = 子の終了に加えて出力の EOF。時間内に終われば、process-group なら group に残った孫へ SIGTERM を送る。時間切れは stop-child で
   止め、それまでの出力を持つ時間切れの答えにする。stream-output なら output-path を先に開き、届いた行をその場で書く(開けなければ子を
   起こさずに OSError が上がる — 子の後に足せない時と同じ)。起こした子は終わりを見届けるまで watch に置く(要求の取り消しで止める —
   頭の註)。"
  (val sink (if (and stream-output (is-not output-path None))
                (open output-path "a" :encoding "utf-8" :errors "surrogateescape")
                None))
  (var outcome None)
  (try
    (var child None)
    (try
      (:= child (subprocess.Popen (list argv)
                                  :stdin (if (is stdin None) None subprocess.PIPE)
                                  :stdout subprocess.PIPE
                                  :stderr subprocess.PIPE
                                  :text True
                                  :encoding "utf-8"
                                  :errors "surrogateescape"
                                  :cwd cwd
                                  :env child-env
                                  :start-new-session process-group))
      (except [error OSError]
        (<- refused ProcessOutcome (not-started-outcome (str error)))
        (:= outcome refused)))
    (when (is-not child None)
      (try
        (<- (watched-child watch child process-group))
        (val pipes (ChildPipes child sink))
        (.start pipes stdin)
        (val until (if (is timeout None) None (+ (time.monotonic) timeout)))
        (val finished (try
                        (do (.wait child :timeout (if (is until None) None (max 0.0 (- until (time.monotonic)))))
                            (.drained pipes until))
                        (except [subprocess.TimeoutExpired] False)))
        (if finished
            (do
              (when process-group
                (<- (signal-group child.pid signal.SIGTERM)))
              (val texts (.texts pipes))
              (:= outcome (ProcessOutcome :exit-code child.returncode :stdout (get texts 0) :stderr (get texts 1))))
            (do
              (<- (stop-child child pipes process-group (float stop-grace)))
              (val partial (.texts pipes))
              (<- timed-out ProcessOutcome (timed-out-outcome (get partial 0) (get partial 1)))
              (:= outcome timed-out)))
        (finally
          (.release watch))))
    (finally
      (when sink
        (.close sink))))
  outcome)


(defk run-communicated [argv stdin timeout cwd child-env watch]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: child-env (| dict None))
         (: watch ChildWatch)]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "子を subprocess.run と同じ待ち方(Popen の communicate — 時間切れは子だけを kill して回収し、それまでの出力を持つ時間切れの答えに
   する)で 1 回走らせるため(process-group も stream-output も使わない呼び — 頭の註)。subprocess.run を呼ばずに Popen を開くのは、起こした
   子を終わりを見届けるまで watch に置き、要求の取り消しの手当てが走っている子を止められるようにするため(agora-redesign #2847)。"
  (var outcome None)
  (try
    (with [child (subprocess.Popen (list argv)
                                   :stdin (if (is stdin None) None subprocess.PIPE)
                                   :stdout subprocess.PIPE
                                   :stderr subprocess.PIPE
                                   :text True
                                   :encoding "utf-8"
                                   :errors "surrogateescape"
                                   :cwd cwd
                                   :env child-env)]
      (try
        (<- (watched-child watch child False))
        ;; subprocess.run と同じ: 時間切れは子を kill して回収してから上げ直し、ほかの失敗も子を kill して上げ直す(回収は with の終わり)。
        (val texts (try
                     (.communicate child stdin :timeout timeout)
                     (except [subprocess.TimeoutExpired]
                       (.kill child)
                       (.wait child)
                       (raise))
                     (except [BaseException]
                       (.kill child)
                       (raise))))
        (:= outcome (ProcessOutcome :exit-code (.poll child) :stdout (or (get texts 0) "") :stderr (or (get texts 1) "")))
        (finally
          (.release watch))))
    (except [error subprocess.TimeoutExpired]
      (<- partial-out str (decoded error.stdout))
      (<- partial-err str (decoded error.stderr))
      (<- timed-out ProcessOutcome (timed-out-outcome partial-out partial-err))
      (:= outcome timed-out))
    (except [error OSError]
      (<- refused ProcessOutcome (not-started-outcome (str error)))
      (:= outcome refused)))
  outcome)


(defk run-subprocess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output [watch None]]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None)) (: env-drop tuple) (: process-group bool) (: stop-grace (| int float))
         (: stream-output bool) (: watch (| ChildWatch None))]
   :post [(: % ProcessOutcome)]}
  "子 process を 1 回走らせて ProcessOutcome にするため(env と env-mode の読み方は child-environment)。
   exit-code は returncode を丸めない。時間切れと起こせない形は値で返す(process_effects.hy の頭の註)。process-group か stream-output を
   使う呼びは run-watched、使わない呼びは subprocess.run と同じ待ち方の run-communicated。stream-output で書いた output-path には後から
   足さない。watch = 要求の取り消しと子の待ち合わせ(offloaded-subprocess-handler が渡す — 頭の註)。None = 取り消されない要求(子を
   置くだけの watch を作る)。"
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (val watching (if (is watch None) (ChildWatch (float stop-grace) None) watch))
  (var outcome None)
  (if (or process-group stream-output)
      (do (<- watched ProcessOutcome (run-watched argv stdin timeout cwd child-env output-path process-group (float stop-grace) stream-output
                                                  watching))
          (:= outcome watched))
      (do (<- communicated ProcessOutcome (run-communicated argv stdin timeout cwd child-env watching))
          (:= outcome communicated)))
  (when (not stream-output)
    (<- (append-output output-path outcome.stdout outcome.stderr)))
  outcome)


(defk start-child-process [argv cwd env env-mode env-drop stdout-path stderr-path process-group [hold-stdin False] [reap-group False]]
  {:pre [(: argv tuple) (: cwd (| str None)) (: env (| tuple None)) (: env-mode EnvMode) (: env-drop tuple) (: stdout-path (| str None))
         (: stderr-path (| str None)) (: process-group bool) (: hold-stdin bool) (: reap-group bool)]
   :post [(: % (| ProcessStarted ProcessNotStarted))] :tags {:context "process" :role "foundation"}}
  "StartProcess に本物の子で答えるため: 出力の file を末尾へ足す形で先に開き(開けなければ立てない)、標準入力の無い子を Popen で立てて
   STARTED-CHILDREN に置き、終わりを待たずに pid を返す。立てられない理由は OSError の文のまま(RunProcess の起こせない形と同じ)。"
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (try
    (with [streams (contextlib.ExitStack)]
      (val out (if (is stdout-path None) subprocess.DEVNULL (.enter-context streams (open stdout-path "ab"))))
      (val err (if (is stderr-path None) subprocess.DEVNULL (.enter-context streams (open stderr-path "ab"))))
      (val child (subprocess.Popen (list argv) :stdin (if hold-stdin subprocess.PIPE subprocess.DEVNULL) :stdout out :stderr err :cwd cwd
                                   :env child-env :start-new-session process-group))
      (.add STARTED-CHILDREN child process-group reap-group)
      (ProcessStarted :pid child.pid))
    (except [error OSError]
      (ProcessNotStarted :detail (str error)))))


(defk poll-child-process [pid]
  {:pre [(: pid int)] :post [(: % (| ProcessRunning ProcessExited ProcessNotChild))] :tags {:context "process" :role "foundation"}}
  "PollProcess に本物の子で答えるため: 表に無い pid は ProcessNotChild、まだ走っていれば ProcessRunning、終わっていれば表から外して
   ProcessExited(待たない — Popen.poll)。"
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val code (.poll (get found 0)))
        (if (is code None)
            (ProcessRunning :pid pid)
            (do (.forget STARTED-CHILDREN pid)
                (ProcessExited :pid pid :exit-code code))))))


(defk stop-child-process [pid stop-grace]
  {:pre [(: pid int) (: stop-grace float)] :post [(: % (| ProcessExited ProcessNotChild))] :tags {:context "process" :role "foundation"}}
  "StopProcess に本物の子で答えるため: 表に無い pid は ProcessNotChild(他人の process に signal を送らない)。走っていれば、process-group
   なら group へ・そうでなければ子へ SIGTERM → stop-grace 秒待つ → SIGKILL で止め、回収して表から外す。終わっていた子はそのまま回収する。"
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val child (get found 0))
        (val process-group (get found 1))
        (when (is (.poll child) None)
          (if process-group
              (<- (signal-group child.pid signal.SIGTERM))
              (with [(contextlib.suppress ProcessLookupError)] (.terminate child)))
          (try
            (.wait child :timeout stop-grace)
            (except [subprocess.TimeoutExpired]
              (if process-group
                  (<- (signal-group child.pid signal.SIGKILL))
                  (with [(contextlib.suppress ProcessLookupError)] (.kill child)))
              (.wait child))))
        (.forget STARTED-CHILDREN pid)
        (ProcessExited :pid pid :exit-code child.returncode))))


(defk signal-child-process [pid sent]
  {:pre [(: pid int) (: sent ProcessSignal)] :post [(: % (| ProcessSignalled ProcessNotChild))] :tags {:context "process" :role "foundation"}}
  "SignalProcess に本物の子で答えるため: 表に無い pid は ProcessNotChild(他人の process に signal を送らない)。既に終わっていた子には
   送らず delivered False(表に残し、終わりは PollProcess が答えて回収する)。走っていれば、process-group で立てた子は group へ・そうでなければ
   子へ signal を 1 度だけ送り、待たずに delivered True。"
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val child (get found 0))
        (val number (match sent ProcessSignal.TERM signal.SIGTERM ProcessSignal.KILL signal.SIGKILL))
        (if (is-not (.poll child) None)
            (ProcessSignalled :pid pid :delivered False)
            (do (if (get found 1)
                    (<- (signal-group child.pid number))
                    (with [(contextlib.suppress ProcessLookupError)] (.send-signal child number)))
                (ProcessSignalled :pid pid :delivered True))))))


(defhandler subprocess-handler
  ;; 本物の子 process と自分の process の環境(頭の註)。
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    (<- outcome (run-subprocess argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output))
    (resume outcome))
  (ExecutableAt [path]
    (<- found (os-executable-at path))
    (resume found))
  (ReadEnvironment [names prefixes]
    (<- found tuple (environment-answer (tuple (.items os.environ)) names prefixes))
    (resume found))
  (WorkingDirectory []
    (resume (os.getcwd)))
  (ProcessAlive [pid]
    (<- alive (os-process-alive pid))
    (resume alive))
  (ReadInterpreter []
    (<- facts InterpreterFacts (os-interpreter-facts))
    (resume facts))
  (ReadMachineName []
    (resume (socket.gethostname)))
  (ResolveModule [name]
    (<- found (| ModuleFound ModuleNotFound) (os-module-location name))
    (resume found))
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- started (start-child-process argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group))
    (resume started))
  (PollProcess [pid]
    (<- seen (poll-child-process pid))
    (resume seen))
  (SignalProcess [pid signal]
    (<- answer (signal-child-process pid signal))
    (resume answer))
  (StopProcess [pid stop-grace]
    (<- stopped (stop-child-process pid (float stop-grace)))
    (resume stopped)))


(defk offloaded-run [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output meter]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None)) (: env-drop tuple) (: process-group bool) (: stop-grace (| int float))
         (: stream-output bool) (: meter (| Callable None))]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "RunProcess 1 回を PROCESS-THREADS の thread で回して待つため(offloaded-subprocess-handler の答え — 頭の註)。待っている task が取り消され
   たら、答えは捨て(keep-nothing)、走っている子は別の thread で止めて名指し、meter が在れば数える(stop-on-cancel)。"
  (val watch (ChildWatch (float stop-grace) meter))
  (<- outcome ProcessOutcome
      (offloaded PROCESS-THREADS
                 (fn [] (run-detached (run-subprocess argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace
                                                      stream-output watch)))
                 keep-nothing
                 (fn [] (run-detached (stop-on-cancel watch)))))
  outcome)


(defhandler metered-offloaded-subprocess-handler [#^ (| (get Callable #(... object)) None) meter]
  "本物の子 process(subprocess-handler と同じ実装)を、呼び 1 つに thread 1 本で回す(頭の註)。外側に scheduled が要る。待っている task が
   取り消されたら走っている子を止め、止めた子を meter(CountMetric に答える答え手・None = 数えない)の counter に数える。"
  ;; 引数に残す理由: 止めた子を数えるのは要求の run の外(取り消しの後の別の thread の新しい VM)で、要求の run の答え手の積みも Ask も
  ;; 届かない — 計器の答え手は handler を入れる所が渡す(頭の註)。
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    (<- outcome ProcessOutcome (offloaded-run argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace
                                              stream-output meter))
    (resume outcome))
  (ExecutableAt [path]
    (<- found (os-executable-at path))
    (resume found))
  (ReadEnvironment [names prefixes]
    (<- found tuple (environment-answer (tuple (.items os.environ)) names prefixes))
    (resume found))
  (WorkingDirectory []
    (resume (os.getcwd)))
  (ProcessAlive [pid]
    (<- alive (os-process-alive pid))
    (resume alive))
  (ReadInterpreter []
    (<- facts InterpreterFacts (os-interpreter-facts))
    (resume facts))
  (ReadMachineName []
    (resume (socket.gethostname)))
  (ResolveModule [name]
    (<- found (| ModuleFound ModuleNotFound) (os-module-location name))
    (resume found))
  ;; 立てる・問うは待たないのでその場で答える。止めるは猶予の間だけ待つので thread で回す。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- started (start-child-process argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group))
    (resume started))
  (PollProcess [pid]
    (<- seen (poll-child-process pid))
    (resume seen))
  (SignalProcess [pid signal]
    (<- answer (signal-child-process pid signal))
    (resume answer))
  (StopProcess [pid stop-grace]
    (<- stopped (offloaded PROCESS-THREADS (fn [] (run-detached (stop-child-process pid (float stop-grace)))) keep-nothing))
    (resume stopped)))


;; 計器の無い形(頭の註 — 取り消しで止めた子は数えず、log にだけ出す)。
(val offloaded-subprocess-handler (metered-offloaded-subprocess-handler None))
