;;; Tests of the general child process and file system effects (process_effects.hy, file_effects.hy).
;;;   - The properties where the real child process (subprocess-handler) and the no-I/O one (scripted-process-handler) give the same answer (returncode, output, timeout, the form that cannot be started,
;;;     environment, cwd, stdin, appending output) are in the contract test test_process_contract.hy. Here are the properties of only one side (the real WorkingDirectory and ReadMachineName,
;;;     the form where ReadMachineName with no answerer fails, the machine name of the script, the form where the script receives env None,
;;;     the per-job working dir, running a script from a script).
;;;   - The real file answerer (os-file-handler) checks the repacking of values (kind, mode, copy that preserves symlinks) in a temporary dir.
;;;   - A Program of the same scenario is run with both the real one (temporary dir) and the no-I/O one (memory-file-handler), and the answers are checked to be the same (they refuse in the same places).
;;;   The test that io_effects of doeff-agents re-exports the same types (one definition) is placed on the doeff-agents side
;;;   (packages/doeff-agents/tests/test_io_effects_reexports.hy) -- what is checked is a property of doeff-agents, and if a test of this package imports the
;;;   package above, the layer direction is reversed.
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import os)
(import socket)
(import pytest)
(import stat)
(import tempfile)
(import dataclasses [dataclass])
(import typing [TypeVar])
(import doeff [run with_handlers Program])
(import doeff [UnhandledEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled Spawn Wait])
(import doeff_core_effects [EnvEntry ProcessOutcome RunProcess WorkingDirectory ReadMachineName ReadEnvironment])
(import doeff_core_effects [PathKind FileFailed PathStat DirEntry LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath ReadDiskFree ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree StatTree CopyFile CopyTree RenamePath RemoveTree AcquireLock ReleaseLock DiskUsage ReadDiskUsage MeasureTree LinkFile CompilePythonSources MakeSymlink])
(import doeff_core_effects._python_bytecode [pyc-path])
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [ScriptedCommand ProcessScript scripted-process-handler scripted-command-peer run-scripted]
        doeff_core_effects._scripted_process [SCRIPTED-COMMAND-EFFECTS])


;; The type of the answer of the program that on runs (differs per scenario).
(val T (TypeVar "T"))


(defn #^ T on [#^ list handlers #^ (get Program T) program]
  "To run program once under a stack of handlers."
  (run (scheduled (with_handlers handlers program))))


;; --- Child process: properties of only the real one or only the fake (the properties where both give the same answer are the contract tests of test_process_contract.hy) --------------------

(deftest test-subprocess-answers-the-own-working-directory
  ;; The real WorkingDirectory is the working dir of its own process (the fake gives a new dir per job -- the script scenario below).
  (<- here str (with_handlers [subprocess-handler] (WorkingDirectory)))
  (assert (= here (os.getcwd)) here))


(deftest test-subprocess-answers-the-machine-name-from-the-os
  ;; The real ReadMachineName is the same value as socket.gethostname.
  (<- name str (with_handlers [subprocess-handler] (ReadMachineName)))
  (assert (= name (socket.gethostname)) name))


(deftest test-scripted-process-answers-the-machine-name-of-the-script
  ;; The script's ReadMachineName is the machine-name of ProcessScript (the default is "scripted-machine").
  (<- given str (with_handlers [(scripted-process-handler (ProcessScript :commands #() :machine-name "sim-host"))] (ReadMachineName)))
  (<- default str (with_handlers [(scripted-process-handler (ProcessScript :commands #()))] (ReadMachineName)))
  (assert (= #(given default) #("sim-host" "scripted-machine")) #(given default)))


(defk read-machine-name-alone []
  {:pre [] :post [(: % str)] :tags {:context "process-test" :role "program"}}
  "A Program that issues ReadMachineName without stacking an answerer (the scenario of the counterexample)."
  (<- name str (ReadMachineName))
  name)


(deftest test-counterexample-machine-name-without-a-handler-is-unhandled
  ;; In a run with no answerer, ReadMachineName fails as unhandled (there is no place that silently answers with a default name).
  (with [(pytest.raises UnhandledEffect :match "ReadMachineName")]
    (run (read-machine-name-alone))))


(defk shown-env [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The script answers by listing the env it received as name=value (None is written as None)."
  (ProcessOutcome :exit-code 0 :stderr ""
                  :stdout (match request.env
                            None "None"
                            given (.join "," (gfor e given (.format "{}={}" e.name e.value))))))


(deftest test-scripted-process-gives-the-script-none-for-an-inherited-environment
  ;; env None (inherit the caller's environment) is passed to the script as None -- reading the inherited contents is the script's side (the real child inherits os.environ).
  ;; The rule of passing everything inherited and added by EXTEND is the contract test.
  (val script (ProcessScript :commands #((ScriptedCommand :name "show" :run shown-env))
                             :env #((EnvEntry :name "DOEFF_INHERITED" :value "inherited"))))
  (<- shown ProcessOutcome (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-process-handler script)]
                             (RunProcess :argv #("show"))))
  (assert (= shown.stdout "None") shown))


(defk outer-show [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of the outer script's command (answers every call with the mark outer -- to tell whether the peer for only the named commands answered or the outer did)."
  (ProcessOutcome :exit-code 0 :stdout "outer" :stderr ""))


(defk peer-journey []
  {:pre [] :post [(: % tuple)] :tags {:context "process-test" :role "program"}}
  "A scenario issuing one call of the named command (argv[0] with a path), one call of an unnamed command, and one environment read (the test of scripted-command-peer)."
  (<- named ProcessOutcome (RunProcess :argv #("/usr/bin/show" "x")))
  (<- other ProcessOutcome (RunProcess :argv #("other")))
  (<- env tuple (ReadEnvironment :names #("DOEFF_OUTER") :prefixes #()))
  #(named.stdout other.stdout env))


(deftest test-the-scripted-command-peer-answers-only-its-commands-and-passes-the-rest
  ;; scripted-command-peer answers by script only for commands whose name is in the script (basename of argv[0]) (env stays as requested -- the mark None for inheriting), and passes calls of other commands
  ;; and environment reads to the outer answerer (even stacked inside the outer environment answerer, it does not hide that answer).
  ;; Failure case: without the peer, the named command is also answered by the outer (mark outer).
  (val outer (scripted-process-handler (ProcessScript :commands #((ScriptedCommand :name "show" :run outer-show)
                                                                  (ScriptedCommand :name "other" :run outer-show))
                                                      :env #((EnvEntry :name "DOEFF_OUTER" :value "outside")))))
  (val peer (scripted-command-peer #((ScriptedCommand :name "show" :run shown-env))))
  (val outside #((EnvEntry :name "DOEFF_OUTER" :value "outside")))
  (<- layered tuple (with_handlers [(state) (memory-file-handler (MemoryFiles)) outer peer] (peer-journey)))
  (assert (= layered #("None" "outer" outside)) layered)
  (<- bare tuple (with_handlers [(state) (memory-file-handler (MemoryFiles)) outer] (peer-journey)))
  (assert (= bare #("outer" "outer" outside)) bare)
  ;; The list of types the peer answers (read by the user's fence) is exactly the RunProcess the peer answers.
  (assert (= SCRIPTED-COMMAND-EFFECTS #(RunProcess)) SCRIPTED-COMMAND-EFFECTS))


;; --- file system: the same answers with the real one and no I/O -------------------------------------------------------------------------------

(defrecord Journey
  "The answers of the scenario (path is made relative to root -- to compare the real one and no I/O)."
  (#^ tuple answers))


(defk relative [root answer]
  {:pre [(: root str) (: answer (| FileFailed PathStat LockHeld str bytes tuple None))] :post [(: % (| FileFailed LockHeld str bytes tuple None))]}
  "To make the answers comparable by removing root from them (mtime and the real path are dropped because only the real one has them)."
  (match answer
    (FileFailed :path path :detail detail) (FileFailed :path (.replace path root "<root>") :detail (.replace detail root "<root>"))
    (PathStat :kind kind :size size) #("stat" kind (if (= kind PathKind.FILE) size 0))  ; the size of a dir differs per file system
    (LockHeld :path path) #("lock" (.replace path root "<root>"))
    _ answer))


(defk journey [root]
  {:pre [(: root str)] :post [(: % Journey)]}
  "To run one file system scenario (successes, and the places where the real file system refuses)."
  (val steps [(MakeDirectory (+ root "/a/b") :mode 0o700)
              (WriteText (+ root "/a/b/config") "contents" :mode 0o600)
              (WriteText (+ root "/a/b/config") "replaced" :replace True)
              (AppendText (+ root "/a/log") "line 1\n")
              (AppendText (+ root "/a/log") "line 2\n")
              (WriteBytes (+ root "/a/raw") b"\xff\x00")
              (ReadText (+ root "/a/b/config"))
              (ReadText (+ root "/a/log"))
              (ReadBytes (+ root "/a/raw"))
              (StatPath (+ root "/a/b/config"))
              (StatPath (+ root "/a/b"))
              (StatPath (+ root "/a/none"))
              (CopyFile (+ root "/a/b/config") (+ root "/a/copy"))
              (MakeDirectory (+ root "/t/b"))
              (WriteText (+ root "/t/keep") "keep")
              (CopyTree (+ root "/a") (+ root "/t"))
              (ListDirectory (+ root "/t"))
              (WalkTree (+ root "/t"))
              (RenamePath (+ root "/a/copy") (+ root "/a/renamed"))
              (ListDirectory (+ root "/a"))
              (RemoveTree (+ root "/t/b"))
              (ListDirectory (+ root "/t"))
              ;; A write that flushes to disk before answering (sync) and a read of only the head (limit) -- the answers have the same form as the write without flush and the read of everything.
              (AppendText (+ root "/a/log") "line 3\n" :sync True)
              (WriteBytes (+ root "/a/synced") b"\x01\x02\x03" :replace True :sync True)
              (WriteText (+ root "/a/b/config") "flushed" :sync True)
              (ReadBytes (+ root "/a/synced") :limit 2)
              (ReadBytes (+ root "/a/synced") :limit 10)
              (ReadText (+ root "/a/log"))
              (AcquireLock (+ root "/a/lock"))
              ;; Places where the real file system refuses.
              (WriteText (+ root "/none/x") "no parent")
              (WriteText (+ root "/a/b") "write to a dir")
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


(defn #^ None test-the-real-and-memory-file-systems-answer-the-same-journey []
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (journey root)))
    (assert (= (stat.S_IMODE (. (os.stat (+ root "/a/b")) st_mode)) 0o700))
    (assert (= (stat.S_IMODE (. (os.stat (+ root "/a/b/config")) st_mode)) 0o600))
    ;; Free space (#831): a nonexistent path is measured at the file system of the existing parent (the answer is a free-space number, not a FileFailed refusal).
    (setv free (on [os-file-handler] (ReadDiskFree (+ root "/missing/deeper"))))
    (assert (and (isinstance free int) (> free 0)) free))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/memory-root")))] (journey "/memory-root")))
  (for [#(i #(a b)) (enumerate (zip real.answers memory.answers))]
    (assert (= a b) #(i a b)))
  ;; The contents of the success scenario (checked with the real answers -- memory gives the same answers).
  (setv answers real.answers)
  (assert (= (cut answers 6 9) #("replaced" "line 1\nline 2\n" b"\xff\x00")) answers)
  (assert (= (get answers 9) #("stat" PathKind.FILE (len (.encode "replaced" "utf-8")))) answers)
  (assert (= (get answers 11) #("stat" PathKind.MISSING 0)) answers)
  (assert (= (lfor e (get answers 16) e.name) ["b" "copy" "keep" "log" "raw"]) answers)
  (assert (= (lfor e (get answers 17) e.name) ["b" "b/config" "copy" "keep" "log" "raw"]) answers)
  (assert (= (lfor e (get answers 19) e.name) ["b" "log" "raw" "renamed"]) answers)
  (assert (= (lfor e (get answers 21) e.name) ["copy" "keep" "log" "raw"]) answers)
  ;; The refusals are all FileFailed (not exceptions).
  (assert (= (cut answers 22 28) #(None None None b"\x01\x02" b"\x01\x02\x03" "line 1\nline 2\nline 3\n")) answers)
  (assert (all (gfor a (cut answers 29 None) (isinstance a FileFailed))) (cut answers 29 None)))


(defk appended-reads [root]
  {:pre [(: root str)] :post [(: % Journey)]}
  "A scenario that reads a file being appended to only from where it was read before (offset of ReadBytes -- for a reader that waits for appends and copies the tail)."
  (val log (+ root "/s/out.log"))
  (<- made (MakeDirectory (+ root "/s")))
  (<- written (WriteBytes log b"abcdef"))
  (<- after-two (ReadBytes log :offset 2))
  (<- three-after-two (ReadBytes log :offset 2 :limit 3))
  (<- at-the-end (ReadBytes log :offset 6))
  (<- past-the-end (ReadBytes log :offset 99))
  (<- appended (AppendText log "gh"))
  (<- the-appended (ReadBytes log :offset 6))
  (<- missing (ReadBytes (+ root "/s/none.log") :offset 2))
  (Journey :answers #(made written after-two three-after-two at-the-end past-the-end appended the-appended missing)))


(defn #^ None test-a-read-from-an-offset-takes-only-the-bytes-after-it []
  ;; Read only from position offset onward (limit is the number of bytes after that). A position past the end of the file is empty bytes (not a failure); a nonexistent file is FileFailed.
  ;; The real and memory answerers give the same answer. Failure case (the former form): ReadBytes had no offset field, and building the scenario raised TypeError.
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (appended-reads root))))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/memory-root")))] (appended-reads "/memory-root")))
  (assert (= (cut real.answers 0 8) #(None None b"cdef" b"cde" b"" b"" None b"gh")) real.answers)
  (assert (isinstance (get real.answers 8) FileFailed) real.answers)
  (assert (= (cut memory.answers 0 8) (cut real.answers 0 8)) #(memory.answers real.answers))
  (assert (isinstance (get memory.answers 8) FileFailed) memory.answers))


(defn #^ None test-copy-tree-keeps-symlinks-on-the-real-file-system []
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
    (assert (isinstance held LockHeld) held)
    (assert (is (on [os-file-handler] (ReleaseLock held)) None))))


(defn #^ None test-stat-without-following-symlinks-sees-a-broken-link []
  ;; A broken symlink: SYMLINK if not followed, MISSING if followed (a request from the consumer of #805).
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv link (os.path.join (os.path.realpath tmp) "broken"))
    (os.symlink "nowhere" link)
    (setv unfollowed (on [os-file-handler] (StatPath link :follow-symlinks False)))
    (assert (and (isinstance unfollowed PathStat) (= unfollowed.kind PathKind.SYMLINK)) unfollowed)
    (setv followed (on [os-file-handler] (StatPath link)))
    (assert (and (isinstance followed PathStat) (= followed.kind PathKind.MISSING)) followed)))


(defn #^ None test-memory-files-can-be-read-back-and-locks-are-exclusive []
  (setv log [])
  (defk contender []
    {:pre [] :post [(: % None)] :tags {:context "file-system" :role "program"}}
    "The 2nd task: waits for the lock being held, and when it gets it, records it and releases it."
    (<- held LockHeld (AcquireLock "/r/lock"))
    (.append log "B got")
    (<- (ReleaseLock held))
    None)
  (defk twice []
    {:pre [] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
    "A scenario that wakes the 2nd task while holding the lock, and checks that the 2nd cannot take it until released and can take it after release."
    (<- first (AcquireLock "/r/lock"))
    (<- task (Spawn (contender)))
    (.append log "A release")
    (<- (ReleaseLock first))
    (<- (Wait task))
    (<- third (AcquireLock "/r/lock"))
    (<- (ReleaseLock third))
    (<- seen MemoryFiles (ReadMemoryFiles))
    (<- free int (ReadDiskFree "/r/missing"))
    #(first third seen free))
  (setv #(first third seen free) (on [(state) (memory-file-handler (MemoryFiles :dirs #("/r") :free 1234))] (twice)))
  ;; Free space (#831) is answered with the value of the store setting, and is kept even if the store is rebuilt by lock or write.
  (assert (= free 1234) free)
  (assert (= seen.free 1234) seen)
  (assert (isinstance first LockHeld) first)
  ;; The lock waits until it can be taken, like the real flock (#835): the 2nd can be taken only after the 1st releases it (it does not refuse without waiting).
  (assert (= log ["A release" "B got"]) log)
  (assert (isinstance third LockHeld) third)
  (assert (= seen.locks #()) seen)
  (assert (= (lfor f seen.files f.path) ["/r/lock"]) seen))


;; --- No-I/O child process ----------------------------------------------------------------------------------------------------

(defk echo-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "Script command echo: places a file that wrote the arguments in cwd, and outputs the arguments."
  (<- (WriteText (+ request.cwd "/echoed") (.join " " (cut request.argv 1 None))))
  (ProcessOutcome :exit-code 0 :stdout (+ (.join " " (cut request.argv 1 None)) "\n") :stderr ""))


(defk wrap-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "Script command wrap: runs the remaining argv as another command of the script (how to use run-scripted)."
  (<- inner ProcessOutcome (run-scripted commands (RunProcess :argv (cut request.argv 1 None) :cwd request.cwd :env request.env)))
  inner)


(defk scripted-journey []
  {:pre [] :post [(: % tuple)] :tags {:context "process-test" :role "program"}}
  "One scenario of the no-I/O child process (asks for the job dir twice, runs echo from wrap, and reads the file echo placed)."
  (<- work str (WorkingDirectory))
  (<- again str (WorkingDirectory))
  (<- ran ProcessOutcome (RunProcess :argv #("/usr/bin/wrap" "echo" "a" "b") :cwd work))
  (<- echoed str (ReadText (+ work "/echoed")))
  #(work again ran echoed))


(deftest test-scripted-processes-run-the-script-on-the-file-handler
;; Properties of only the fake: WorkingDirectory is a new job dir every time it is asked; the script writes the store with file effects; running a script from a script
;; with run-scripted. The form that cannot be started, environment, ExecutableAt, and appending to output-path are in the contract test (test_process_contract.hy).
  (val script (ProcessScript :commands #((ScriptedCommand :name "echo" :run echo-script) (ScriptedCommand :name "wrap" :run wrap-script))
                             :work-root "/work/jobs"))
  (<- answers tuple (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-process-handler script)] (scripted-journey)))
  (assert (= answers #("/work/jobs/job-1" "/work/jobs/job-2" (ProcessOutcome :exit-code 0 :stdout "a b\n" :stderr "") "a b")) answers))


(defk tree-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "A scenario that builds a tree and measures the total size (everything under a dir, only the lower dir, the refusal of measuring a file)."
  (<- (MakeDirectory (+ root "/t/sub")))
  (<- (WriteText (+ root "/t/a") "αβ"))
  (<- (WriteText (+ root "/t/sub/b") "xyz"))
  (<- whole (MeasureTree (+ root "/t")))
  (<- below (MeasureTree (+ root "/t/sub")))
  (<- refused (MeasureTree (+ root "/t/a")))
  #(whole below (if (isinstance refused FileFailed) (FileFailed :path (.replace refused.path root "") :detail "") refused)))


(defn #^ None test-disk-usage-and-tree-size-answer-the-same-on-the-real-and-memory-file-systems []
  ;; #2504: ReadDiskUsage (total and free -- a nonexistent path is measured at the existing parent) and MeasureTree (the total size of the files under a dir).
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (tree-journey root)))
    (setv usage (on [os-file-handler] (ReadDiskUsage (+ root "/missing/deeper"))))
    (assert (isinstance usage DiskUsage) usage)
    (assert (<= 0 usage.free usage.total) usage)
    (assert (> usage.total 0) usage))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m") :free 7 :total 9))] (tree-journey "/m")))
  (assert (= real memory) #(real memory))
  (assert (= (get real 0) (+ (len (.encode "αβ" "utf-8")) 3)) real)
  (assert (isinstance (get real 2) FileFailed) real)
  (assert (= (on [(state) (memory-file-handler (MemoryFiles :free 7 :total 9))] (ReadDiskUsage "/none")) (DiskUsage :total 9 :free 7))))


(defk stat-tree-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "A scenario that builds a tree (2 files, a lower dir, a symlink pointing at a file), walks it with stat (StatTree), and returns whether it lists the same names as WalkTree, the names, kinds, and sizes (the size of a dir
   is None since it differs per real file system), and the two refusals (walking a file, walking a nonexistent path)."
  (<- (MakeDirectory (+ root "/t/sub")))
  (<- (WriteText (+ root "/t/a") "αβ"))
  (<- (WriteText (+ root "/t/sub/b") "xyz"))
  (<- (MakeSymlink (+ root "/t/l") "sub/b"))
  (<- stats tuple (StatTree (+ root "/t")))
  (<- walked tuple (WalkTree (+ root "/t")))
  (<- file (StatTree (+ root "/t/a")))
  (<- missing (StatTree (+ root "/t/none")))
  (val shown (lfor answer [file missing]
                   (if (isinstance answer FileFailed)
                       #((.replace answer.path root "") (get (.split (get (.split answer.detail "] " 1) -1) ":") 0))
                       answer)))
  #((= (tuple (gfor e stats e.name)) (tuple (gfor e walked e.name)))
    (tuple (gfor e stats #(e.name e.kind (if (= e.kind PathKind.DIRECTORY) None e.size))))
    #* shown))


(deftest test-stat-tree-answers-the-same-on-the-real-and-memory-file-systems [tmp-path]
  ;; StatTree lists the same names as WalkTree in the same order, and the kinds, sizes (a link is not followed; the link itself), and refusals are the same for the real one and memory.
  ;; The real mtime is the same as os.lstat (memory has no time, so 0).
  (val root (os.path.realpath tmp-path))
  (<- real tuple (with_handlers [os-file-handler] (stat-tree-journey root)))
  (<- memory tuple (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (stat-tree-journey "/m")))
  (assert (= real memory) #(real memory))
  (assert (get real 0) real)
  (assert (= (get real 1) #(#("a" PathKind.FILE (len (.encode "αβ" "utf-8"))) #("l" PathKind.SYMLINK (len "sub/b")) #("sub" PathKind.DIRECTORY None)
                            #("sub/b" PathKind.FILE 3)))
          real)
  (assert (= (cut real 2 None) #(#("/t/a" "Not a directory") #("/t/none" "No such file or directory"))) real)
  (<- stats tuple (with_handlers [os-file-handler] (StatTree (+ root "/t"))))
  (assert (= (. (get stats 0) modified) (. (os.lstat (+ root "/t/a")) st-mtime)) stats))


(deftest test-stat-tree-names-an-entry-gone-before-its-stat-missing [tmp-path monkeypatch]
  ;; What vanished after the listing and before the stat is read is not dropped, but listed with kind MISSING and size 0, like a path with no StatPath (the walk does not refuse -- because a writer
  ;; walks the destination that it replaces from a temporary file). The vanishing is a swap of os.lstat.
  (.mkdir (/ tmp-path "t"))
  (.write-bytes (/ tmp-path "t" "kept") b"abc")
  (.write-bytes (/ tmp-path "t" "gone") b"x")
  (val original os.lstat)
  (.setattr monkeypatch os "lstat" (fn [path #* args #** options]
                                     (if (.endswith (str path) "/t/gone")
                                         (raise (FileNotFoundError 2 "No such file or directory" path))
                                         (original path #* args #** options))))
  (<- stats tuple (with_handlers [os-file-handler] (StatTree (str (/ tmp-path "t")))))
  (assert (= (tuple (gfor e stats #(e.name e.kind e.size))) #(#("gone" PathKind.MISSING 0) #("kept" PathKind.FILE 3))) stats))


(deftest test-list-directory-skips-a-name-gone-before-its-kind-is-read [tmp-path monkeypatch]
  ;; Names that vanished after listing and before the kind is read are skipped, and the rest are returned in name order (a copy of the names present at listing time). The vanishing is a
  ;; swap of os.lstat. Failure case (the former form): if one process ended while /proc was being listed, the whole listing became a FileFailed.
  (.mkdir (/ tmp-path "t"))
  (.write-bytes (/ tmp-path "t" "kept") b"abc")
  (.write-bytes (/ tmp-path "t" "gone") b"x")
  (.mkdir (/ tmp-path "t" "sub"))
  (val original os.lstat)
  (.setattr monkeypatch os "lstat" (fn [path #* args #** options]
                                     (if (.endswith (str path) "/t/gone")
                                         (raise (FileNotFoundError 2 "No such file or directory" path))
                                         (original path #* args #** options))))
  (<- listed tuple (with_handlers [os-file-handler] (ListDirectory (str (/ tmp-path "t")))))
  (assert (= listed #((DirEntry :name "kept" :kind PathKind.FILE) (DirEntry :name "sub" :kind PathKind.DIRECTORY))) listed)
  (<- missing FileFailed (with_handlers [os-file-handler] (ListDirectory (str (/ tmp-path "none")))))
  (assert (in "No such file or directory" missing.detail) missing))


(defk link-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "A scenario that gives a file a name (#2462), reads from the given name, and returns 4 refusals (existing destination, nonexistent source, dir source, nonexistent parent). The refusals have the source and destination paths with root removed."
  (<- (MakeDirectory (+ root "/l/sub")))
  (<- (WriteText (+ root "/l/a.pyc") "baked"))
  (<- linked (LinkFile (+ root "/l/a.pyc") (+ root "/l/sub/a.pyc")))
  (<- read (ReadText (+ root "/l/sub/a.pyc")))
  (<- exists (LinkFile (+ root "/l/a.pyc") (+ root "/l/sub/a.pyc")))
  (<- missing (LinkFile (+ root "/l/none") (+ root "/l/b")))
  (<- directory (LinkFile (+ root "/l/sub") (+ root "/l/c")))
  (<- no-parent (LinkFile (+ root "/l/a.pyc") (+ root "/l/none/d")))
  (val shown (lfor answer [exists missing directory no-parent]
                   (if (isinstance answer FileFailed)
                       #((.replace answer.path root "") (.replace answer.detail root ""))
                       answer)))
  #(linked read #* shown))


(defn #^ None test-link-file-answers-the-same-on-the-real-and-memory-file-systems []
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (link-journey root)))
    ;; The real ones point at the same inode (not a copy).
    (assert (= (. (os.stat (+ root "/l/a.pyc")) st-ino) (. (os.stat (+ root "/l/sub/a.pyc")) st-ino))))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (link-journey "/m")))
  (assert (= (cut real 0 2) #(None "baked")) real)
  (assert (= (cut real 0 3) (cut memory 0 3)) #(real memory))
  ;; The refusals answer with the same path (the source side). The errno number in the message differs by OS for some (the dir source is the same EPERM on Linux and macOS).
  (for [i (range 2 6)]
    (assert (= (get (get real i) 0) (get (get memory i) 0)) #(i real memory)))
  (assert (in "File exists" (get real 2 1)) real)
  (assert (in "No such file" (get real 3 1)) real)
  (assert (in "No such file" (get real 5 1)) real))


(defk symlink-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "A scenario that makes a symlink pointing at a dir and follows it, swaps in a symlink pointing at another tree in one rename step (swapping an entry of the settings dir),
   and returns the unfollowed stat, listing, tree walk, 3 refusals (existing name, nonexistent parent, read of a link with no target), and the form that removes only the link."
  (<- (MakeDirectory (+ root "/t/a")))
  (<- (WriteText (+ root "/t/a/x") "A"))
  (<- (MakeDirectory (+ root "/t/b")))
  (<- (WriteText (+ root "/t/b/x") "B"))
  (<- (MakeDirectory (+ root "/h")))
  (<- made (MakeSymlink (+ root "/h/e") "../t/a"))
  (<- first (ReadText (+ root "/h/e/x")))
  (<- unfollowed (StatPath (+ root "/h/e") :follow-symlinks False))
  (<- followed (StatPath (+ root "/h/e")))
  (<- (MakeSymlink (+ root "/h/.e.next") "../t/b"))
  (<- switched (RenamePath (+ root "/h/.e.next") (+ root "/h/e")))
  (<- second (ReadText (+ root "/h/e/x")))
  (<- listed (ListDirectory (+ root "/h")))
  (<- walked (WalkTree (+ root "/h")))
  (<- through (WalkTree (+ root "/h/e")))
  (<- exists (MakeSymlink (+ root "/h/e") "../t/a"))
  (<- no-parent (MakeSymlink (+ root "/none/z") "x"))
  (<- (MakeSymlink (+ root "/h/d") "../t/none"))
  (<- dangling (ReadText (+ root "/h/d/x")))
  (<- removed (RemoveTree (+ root "/h/e")))
  (<- kept (ReadText (+ root "/t/b/x")))
  (<- gone (StatPath (+ root "/h/e") :follow-symlinks False))
  ;; The refusals are pairs of the path with root removed and the errno message (the one clause after the number) (the spelling of the errno number differs by OS for some, so only the message is compared).
  (val shown (lfor answer [exists no-parent dangling]
                   (if (isinstance answer FileFailed)
                       #((.replace answer.path root "") (get (.split (get (.split answer.detail "] " 1) -1) ":") 0))
                       answer)))
  #(made first unfollowed.kind followed.kind switched second listed walked through #* shown removed kept gone.kind))


(defn #^ None test-symlinks-answer-the-same-on-the-real-and-memory-file-systems []
  ;; The base for making an entry of the settings dir a symlink to a tree and swapping the alias symlink in one rename step. The real one and memory
  ;; give the same answer -- memory also holds links; read, stat, listing, and walk follow them; rename replaces the link; RemoveTree removes only the link.
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (symlink-journey root)))
    (assert (= (os.readlink (+ root "/h/d")) "../t/none")))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (symlink-journey "/m")))
  (assert (= real
             #(None "A" PathKind.SYMLINK PathKind.DIRECTORY None "B"
               ;; After the swap, the direct children of the dir and the walk: the alias link is gone by rename, and the link target (a dir) is not entered.
               #((DirEntry :name "e" :kind PathKind.SYMLINK))
               #((DirEntry :name "e" :kind PathKind.SYMLINK))
               #((DirEntry :name "x" :kind PathKind.FILE))
               #("/h/e" "File exists") #("/none/z" "No such file or directory") #("/h/d/x" "No such file or directory")
               None "B" PathKind.MISSING))
          real)
  (assert (= real memory) #(real memory)))


(defk compile-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "Bake 2 Python sources (a bakeable one and one with SyntaxError) and a nonexistent source (#2463), and return the list of failures and the contents of the baked .pyc."
  (<- (MakeDirectory (+ root "/c/pkg")))
  (<- (WriteText (+ root "/c/pkg/ok.py") "ANSWER = 42\n"))
  (<- (WriteText (+ root "/c/pkg/bad.py") "def broken(:\n"))
  (<- failures (CompilePythonSources (+ root "/c") #(#("pkg/ok.py" "pkg.ok") #("pkg/bad.py" "pkg.bad") #("pkg/none.py" "pkg.none"))))
  (<- pyc (ReadBytes (+ root "/c/" (pyc-path "pkg/ok.py"))))
  (<- broken (StatPath (+ root "/c/" (pyc-path "pkg/bad.py"))))
  #((lfor f failures f.path) (lfor f failures (get (.split f.reason ":") 0)) pyc broken.kind))


(defn #^ None test-compile-python-sources-answers-the-same-on-the-real-and-memory-file-systems []
  (import importlib.util)
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (compile-journey root)))
    ;; The real parallel run (jobs 2) gives the same answer.
    (setv parallel (on [os-file-handler] (CompilePythonSources (+ root "/c") #(#("pkg/ok.py" "pkg.ok") #("pkg/bad.py" "pkg.bad")) :jobs 2))))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (compile-journey "/m")))
  ;; The code in the .pyc has the source path, so the comparison of contents goes only up to the head (magic, flags, source hash).
  (assert (= #((get real 0) (get real 1) (cut (get real 2) 0 16) (get real 3))
             #((get memory 0) (get memory 1) (cut (get memory 2) 0 16) (get memory 3)))
          #(real memory))
  (assert (= (get real 0) ["pkg/bad.py" "pkg/none.py"]) real)
  (assert (= (get real 1) ["SyntaxError" "[Errno 2] No such file or directory"]) real)
  ;; A checked hash .pyc (PEP 552 -- flags 0b11).
  (assert (= (cut (get real 2) 0 4) importlib.util.MAGIC-NUMBER) real)
  (assert (= (int.from-bytes (cut (get real 2) 4 8) "little") 0b11) real)
  (assert (= (get real 3) PathKind.MISSING) real)
  (assert (= (lfor f parallel f.path) ["pkg/bad.py"]) parallel))
