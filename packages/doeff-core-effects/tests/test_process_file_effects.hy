;;; 汎用の子 process と file system の effect(process_effects.hy・file_effects.hy — agora-redesign #802 便 1)の検。
;;;   - 子 process の本物(subprocess-handler)と I/O なし(scripted-process-handler)が同じ答えになる性質(returncode・出力・時間切れ・起こせない形・
;;;     環境・cwd・stdin・出力の追記)は契約テスト test_process_contract.hy。ここは片方だけの性質(本物の WorkingDirectory と ReadMachineName・
;;;     答え手の無い ReadMachineName が落ちる形・台本の機体の名・台本が env None を
;;;     受ける形・job ごとの作業 dir・台本から台本を走らせる)。
;;;   - 本物の file の答え手(os-file-handler)は一時 dir で、値の詰め替え(種類・mode・symlink を保つ写し)を確かめる。
;;;   - 同じ筋書きの Program を本物(一時 dir)と I/O なし(memory-file-handler)の両方で走らせ、答えが同じになることを確かめる(同じ所で断る)。
;;;   doeff-agents の io_effects が同じ型を re-export する(定義は 1 つ)ことの検は、doeff-agents の側
;;;   (packages/doeff-agents/tests/test_io_effects_reexports.hy)に置く — 確かめるのは doeff-agents の性質で、この package の検が上の
;;;   package を import すると層の向きが逆になる(agora-redesign #2837)。
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
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
(import doeff_core_effects.process_effects [EnvEntry ProcessOutcome RunProcess WorkingDirectory ReadMachineName])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath ReadDiskFree
                                         ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree CopyFile
                                         CopyTree RenamePath RemoveTree AcquireLock ReleaseLock DiskUsage ReadDiskUsage MeasureTree LinkFile
                                         CompilePythonSources])
(import doeff_core_effects.python_bytecode [pyc-path])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler run-scripted])


;; on が回す program の答えの型(筋書きごとに違う)。
(val T (TypeVar "T"))


(defn #^ T on [#^ list handlers #^ (get Program T) program]
  "handler の列の下で program を 1 回回すため。"
  (run (scheduled (with_handlers handlers program))))


;; --- 子 process: 本物だけ・fake だけの性質(両方が同じ答えになる性質は test_process_contract.hy の契約テスト) --------------------

(deftest test-subprocess-answers-the-own-working-directory
  ;; 本物の WorkingDirectory は自分の process の作業 dir(fake は job ごとに新しい dir — 下の台本の筋書き)。
  (<- here str (with_handlers [subprocess-handler] (WorkingDirectory)))
  (assert (= here (os.getcwd)) here))


(deftest test-subprocess-answers-the-machine-name-from-the-os
  ;; 本物の ReadMachineName は socket.gethostname と同じ値(agora-redesign #3050)。
  (<- name str (with_handlers [subprocess-handler] (ReadMachineName)))
  (assert (= name (socket.gethostname)) name))


(deftest test-scripted-process-answers-the-machine-name-of-the-script
  ;; 台本の ReadMachineName は ProcessScript の machine-name(既定は "scripted-machine")。
  (<- given str (with_handlers [(scripted-process-handler (ProcessScript :commands #() :machine-name "sim-host"))] (ReadMachineName)))
  (<- default str (with_handlers [(scripted-process-handler (ProcessScript :commands #()))] (ReadMachineName)))
  (assert (= #(given default) #("sim-host" "scripted-machine")) #(given default)))


(defk read-machine-name-alone []
  {:pre [] :post [(: % str)] :tags {:context "process-test" :role "program"}}
  "答え手を積まずに ReadMachineName を出す Program(反例の筋書き)。"
  (<- name str (ReadMachineName))
  name)


(deftest test-counterexample-machine-name-without-a-handler-is-unhandled
  ;; 答え手の無い run では ReadMachineName は未処理で落ちる(既定の名で黙って答える所は無い)。
  (with [(pytest.raises UnhandledEffect :match "ReadMachineName")]
    (run (read-machine-name-alone))))


(defk shown-env [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "台本が受けた env を名=値で並べて答える(None は None と書く)。"
  (ProcessOutcome :exit-code 0 :stderr ""
                  :stdout (match request.env
                            None "None"
                            given (.join "," (gfor e given (.format "{}={}" e.name e.value))))))


(deftest test-scripted-process-gives-the-script-none-for-an-inherited-environment
  ;; env None(呼び手の環境を継ぐ)は台本に None で渡る — 継いだ中身を読むのは台本の側(本物の子は os.environ を継ぐ)。
  ;; EXTEND で継いで足した全部を渡す規則は契約テスト。
  (val script (ProcessScript :commands #((ScriptedCommand :name "show" :run shown-env))
                             :env #((EnvEntry :name "DOEFF_INHERITED" :value "継いだ"))))
  (<- shown ProcessOutcome (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-process-handler script)]
                             (RunProcess :argv #("show"))))
  (assert (= shown.stdout "None") shown))


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
              ;; 答える前に disk へ落とす書き(sync)と、頭だけの読み(limit)— 答えは落とさない書き・全部の読みと同じ形。
              (AppendText (+ root "/a/log") "3行目\n" :sync True)
              (WriteBytes (+ root "/a/synced") b"\x01\x02\x03" :replace True :sync True)
              (WriteText (+ root "/a/b/config") "落とした" :sync True)
              (ReadBytes (+ root "/a/synced") :limit 2)
              (ReadBytes (+ root "/a/synced") :limit 10)
              (ReadText (+ root "/a/log"))
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


(defn #^ None test-the-real-and-memory-file-systems-answer-the-same-journey []
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (journey root)))
    (assert (= (stat.S_IMODE (. (os.stat (+ root "/a/b")) st_mode)) 0o700))
    (assert (= (stat.S_IMODE (. (os.stat (+ root "/a/b/config")) st_mode)) 0o600))
    ;; 空き(#831): 無い path は在る親の file system で測る(答えは断りの FileFailed でなく空きの数)。
    (setv free (on [os-file-handler] (ReadDiskFree (+ root "/missing/deeper"))))
    (assert (and (isinstance free int) (> free 0)) free))
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
  (assert (= (cut answers 22 28) #(None None None b"\x01\x02" b"\x01\x02\x03" "1行目\n2行目\n3行目\n")) answers)
  (assert (all (gfor a (cut answers 29 None) (isinstance a FileFailed))) (cut answers 29 None)))


(defk appended-reads [root]
  {:pre [(: root str)] :post [(: % Journey)]}
  "追記される file を、前に読んだ所から先だけ読む筋書き(ReadBytes の offset — 追記を待って末尾を写す読み手のため・agora-redesign #3977)。"
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
  ;; 位置 offset から先だけを読む(limit はその先の byte 数)。file の終わりより先の位置は空の bytes(失敗ではない)・無い file は FileFailed。
  ;; 本物と memory の答え手が同じ答えを返す。失敗ケース(前の形): ReadBytes に offset の欄が無く、筋書きを組む所で TypeError。
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
  ;; 壊れた symlink: 辿らなければ SYMLINK・辿れば MISSING(#805 の消費者の頼み)。
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
    "2 本目の task: 取られている錠を待ち、取れたら記録して放す。"
    (<- held LockHeld (AcquireLock "/r/lock"))
    (.append log "B got")
    (<- (ReleaseLock held))
    None)
  (defk twice []
    {:pre [] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
    "錠を取ったまま 2 本目の task を起こし、放すまで 2 本目が取れないことと、放した後に取れることを確かめる筋。"
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
  ;; 空き(#831)は置き場の設定の値で答え、錠や書きで置き場を作り直しても保つ。
  (assert (= free 1234) free)
  (assert (= seen.free 1234) seen)
  (assert (isinstance first LockHeld) first)
  ;; 錠は本物の flock と同じく取れるまで待つ(#835): 2 本目は 1 本目が放した後にだけ取れる(待たずに断るのではない)。
  (assert (= log ["A release" "B got"]) log)
  (assert (isinstance third LockHeld) third)
  (assert (= seen.locks #()) seen)
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
  {:pre [] :post [(: % tuple)] :tags {:context "process-test" :role "program"}}
  "I/O なしの子 process の筋書き 1 つ(job の dir を 2 度聞き、wrap から echo を走らせ、echo が置いた file を読む)。"
  (<- work str (WorkingDirectory))
  (<- again str (WorkingDirectory))
  (<- ran ProcessOutcome (RunProcess :argv #("/usr/bin/wrap" "echo" "a" "b") :cwd work))
  (<- echoed str (ReadText (+ work "/echoed")))
  #(work again ran echoed))


(deftest test-scripted-processes-run-the-script-on-the-file-handler
  ;; fake だけの性質: WorkingDirectory は聞くたびに新しい job の dir・台本は file の effect で置き場を書く・台本から台本を
  ;; run-scripted で走らせる。起こせない形・環境・ExecutableAt・output-path の追記は契約テスト(test_process_contract.hy)。
  (val script (ProcessScript :commands #((ScriptedCommand :name "echo" :run echo-script) (ScriptedCommand :name "wrap" :run wrap-script))
                             :work-root "/work/jobs"))
  (<- answers tuple (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-process-handler script)] (scripted-journey)))
  (assert (= answers #("/work/jobs/job-1" "/work/jobs/job-2" (ProcessOutcome :exit-code 0 :stdout "a b\n" :stderr "") "a b")) answers))


(defk tree-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "木を作って大きさの合計を測る筋(dir の下の全部・下の dir だけ・file を測る断り)。"
  (<- (MakeDirectory (+ root "/t/sub")))
  (<- (WriteText (+ root "/t/a") "あい"))
  (<- (WriteText (+ root "/t/sub/b") "xyz"))
  (<- whole (MeasureTree (+ root "/t")))
  (<- below (MeasureTree (+ root "/t/sub")))
  (<- refused (MeasureTree (+ root "/t/a")))
  #(whole below (if (isinstance refused FileFailed) (FileFailed :path (.replace refused.path root "") :detail "") refused)))


(defn #^ None test-disk-usage-and-tree-size-answer-the-same-on-the-real-and-memory-file-systems []
  ;; #2504: ReadDiskUsage(総量と空き — 無い path は在る親で測る)と MeasureTree(dir の下の file の大きさの合計)。
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv root (os.path.realpath tmp))
    (setv real (on [os-file-handler] (tree-journey root)))
    (setv usage (on [os-file-handler] (ReadDiskUsage (+ root "/missing/deeper"))))
    (assert (isinstance usage DiskUsage) usage)
    (assert (<= 0 usage.free usage.total) usage)
    (assert (> usage.total 0) usage))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m") :free 7 :total 9))] (tree-journey "/m")))
  (assert (= real memory) #(real memory))
  (assert (= (get real 0) (+ (len (.encode "あい" "utf-8")) 3)) real)
  (assert (isinstance (get real 2) FileFailed) real)
  (assert (= (on [(state) (memory-file-handler (MemoryFiles :free 7 :total 9))] (ReadDiskUsage "/none")) (DiskUsage :total 9 :free 7))))


(defk link-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "file に名を付け(#2462)、付けた名から読み、断りの 4 つ(在る先・無い元・dir の元・無い親)を返す筋。断りは元と先の path を root の外して。"
  (<- (MakeDirectory (+ root "/l/sub")))
  (<- (WriteText (+ root "/l/a.pyc") "焼いた"))
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
    ;; 本物は同じ inode を指す(写しではない)。
    (assert (= (. (os.stat (+ root "/l/a.pyc")) st-ino) (. (os.stat (+ root "/l/sub/a.pyc")) st-ino))))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (link-journey "/m")))
  (assert (= (cut real 0 2) #(None "焼いた")) real)
  (assert (= (cut real 0 3) (cut memory 0 3)) #(real memory))
  ;; 断りは同じ path(元の側)で答える。文の errno の番号は OS で違う物が在る(dir の元は Linux と macOS で同じ EPERM)。
  (for [i (range 2 6)]
    (assert (= (get (get real i) 0) (get (get memory i) 0)) #(i real memory)))
  (assert (in "File exists" (get real 2 1)) real)
  (assert (in "No such file" (get real 3 1)) real)
  (assert (in "No such file" (get real 5 1)) real))


(defk compile-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "Python の source 2 つ(焼ける物と SyntaxError の物)と無い source を焼き(#2463)、失敗の列と焼いた .pyc の中身を返す筋。"
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
    ;; 本物の並列(jobs 2)も同じ答え。
    (setv parallel (on [os-file-handler] (CompilePythonSources (+ root "/c") #(#("pkg/ok.py" "pkg.ok") #("pkg/bad.py" "pkg.bad")) :jobs 2))))
  (setv memory (on [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (compile-journey "/m")))
  ;; .pyc の中の code は source の path を持つので、中身の比べは頭(magic・flags・source の hash)まで。
  (assert (= #((get real 0) (get real 1) (cut (get real 2) 0 16) (get real 3))
             #((get memory 0) (get memory 1) (cut (get memory 2) 0 16) (get memory 3)))
          #(real memory))
  (assert (= (get real 0) ["pkg/bad.py" "pkg/none.py"]) real)
  (assert (= (get real 1) ["SyntaxError" "[Errno 2] No such file or directory"]) real)
  ;; checked hash の .pyc(PEP 552 — flags 0b11)。
  (assert (= (cut (get real 2) 0 4) importlib.util.MAGIC-NUMBER) real)
  (assert (= (int.from-bytes (cut (get real 2) 4 8) "little") 0b11) real)
  (assert (= (get real 3) PathKind.MISSING) real)
  (assert (= (lfor f parallel f.path) ["pkg/bad.py"]) parallel))
