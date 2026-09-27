;;; 汎用の子 process の effect(process_effects.hy)の I/O なしの答え手 scripted-process-handler(agora-redesign #802 便 1)。本物の子 process を
;;; 起こさず、argv[0] の名(basename)ごとの台本(ScriptedCommand)で答える。台本は Program で、file system の effect(file_effects.hy)で置き場を
;;; 読み書きしてよい(外側の file の答え手 — 多くは memory-file-handler — が受ける)。業務を知らない: 台本の中身は呼び手が渡す。
;;;
;;;   RunProcess        env-mode EXTEND は ProcessScript の env を継いで足した全部を台本に渡す。名の無い命令・無い cwd は started False(exit-code 127)— 本物の subprocess と同じ所で起こせない。output-path は
;;;                     台本の出力をその file の末尾へ足す。timeout は台本に任せる(台本が timed-out の答えを返してよい)。
;;;   ExecutableAt      argv[0] の名が台本に在れば True。
;;;   ReadEnvironment   ProcessScript の env から。
;;;   WorkingDirectory  聞かれるたびに新しい空の dir(<work-root>/job-<n>)を作って答える — worker が job ごとに空の作業 dir を作って子を
;;;                     起こすのの代役(同じ VM で task を走らせる模擬では task ごとに 1 回聞かれる)。
;;; 並び: file の答え手をこの handler より外側に置く。session の値の置き場(doeff_core_effects の state)はさらに外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import fnmatch)
(import posixpath)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory])
(import doeff_core_effects.file_effects [PathKind PathStat StatPath MakeDirectory AppendText FileFailed])

(val NOT-STARTED-CODE 127)


(defrecord ScriptedCommand
  "台本の命令 1 つ(name = argv[0] の basename・run = (run commands request) → ProcessOutcome を答える Program。commands は台本の表の全部 —
   timeout のように別の命令を走らせる台本が run-scripted で引く)。"
  (#^ str name)
  (#^ Callable run))


(defrecord ProcessScript
  "scripted-process-handler に渡す世界(commands = ScriptedCommand の tuple・env = 自分の process の環境変数・work-root = job ごとの作業 dir を
   作る親)。"
  (#^ (get tuple #(ScriptedCommand ...)) commands)
  (setv #^ (get tuple #(EnvEntry ...)) env #())
  (setv #^ str work-root "/work/jobs"))


(defk not-started [detail]
  {:pre [(: detail str)] :post [(: % ProcessOutcome)]}
  "起こせない形の答えを作るため(本物の subprocess-handler と同じ exit-code)。"
  (ProcessOutcome :exit-code NOT-STARTED-CODE :stdout "" :stderr "" :started False :start-error detail))


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
  "命令 1 つを台本で走らせるため(名の無い命令・無い cwd は起こせない形)。台本から別の命令を走らせる時もこれを呼ぶ。"
  (val name (posixpath.basename (get request.argv 0)))
  (val found (lfor c commands :if (= c.name name) c))
  (when (not found)
    (<- missing ProcessOutcome (not-started (.format "[Errno 2] No such file or directory: {!r}" (get request.argv 0))))
    (return missing))
  (when (is-not request.cwd None)
    (<- stat (StatPath request.cwd))
    (when (or (isinstance stat FileFailed) (!= stat.kind PathKind.DIRECTORY))
      (<- no-cwd ProcessOutcome (not-started (.format "[Errno 2] No such file or directory: {!r}" request.cwd)))
      (return no-cwd)))
  (<- outcome ProcessOutcome ((. (get found 0) run) commands request))
  outcome)


(defhandler scripted-process-handler [#^ ProcessScript script]
  ;; 引数に残す理由: 台本の表と環境は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  (session var jobs 0)
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop]
    ;; 台本が見る env は子の環境変数の全部にそろえる(EXTEND は台本の世界の環境 script.env から env-drop を外して足す — 本物の subprocess-handler と同じ)。
    (<- child-env (| tuple None) (scripted-child-env script.env env env-mode env-drop))
    (<- outcome ProcessOutcome (run-scripted script.commands (RunProcess :argv argv :stdin stdin :timeout timeout :cwd cwd :env child-env
                                                                          :output-path output-path)))
    (when (is-not output-path None)
      (<- (AppendText output-path (+ outcome.stdout outcome.stderr))))
    (resume outcome))
  (ExecutableAt [path]
    (resume (any (gfor c script.commands (= c.name (posixpath.basename path))))))
  (ReadEnvironment [names]
    (resume (tuple (gfor name names e script.env :if (= e.name name) e))))
  (WorkingDirectory []
    (:= jobs (+ jobs 1))
    (val work (posixpath.join script.work-root (.format "job-{}" jobs)))
    (<- made (MakeDirectory work))
    (when (isinstance made FileFailed)
      (raise (RuntimeError (.format "作業 dir を作れない: {}" made.detail))))
    (resume work)))
