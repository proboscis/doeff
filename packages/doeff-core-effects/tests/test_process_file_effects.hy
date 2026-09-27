;;; 汎用の子 process と file system の effect(process_effects.hy・file_effects.hy — agora-redesign #802 便 1)の検。
;;;   - 本物の答え手(subprocess-handler・os-file-handler)は実 process と一時 dir で、値の詰め替え(生の returncode・時間切れ・起こせない形・
;;;     出力の追記・種類・mode・symlink を保つ写し)を確かめる。
;;;   - 同じ筋書きの Program を本物(一時 dir)と I/O なし(memory-file-handler)の両方で走らせ、答えが同じになることを確かめる(同じ所で断る)。
;;;   - I/O なしの子 process(scripted-process-handler)は、台本・起こせない形・作業 dir・出力の追記を確かめる。
;;;   - doeff-agents の io_effects は同じ型を re-export する(定義は 1 つ)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(import os)
(import stat)
(import tempfile)
(import dataclasses [dataclass])
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.process_effects [EnvEntry ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath
                                         ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree CopyFile
                                         CopyTree RenamePath RemoveTree AcquireLock ReleaseLock])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler run-scripted])


(defn on [handlers program]
  "handler の列の下で program を 1 回回すため。"
  (run (scheduled (with_handlers handlers program))))


;; --- 本物の子 process -----------------------------------------------------------------------------------------------------

(defn test-subprocess-keeps-the-raw-returncode-and-gives-failures-as-values []
  (with [root (tempfile.TemporaryDirectory)]
    (setv log (os.path.join root "out.log"))
    (setv done (on [subprocess-handler] (RunProcess :argv #("/bin/sh" "-c" "echo \"$GIVEN $(pwd)\"; echo err >&2; exit 3") :cwd root
                                                    :env #((EnvEntry :name "GIVEN" :value "渡した")) :output-path log)))
    (assert (= done (ProcessOutcome :exit-code 3 :stdout (.format "渡した {}\n" (os.path.realpath root)) :stderr "err\n")) done)
    (with [f (open log :encoding "utf-8")] (assert (= (.read f) (+ done.stdout done.stderr))))
    ;; signal で終わった子の returncode は負のまま(丸めない)。
    (setv killed (on [subprocess-handler] (RunProcess :argv #("/bin/sh" "-c" "kill -9 $$"))))
    (assert (= killed.exit-code -9) killed)
    (setv slow (on [subprocess-handler] (RunProcess :argv #("sleep" "5") :timeout 0.2)))
    (assert (and slow.timed-out slow.started (= slow.exit-code 124)) slow)
    (setv missing (on [subprocess-handler] (RunProcess :argv #("/nonexistent/doeff-command"))))
    (assert (and (not missing.started) (= missing.exit-code 127) (in "No such file" missing.start-error)) missing)
    (assert (on [subprocess-handler] (ExecutableAt :path "/bin/sh")))
    (assert (not (on [subprocess-handler] (ExecutableAt :path "/nonexistent/doeff-command"))))))


(defn test-subprocess-reads-the-own-environment-and-working-directory []
  (setv (get os.environ "DOEFF_PROCESS_PRESENT") "在る")
  (try
    (assert (= (on [subprocess-handler] (ReadEnvironment #("DOEFF_PROCESS_MISSING" "DOEFF_PROCESS_PRESENT")))
               #((EnvEntry :name "DOEFF_PROCESS_PRESENT" :value "在る"))))
    (finally (del (get os.environ "DOEFF_PROCESS_PRESENT"))))
  (assert (= (on [subprocess-handler] (WorkingDirectory)) (os.getcwd))))


(defn test-doeff-agents-re-exports-the-same-process-types []
  (import doeff_agents.io_effects :as agents)
  (assert (is agents.RunProcess RunProcess))
  (assert (is agents.ProcessOutcome ProcessOutcome))
  (assert (is agents.ExecutableAt ExecutableAt)))


;; --- file system: 本物と I/O なしで同じ答え -------------------------------------------------------------------------------

(defrecord Journey
  "筋書きの答え(path は root からの相対にそろえる — 本物と I/O なしで比べるため)。"
  (#^ tuple answers))


(defk relative [root answer]
  {:pre [(: root str) (: answer (| FileFailed PathStat LockHeld str bytes tuple None))] :post [(: % (| FileFailed LockHeld str bytes tuple None))]}
  "答えの中の root を消して比べられる形にするため(mtime と実の path は本物だけが持つので落とす)。"
  (match answer
    (FileFailed :path path :detail detail) (FileFailed :path (.replace path root "<root>") :detail (.replace detail root "<root>"))
    (PathStat :kind kind :size size) #("stat" kind (if (= kind PathKind.FILE) size 0))  ; dir の大きさは file system ごとに違う
    (LockHeld :path path) #("lock" (.replace path root "<root>"))
    _ answer))


(defk journey [root]
  {:pre [(: root str)] :post [(: % Journey)]}
  "file system の筋書き 1 つ(成功と、本物の file system が断る所)を走らせるため。"
  (val steps [(MakeDirectory (+ root "/a/b") :mode 0o700)
              (WriteText (+ root "/a/b/config") "中身" :mode 0o600)
              (WriteText (+ root "/a/b/config") "置き換えた" :replace True)
              (AppendText (+ root "/a/log") "1行目\n")
              (AppendText (+ root "/a/log") "2行目\n")
              (WriteBytes (+ root "/a/raw") b"\xff\x00")
              (ReadText (+ root "/a/b/config"))
              (ReadText (+ root "/a/log"))
              (ReadBytes (+ root "/a/raw"))
              (StatPath (+ root "/a/b/config"))
              (StatPath (+ root "/a/b"))
              (StatPath (+ root "/a/none"))
              (CopyFile (+ root "/a/b/config") (+ root "/a/copy"))
              (MakeDirectory (+ root "/t/b"))
              (WriteText (+ root "/t/keep") "残す")
              (CopyTree (+ root "/a") (+ root "/t"))
              (ListDirectory (+ root "/t"))
              (WalkTree (+ root "/t"))
              (RenamePath (+ root "/a/copy") (+ root "/a/renamed"))
              (ListDirectory (+ root "/a"))
              (RemoveTree (+ root "/t/b"))
              (ListDirectory (+ root "/t"))
              (AcquireLock (+ root "/a/lock"))
              ;; 本物の file system が断る所。
              (WriteText (+ root "/none/x") "親が無い")
              (WriteText (+ root "/a/b") "dir へ書く")
              (ReadText (+ root "/a/b"))
              (ReadText (+ root "/a/none"))
              (MakeDirectory (+ root "/a/log/sub"))
              (ListDirectory (+ root "/a/log"))
              (RenamePath (+ root "/a/renamed") (+ root "/a/b"))
              (RenamePath (+ root "/a") (+ root "/t"))
              (RemoveTree (+ root "/a/none"))])
  (val answers [])
  (for [step steps]
    (<- answer step)
    (<- shown (relative root answer))
    (.append answers shown))
  (Journey :answers (tuple answers)))


(defn test-the-real-and-memory-file-systems-answer-the-same-journey []
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (journey root)))
    (assert (= (stat.S_IMODE (. (os.stat (+ root "/a/b")) st_mode)) 0o700))
    (assert (= (stat.S_IMODE (. (os.stat (+ root "/a/b/config")) st_mode)) 0o600)))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/memory-root")))] (journey "/memory-root")))
  (for [#(i #(a b)) (enumerate (zip real.answers memory.answers))]
    (assert (= a b) #(i a b)))
  ;; 成功の筋の中身(本物の答えで確かめる — memory も同じ答え)。
  (setv answers real.answers)
  (assert (= (cut answers 6 9) #("置き換えた" "1行目\n2行目\n" b"\xff\x00")) answers)
  (assert (= (get answers 9) #("stat" PathKind.FILE (len (.encode "置き換えた" "utf-8")))) answers)
  (assert (= (get answers 11) #("stat" PathKind.MISSING 0)) answers)
  (assert (= (lfor e (get answers 16) e.name) ["b" "copy" "keep" "log" "raw"]) answers)
  (assert (= (lfor e (get answers 17) e.name) ["b" "b/config" "copy" "keep" "log" "raw"]) answers)
  (assert (= (lfor e (get answers 19) e.name) ["b" "log" "raw" "renamed"]) answers)
  (assert (= (lfor e (get answers 21) e.name) ["copy" "keep" "log" "raw"]) answers)
  ;; 断りは全部 FileFailed(例外にしない)。
  (assert (all (gfor a (cut answers 23 None) (isinstance a FileFailed))) (cut answers 23 None)))


(defn test-copy-tree-keeps-symlinks-on-the-real-file-system []
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (os.makedirs (+ root "/s"))
    (with [f (open (+ root "/s/file") "w")] (.write f "x"))
    (os.symlink "file" (+ root "/s/link"))
    (assert (is (on [os-file-handler] (CopyTree (+ root "/s") (+ root "/d"))) None))
    (assert (= (os.readlink (+ root "/d/link")) "file"))
    (assert (= (on [os-file-handler] (ListDirectory (+ root "/d")))
               #((DirEntry :name "file" :kind PathKind.FILE) (DirEntry :name "link" :kind PathKind.SYMLINK))))
    (setv held (on [os-file-handler] (AcquireLock (+ root "/lock"))))
    (assert (is (on [os-file-handler] (ReleaseLock held)) None))))


(defn test-stat-without-following-symlinks-sees-a-broken-link []
  ;; 壊れた symlink: 辿らなければ SYMLINK・辿れば MISSING(#805 の消費者の頼み)。
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv link (os.path.join (os.path.realpath tmp) "broken"))
    (os.symlink "nowhere" link)
    (assert (= (. (on [os-file-handler] (StatPath link :follow-symlinks False)) kind) PathKind.SYMLINK))
    (assert (= (. (on [os-file-handler] (StatPath link)) kind) PathKind.MISSING))))


(defn test-memory-files-can-be-read-back-and-locks-are-exclusive []
  (defk twice []
    {:pre [] :post [(: % tuple)]}
    (<- first (AcquireLock "/r/lock"))
    (<- second (AcquireLock "/r/lock"))
    (<- (ReleaseLock first))
    (<- third (AcquireLock "/r/lock"))
    (<- seen MemoryFiles (ReadMemoryFiles))
    #(first second third seen))
  (setv #(first second third seen) (on [(state) (memory-file-handler (MemoryFiles :dirs #("/r")))] (twice)))
  (assert (isinstance first LockHeld) first)
  (assert (and (isinstance second FileFailed) (in "Resource temporarily unavailable" second.detail)) second)
  (assert (isinstance third LockHeld) third)
  (assert (= (lfor f seen.files f.path) ["/r/lock"]) seen))


;; --- I/O なしの子 process ----------------------------------------------------------------------------------------------------

(defk echo-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "台本の命令 echo: 引数を書いた file を cwd に置き、引数を出力する。"
  (<- (WriteText (+ request.cwd "/echoed") (.join " " (cut request.argv 1 None))))
  (ProcessOutcome :exit-code 0 :stdout (+ (.join " " (cut request.argv 1 None)) "\n") :stderr ""))


(defk wrap-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "台本の命令 wrap: 残りの argv を台本の別の命令として走らせる(run-scripted の使い方)。"
  (<- inner ProcessOutcome (run-scripted commands (RunProcess :argv (cut request.argv 1 None) :cwd request.cwd :env request.env)))
  inner)


(defk scripted-journey []
  {:pre [] :post [(: % tuple)]}
  "I/O なしの子 process の筋書き 1 つを走らせるため。"
  (<- work (WorkingDirectory))
  (<- again (WorkingDirectory))
  (<- ran (RunProcess :argv #("/usr/bin/wrap" "echo" "a" "b") :cwd work :output-path (+ work "/out.log")))
  (<- unknown (RunProcess :argv #("nope") :cwd work))
  (<- no-cwd (RunProcess :argv #("echo") :cwd "/nowhere"))
  (<- env (ReadEnvironment #("A" "B")))
  (<- runnable (ExecutableAt :path "/bin/echo"))
  (<- echoed (ReadText (+ work "/echoed")))
  (<- logged (ReadText (+ work "/out.log")))
  #(work again ran unknown no-cwd env runnable echoed logged))


(defn test-scripted-processes-run-the-script-on-the-file-handler []
  (setv script (ProcessScript :commands #((ScriptedCommand :name "echo" :run echo-script) (ScriptedCommand :name "wrap" :run wrap-script))
                              :env #((EnvEntry :name "A" :value "1")) :work-root "/work/jobs"))
  (setv #(work again ran unknown no-cwd env runnable echoed logged)
        (on [(state) (memory-file-handler (MemoryFiles)) (scripted-process-handler script)] (scripted-journey)))
  (assert (= #(work again) #("/work/jobs/job-1" "/work/jobs/job-2")))
  (assert (= ran (ProcessOutcome :exit-code 0 :stdout "a b\n" :stderr "")) ran)
  (assert (and (not unknown.started) (= unknown.exit-code 127)) unknown)
  (assert (and (not no-cwd.started) (in "/nowhere" no-cwd.start-error)) no-cwd)
  (assert (= env #((EnvEntry :name "A" :value "1"))))
  (assert runnable)
  (assert (= #(echoed logged) #("a b" "a b\n"))))
