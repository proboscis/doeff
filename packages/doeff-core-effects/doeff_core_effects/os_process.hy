;;; 汎用の子 process の effect(process_effects.hy)の本物の答え手 subprocess-handler(agora-redesign #802 便 1)。subprocess と os.environ を
;;; 呼んで値を詰め替えるだけで、判断を持たない。doeff-agents の driver-io-handler も RunProcess に同じ run-subprocess で答える(実装は 1 つ)。
;;; 子の標準入力・標準出力・標準エラーと output-path は utf-8 と surrogateescape で読み書きする(可逆 — process_effects.hy の頭の註・
;;; agora-redesign #2160)。
;;;
;;; process-group・stream-output(agora-redesign #2184)を使う呼びだけ、子を Popen で起こし、標準出力と標準エラーを thread 2 本で行ごとに
;;; 読みながら待つ(run-watched)。使わない呼びは前と同じ subprocess.run の道(run-subprocess の頭の枝)を通る。
;;; offloaded-subprocess-handler は同じ実装を、呼び 1 つに thread 1 本(offloaded_call.hy の ThreadPerCall)で回す — 子を待つ間も
;;; scheduler の他の task が回る。外側に scheduled が要る。待っている task が取り消されても、走り出した子は止めない(答えは捨てる)。
;;; StartProcess・PollProcess・StopProcess(agora-redesign #2223)・SignalProcess(#2461)は、立てた子を process に 1 つの表 STARTED-CHILDREN で
;;; 持つ。立てる・問う・signal を送るは待たないので、どちらの答え手もその場で答える。止めるは猶予の間だけ待つので、offloaded-subprocess-handler
;;; では thread で回す。
(require doeff-hy.macros [defhandler defk <- val var])
(import contextlib)
(import fnmatch)
(import importlib.util)
(import io)
(import os)
(import signal)
(import subprocess)
(import sys)
(import threading)
(import time)
(import doeff_core_effects.file_effects [FileFailed PathKind PathStat])
(import doeff_core_effects.os_file [stat-path])
(import doeff_core_effects.offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory
                                            ProcessAlive StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted
                                            ProcessRunning ProcessExited ProcessNotChild SignalProcess ProcessSignal ProcessSignalled
                                            ReadInterpreter ResolveModule InterpreterFacts ModuleFound ModuleNotFound
                                            timed-out-outcome not-started-outcome executable-file-answer])

;; offloaded-subprocess-handler の thread(呼び 1 つに 1 本 — 同時の数の上限は呼び手が並べる数)。
(val PROCESS-THREADS (ThreadPerCall))


(defclass StartedChildren []
  "StartProcess で立てた子の表(pid → #(Popen process-group))を process に 1 つ持つため(agora-redesign #2223 — 子は OS の process ごとの資源で、
   答え手を積み直しても同じ子を問える)。Popen を捨てると subprocess の後始末が子を回収して終了 code を奪うので、終わりを答えるまで表で持つ。
   表の読み書きは 1 つの錠の下(offloaded の thread と本体が並んで触る)。"
  (defn __init__ [self]
    (setv self.lock (threading.Lock))
    (setv self.children {}))

  (defn add [self child process-group]
    "立てた子を表に置く。"
    (with [self.lock]
      (setv (get self.children child.pid) #(child process-group))))

  (defn find [self pid]
    "pid の子の #(Popen process-group)— 立てていない pid は None。"
    (with [self.lock]
      (.get self.children pid)))

  (defn forget [self pid]
    "終わりを答えた子を表から外す(この後その pid は ProcessNotChild)。"
    (with [self.lock]
      (.pop self.children pid None))))


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


(defk run-watched [argv stdin timeout cwd child-env output-path process-group stop-grace stream-output]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: child-env (| dict None))
         (: output-path (| str None)) (: process-group bool) (: stop-grace float) (: stream-output bool)]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "子を Popen で起こし、出力を thread で読みながら待つため(process-group か stream-output を使う呼び — 頭の註)。待ち方は communicate と
   同じ = 子の終了に加えて出力の EOF。時間内に終われば、process-group なら group に残った孫へ SIGTERM を送る。時間切れは stop-child で
   止め、それまでの出力を持つ時間切れの答えにする。stream-output なら output-path を先に開き、届いた行をその場で書く(開けなければ子を
   起こさずに OSError が上がる — 子の後に足せない時と同じ)。"
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
            (:= outcome timed-out))))
    (finally
      (when sink
        (.close sink))))
  outcome)


(defk run-subprocess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None)) (: env-drop tuple) (: process-group bool) (: stop-grace (| int float))
         (: stream-output bool)]
   :post [(: % ProcessOutcome)]}
  "子 process を 1 回走らせて ProcessOutcome にするため(env と env-mode の読み方は child-environment)。
   exit-code は returncode を丸めない。時間切れと起こせない形は値で返す(process_effects.hy の頭の註)。process-group か stream-output を
   使う呼びは run-watched、使わない呼びは前と同じ subprocess.run。stream-output で書いた output-path には後から足さない。"
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (var outcome None)
  (if (or process-group stream-output)
      (do (<- watched ProcessOutcome (run-watched argv stdin timeout cwd child-env output-path process-group (float stop-grace) stream-output))
          (:= outcome watched))
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
          (:= outcome refused))))
  (when (not stream-output)
    (<- (append-output output-path outcome.stdout outcome.stderr)))
  outcome)


(defk start-child-process [argv cwd env env-mode env-drop stdout-path stderr-path process-group]
  {:pre [(: argv tuple) (: cwd (| str None)) (: env (| tuple None)) (: env-mode EnvMode) (: env-drop tuple) (: stdout-path (| str None))
         (: stderr-path (| str None)) (: process-group bool)]
   :post [(: % (| ProcessStarted ProcessNotStarted))] :tags {:context "process" :role "foundation"}}
  "StartProcess に本物の子で答えるため: 出力の file を末尾へ足す形で先に開き(開けなければ立てない)、標準入力の無い子を Popen で立てて
   STARTED-CHILDREN に置き、終わりを待たずに pid を返す。立てられない理由は OSError の文のまま(RunProcess の起こせない形と同じ)。"
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (try
    (with [streams (contextlib.ExitStack)]
      (val out (if (is stdout-path None) subprocess.DEVNULL (.enter-context streams (open stdout-path "ab"))))
      (val err (if (is stderr-path None) subprocess.DEVNULL (.enter-context streams (open stderr-path "ab"))))
      (val child (subprocess.Popen (list argv) :stdin subprocess.DEVNULL :stdout out :stderr err :cwd cwd :env child-env
                                   :start-new-session process-group))
      (.add STARTED-CHILDREN child process-group)
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
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name os.environ) (EnvEntry :name name :value (get os.environ name))))))
  (WorkingDirectory []
    (resume (os.getcwd)))
  (ProcessAlive [pid]
    (<- alive (os-process-alive pid))
    (resume alive))
  (ReadInterpreter []
    (<- facts InterpreterFacts (os-interpreter-facts))
    (resume facts))
  (ResolveModule [name]
    (<- found (| ModuleFound ModuleNotFound) (os-module-location name))
    (resume found))
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group]
    (<- started (start-child-process argv cwd env env-mode env-drop stdout-path stderr-path process-group))
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


(defhandler offloaded-subprocess-handler
  ;; 本物の子 process(subprocess-handler と同じ実装)を、呼び 1 つに thread 1 本で回す(頭の註)。外側に scheduled が要る。
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    (<- outcome (offloaded PROCESS-THREADS
                           (fn [] (run-detached (run-subprocess argv stdin timeout cwd env env-mode output-path env-drop
                                                                process-group stop-grace stream-output)))
                           keep-nothing))
    (resume outcome))
  (ExecutableAt [path]
    (<- found (os-executable-at path))
    (resume found))
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name os.environ) (EnvEntry :name name :value (get os.environ name))))))
  (WorkingDirectory []
    (resume (os.getcwd)))
  (ProcessAlive [pid]
    (<- alive (os-process-alive pid))
    (resume alive))
  (ReadInterpreter []
    (<- facts InterpreterFacts (os-interpreter-facts))
    (resume facts))
  (ResolveModule [name]
    (<- found (| ModuleFound ModuleNotFound) (os-module-location name))
    (resume found))
  ;; 立てる・問うは待たないのでその場で答える。止めるは猶予の間だけ待つので thread で回す。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group]
    (<- started (start-child-process argv cwd env env-mode env-drop stdout-path stderr-path process-group))
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
