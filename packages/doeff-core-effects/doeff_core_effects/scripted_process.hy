;;; 汎用の子 process の effect(process_effects.hy)の I/O なしの答え手 scripted-process-handler(agora-redesign #802 便 1)。本物の子 process を
;;; 起こさず、argv[0] の名(basename)ごとの台本(ScriptedCommand)で答える。台本は Program で、file system の effect(file_effects.hy)で置き場を
;;; 読み書きしてよい(外側の file の答え手 — 多くは memory-file-handler — が受ける)。業務を知らない: 台本の中身は呼び手が渡す。
;;;
;;;   RunProcess        env-mode EXTEND は ProcessScript の env を継いで足した全部を台本に渡す。無い cwd・名の無い命令は started False(exit-code 127)— 本物の subprocess と同じ所・同じ順・同じ文で起こせない
;;;                     (process_effects.hy の not-started-outcome・start-refusal)。output-path は台本の出力をその file の末尾へ足し、足せなければ
;;;                     本物と同じく OSError を上げる。timeout は台本に任せる(台本が timed-out-outcome の答えを返してよい)。
;;;                     env が None の時は台本も None を受ける(呼び手の環境を継ぐ印 — 継いだ中身を読むのは台本の側)。
;;;                     process-group・stop-grace・stream-output(agora-redesign #2184)は台本の要求にそのまま渡す(group と時間の経過は台本の
;;;                     世界に無い — 止め方を答えに表すのは台本の側)。stream-output の output-path は、台本が終わってから出力を足す
;;;                     (届いた順に書く本物と、終わった後の file の中身は同じ)。
;;;                     本物との契約は tests/test_process_contract.hy。
;;;   ProcessAlive      ProcessScript の alive(生きている pid の表)に在るか。0 以下の pid は本物と同じく生きていない。
;;;   StartProcess      出力の file を先に開き(空を足す — 開けなければ本物と同じ文の ProcessNotStarted)、台本を timeout 0 で 1 回走らせる
;;;                     (agora-redesign #2223)。起こせない形の答えは ProcessNotStarted。時間切れの答え = 止めるまで走り続ける子、それ以外 =
;;;                     すぐ終わった子(終了 code を表に置く)。台本の出力(時間切れならそれまでの分)をそれぞれの file の末尾へ足し、
;;;                     SCRIPTED-FIRST-PID からの pid を配る。
;;;   PollProcess       表に無い pid = ProcessNotChild・走り続ける子 = ProcessRunning・終わった子 = ProcessExited(表から外す)。
;;;   StopProcess       表に無い pid = ProcessNotChild・走り続ける子は SIGTERM で終わった形(SCRIPTED-STOPPED-CODE = -15)・終わった子はその
;;;                     終了 code。どれも表から外す。group と猶予は台本の世界に無い。
;;;   ExecutableAt      種類は置き場(file の答え手)の StatPath — 置き場に無い path は台本に名(basename)が在れば実行できる file、無ければ無い物。
;;;                     実行の許しは台本に名が在ること。判断は本物と同じ executable-file-answer(dir は名が台本に在っても False)。
;;;   ReadEnvironment   ProcessScript の env から。
;;;   ReadInterpreter   ProcessScript の interpreter(agora-redesign #2347)。
;;;   ResolveModule     ProcessScript の modules の表から名で引く。表に無い名は ModuleNotFound(#2347)。
;;;   WorkingDirectory  聞かれるたびに新しい空の dir(<work-root>/job-<n>)を作って答える — worker が job ごとに空の作業 dir を作って子を
;;;                     起こすのの代役(同じ VM で task を走らせる模擬では task ごとに 1 回聞かれる)。
;;; 並び: file の答え手をこの handler より外側に置く。session の値の置き場(doeff_core_effects の state)はさらに外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import errno)
(import fnmatch)
(import posixpath)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory
                                            ProcessAlive StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted
                                            ProcessRunning ProcessExited ProcessNotChild
                                            ReadInterpreter ResolveModule InterpreterFacts ModuleFound ModuleNotFound
                                            not-started-outcome start-refusal executable-file-answer])
(import doeff_core_effects.file_effects [PathKind PathStat StatPath MakeDirectory AppendText FileFailed])


(defrecord ScriptedCommand
  "台本の命令 1 つ(name = argv[0] の basename・run = (run commands request) → ProcessOutcome を答える Program。commands は台本の表の全部 —
   timeout のように別の命令を走らせる台本が run-scripted で引く)。"
  (#^ str name)
  (#^ Callable run))


(defrecord ProcessScript
  "scripted-process-handler に渡す世界(commands = ScriptedCommand の tuple・env = 自分の process の環境変数・work-root = job ごとの作業 dir を
   作る親・alive = 生きている pid の表 — ProcessAlive の答え・interpreter = 自分の process の interpreter の事実 — ReadInterpreter の答え・
   modules = import が解く module の置き場の表 — ResolveModule の答え。表に無い名は ModuleNotFound)。alive の要素は pid(int)— 作る時に
   確かめる(要素の型が欄の型 frozenset[int] と違う値を黙って持たない)。"
  {:check [(all (gfor pid alive (isinstance pid int)))]}
  (#^ (get tuple #(ScriptedCommand ...)) commands)
  (setv #^ (get tuple #(EnvEntry ...)) env #())
  (setv #^ str work-root "/work/jobs")
  (setv #^ (get frozenset int) alive (frozenset))
  (setv #^ InterpreterFacts interpreter (InterpreterFacts :prefix "/" :pid 1))
  (setv #^ (get tuple #(ModuleFound ...)) modules #()))


(defk scripted-child-env [inherited env env-mode env-drop]
  {:pre [(: inherited tuple) (: env (| tuple None)) (: env-mode EnvMode) (: env-drop tuple)] :post [(: % (| tuple None))]}
  "台本に渡す子の環境を作るため: None と REPLACE は渡された env のまま(前からの振る舞い)、EXTEND は台本の世界の環境 inherited から env-drop の
   型に合う名を外して env を足した全部(同じ名は env が勝つ — 本物の subprocess-handler の child-environment と同じ規則)。"
  (match #(env env-mode)
    #(None _) None
    #(_ EnvMode.EXTEND) (do (val added (sfor e env e.name))
                            (+ (tuple (gfor e inherited :if (not (or (in e.name added)
                                                                     (any (gfor p env-drop (fnmatch.fnmatchcase e.name p)))))
                                            e))
                               env))
    _ env))


(defk run-scripted [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "命令 1 つを台本で走らせるため(無い cwd・名の無い命令は起こせない形)。台本から別の命令を走らせる時もこれを呼ぶ。
   本物の subprocess と同じ順で断る: 先に cwd(無ければ ENOENT・dir でなければ ENOTDIR)、次に命令(ENOENT)。"
  (when (is-not request.cwd None)
    (<- stat (StatPath request.cwd))
    (val refused-errno (match stat
                         (FileFailed) errno.ENOENT
                         (PathStat :kind PathKind.DIRECTORY) None
                         (PathStat :kind PathKind.MISSING) errno.ENOENT
                         _ errno.ENOTDIR))
    (when (is-not refused-errno None)
      (<- cwd-refusal str (start-refusal refused-errno request.cwd))
      (<- no-cwd ProcessOutcome (not-started-outcome cwd-refusal))
      (return no-cwd)))
  (val name (posixpath.basename (get request.argv 0)))
  (val found (lfor c commands :if (= c.name name) c))
  (when (not found)
    (<- command-refusal str (start-refusal errno.ENOENT (get request.argv 0)))
    (<- missing ProcessOutcome (not-started-outcome command-refusal))
    (return missing))
  (<- outcome ProcessOutcome ((. (get found 0) run) commands request))
  outcome)


;; 台本の世界で立てた子の pid の始まり(本物の pid と混ざらない大きさ)と、止めた子の終了 code(SIGTERM で終わった子 — 本物と同じ負の値)。
(val SCRIPTED-FIRST-PID 50000)
(val SCRIPTED-STOPPED-CODE -15)
;; 台本の世界で、止めるまで走り続ける子の印(立てた子の表の値 — 終わった子は終了 code)。
(val SCRIPTED-RUNNING "running")


(defk scripted-open-outputs [paths]
  {:pre [(: paths tuple)] :post [(: % (| ProcessNotStarted None))] :tags {:context "process" :role "program"}}
  "StartProcess の出力の file を、子を立てる前に末尾へ足す形で開く(空を足す)ため — 本物は Popen の前に開き、開けなければ立てない。
   答え = 開けなかった最初の file の理由(本物の OSError と同じ文)か None。"
  (var refused None)
  (for [path paths]
    (when (and (is refused None) (is-not path None))
      (<- appended (AppendText path ""))
      (when (isinstance appended FileFailed)
        (:= refused (ProcessNotStarted :detail appended.detail)))))
  refused)


(defk scripted-append-outputs [stdout-path stderr-path outcome]
  {:pre [(: stdout-path (| str None)) (: stderr-path (| str None)) (: outcome ProcessOutcome)] :post [(: % None)]
   :tags {:context "process" :role "program"}}
  "台本の子の標準出力と標準エラーを、それぞれの file の末尾へ足すため(None は捨てる — 本物の DEVNULL)。"
  (for [#(path text) #(#(stdout-path outcome.stdout) #(stderr-path outcome.stderr))]
    (when (and (is-not path None) text)
      (<- appended (AppendText path text))
      (when (isinstance appended FileFailed)
        (raise (OSError appended.detail)))))
  None)


(defk scripted-executable-at [commands path]
  {:pre [(: commands tuple) (: path str)] :post [(: % bool)] :tags {:context "process" :role "judgment"}}
  "ExecutableAt に台本の世界で答えるため: 種類は置き場(外側の file の答え手)の StatPath — 置き場に無い path は、台本に名(basename)が在れば
   実行できる file(台本がその命令の file の代役)、無ければ無い物。実行の許しは台本に名が在ること。判断は本物と同じ executable-file-answer。"
  (val named (any (gfor c commands (= c.name (posixpath.basename path)))))
  (<- seen (| PathStat FileFailed) (StatPath path))
  (val kind (match seen
              (PathStat :kind PathKind.MISSING) (if named PathKind.FILE PathKind.MISSING)
              (PathStat :kind found) found
              (FileFailed) PathKind.MISSING))
  (<- answer bool (executable-file-answer kind named))
  answer)


(defhandler scripted-process-handler [#^ ProcessScript script]
  ;; 引数に残す理由: 台本の表と環境は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  (session var jobs 0)
  ;; 立てたらすぐ返す子(StartProcess)の表(pid → 終了 code か SCRIPTED-RUNNING)と、次に配る pid。
  (session val started {})
  (session var next-pid SCRIPTED-FIRST-PID)
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    ;; 台本が見る env は子の環境変数の全部にそろえる(EXTEND は台本の世界の環境 script.env から env-drop を外して足す — 本物の subprocess-handler と同じ)。
    (<- child-env (| tuple None) (scripted-child-env script.env env env-mode env-drop))
    (<- outcome ProcessOutcome (run-scripted script.commands (RunProcess :argv argv :stdin stdin :timeout timeout :cwd cwd :env child-env
                                                                          :output-path output-path :process-group process-group
                                                                          :stop-grace stop-grace :stream-output stream-output)))
    ;; 足せない output-path は本物と同じく例外で上げる(本物は子の後の open が OSError を上げ、答えは返らない)。
    (when (is-not output-path None)
      (<- appended (AppendText output-path (+ outcome.stdout outcome.stderr)))
      (when (isinstance appended FileFailed)
        (raise (OSError appended.detail))))
    (resume outcome))
  (ExecutableAt [path]
    (<- found bool (scripted-executable-at script.commands path))
    (resume found))
  (ReadEnvironment [names]
    (resume (tuple (gfor name names e script.env :if (= e.name name) e))))
  (WorkingDirectory []
    (:= jobs (+ jobs 1))
    (val work (posixpath.join script.work-root (.format "job-{}" jobs)))
    (<- made (MakeDirectory work))
    (when (isinstance made FileFailed)
      (raise (RuntimeError (.format "作業 dir を作れない: {}" made.detail))))
    (resume work))
  (ProcessAlive [pid]
    (resume (and (> pid 0) (in pid script.alive))))
  (ReadInterpreter []
    (resume script.interpreter))
  (ResolveModule [name]
    (resume (next (gfor m script.modules :if (= m.name name) m) (ModuleNotFound :name name))))
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group]
    (<- refused (| ProcessNotStarted None) (scripted-open-outputs #(stdout-path stderr-path)))
    (if (is-not refused None)
        (resume refused)
        (do
          (<- child-env (| tuple None) (scripted-child-env script.env env env-mode env-drop))
          ;; 台本を timeout 0 で 1 回走らせる: 時間切れの答え = 止めるまで走り続ける子・それ以外 = すぐ終わった子。
          (<- outcome ProcessOutcome (run-scripted script.commands (RunProcess :argv argv :cwd cwd :env child-env :timeout 0.0
                                                                                :process-group process-group)))
          (if (not outcome.started)
              (resume (ProcessNotStarted :detail outcome.start-error))
              (do
                (<- (scripted-append-outputs stdout-path stderr-path outcome))
                (val pid next-pid)
                (:= next-pid (+ next-pid 1))
                (setv (get started pid) (if outcome.timed-out SCRIPTED-RUNNING outcome.exit-code))
                (resume (ProcessStarted :pid pid)))))))
  (PollProcess [pid]
    (match (.get started pid)
      None (resume (ProcessNotChild :pid pid))
      state :if (= state SCRIPTED-RUNNING) (resume (ProcessRunning :pid pid))
      code (do (del (get started pid))
               (resume (ProcessExited :pid pid :exit-code code)))))
  (StopProcess [pid stop-grace]
    (match (.get started pid)
      None (resume (ProcessNotChild :pid pid))
      state (do (del (get started pid))
                (resume (ProcessExited :pid pid :exit-code (if (= state SCRIPTED-RUNNING) SCRIPTED-STOPPED-CODE state)))))))
