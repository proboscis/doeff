;;; Tests for offloaded-file-handler (os_file.hy) -- that the handler which waits in a thread for the synchronous file effects touched by tick (WriteText, ReadText, StatPath,
;;; ListDirectory, MakeDirectory, RenamePath, ReadDiskFree) returns the same answers as os-file-handler, and that other tasks of the same scheduler keep running
;;; while a write waits in a thread. Uses real files (under tmp). Same structure as test_offloaded_tree.hy.
(require doeff-hy.macros [defk deftest <- val])
(import threading)
(import pathlib [Path])
(import doeff [Program with_handlers])
(import doeff_core_effects._offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects [WriteText ReadText StatPath ListDirectory MakeDirectory RenamePath ReadDiskFree FileFailed PathKind ReadBytes WriteBytes AppendText CopyFile CopyTree WalkTree LinkFile MakeSymlink ReadDiskUsage])
(import doeff_core_effects [os-file-handler offloaded-file-handler])
(import doeff_core_effects [scheduled CreateExternalPromise Spawn Wait Task])

(val SCENARIO-LIMIT 5.0)


(defk touched [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-test" :role "program"}}
  "To return, side by side, the answers of making a dir, writing, reading, reading its status, renaming, listing, and reading free space."
  (<- made (MakeDirectory (+ root "/d/e") None))
  (<- wrote (WriteText (+ root "/d/e/a") "hello" None True False))
  (<- text (ReadText (+ root "/d/e/a")))
  (<- stat (StatPath (+ root "/d/e/a") True))
  (<- renamed (RenamePath (+ root "/d/e/a") (+ root "/d/e/b")))
  (<- listing (ListDirectory (+ root "/d/e")))
  (<- missing (ReadText (+ root "/d/e/a")))
  (<- free (ReadDiskFree root))
  #(made wrote text stat.kind stat.size renamed (tuple (gfor e listing e.name)) (isinstance missing FileFailed) (> free 0)))


(defk copied [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-test" :role "program"}}
  "To return, side by side, the answers of writing bytes, reading, appending, copying, linking, copying and walking a tree, and reading total and free space (the extended effects)."
  (<- (MakeDirectory (+ root "/t/u") None))
  (<- wrote (WriteBytes (+ root "/t/u/a") b"hello" None True False))
  (<- whole (ReadBytes (+ root "/t/u/a")))
  (<- part (ReadBytes (+ root "/t/u/a") 2 1))
  (<- appended (AppendText (+ root "/t/u/a") " world" False))
  (<- copied-file (CopyFile (+ root "/t/u/a") (+ root "/t/u/b")))
  (<- linked (LinkFile (+ root "/t/u/a") (+ root "/t/u/c")))
  (<- symlinked (MakeSymlink (+ root "/t/u/d") "a"))
  (<- copied-tree (CopyTree (+ root "/t") (+ root "/w")))
  (<- walked (WalkTree (+ root "/w")))
  (<- after (ReadBytes (+ root "/w/u/b")))
  (<- missing (ReadBytes (+ root "/t/u/none")))
  (<- usage (ReadDiskUsage root))
  #(wrote whole part appended copied-file linked symlinked copied-tree (tuple (gfor e walked #(e.name e.kind))) after
    (isinstance missing FileFailed) (> usage.total 0)))


(defk write-while-another-runs [path gate]
  {:pre [(: path str) (: gate threading.Event)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "To start, in another task, a write that stays stopped until a signal, wait for the outside signal, then send the signal and wait for the write to finish. If the write waits on the scheduler's thread,
   this task cannot come back after the outside signal and cannot send the signal."
  (<- writing Task (Spawn (WriteText path "x" None True False)))
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer 0.2 (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  (.set gate)
  (<- (Wait writing))
  None)


(defk in-thread [program handlers]
  {:pre [(: program Program) (: handlers list)] :post [(: % "answer of program")] :tags {:context "file-test" :role "program"}}
  "To run program once, under scheduled and handlers, in a new VM on a thread separate from the test's thread, waiting for the answer for up to SCENARIO-LIMIT seconds."
  (val running (.submit (ThreadPerCall) run-detached (scheduled (with_handlers handlers program))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "No answer in {} seconds (the scheduler's thread is blocked)" SCENARIO-LIMIT))))))


(deftest test-the-offloaded-file-answers-like-the-os-file-handler [tmp-path]
  (val one (tmp-path.joinpath "one"))
  (val two (tmp-path.joinpath "two"))
  (.mkdir one)
  (.mkdir two)
  (<- direct tuple (in-thread (touched (str one)) [os-file-handler]))
  (<- offloaded tuple (in-thread (touched (str two)) [os-file-handler offloaded-file-handler]))
  (assert (= offloaded direct) #(offloaded direct))
  (assert (= offloaded #(None None "hello" PathKind.FILE 5 None #("b") True True)) offloaded))


(deftest test-another-task-runs-while-a-file-is-written [tmp-path monkeypatch]
  ;; The write stops at the head of write-content (where the tempfile is made) until the signal. The signal is sent by another task of the same scheduler -- it finishes if that task runs while the write waits in a thread.
  (import tempfile)
  (val gate (threading.Event))
  (val original tempfile.NamedTemporaryFile)
  (.setattr monkeypatch tempfile "NamedTemporaryFile" (fn [#* args #** kwargs] (.wait gate (* 2 SCENARIO-LIMIT)) (original #* args #** kwargs)))
  (<- (in-thread (write-while-another-runs (str (/ tmp-path "f")) gate) [os-file-handler offloaded-file-handler]))
  (assert (= (.read-text (/ tmp-path "f")) "x")))


(deftest test-the-widened-offloaded-file-answers-like-the-os-file-handler [tmp-path]
  (val one (tmp-path.joinpath "one"))
  (val two (tmp-path.joinpath "two"))
  (.mkdir one)
  (.mkdir two)
  (<- direct tuple (in-thread (copied (str one)) [os-file-handler]))
  (<- offloaded tuple (in-thread (copied (str two)) [os-file-handler offloaded-file-handler]))
  (assert (= offloaded direct) #(offloaded direct))
  (assert (= offloaded #(None b"hello" b"el" None None None None None
                         #(#("u" PathKind.DIRECTORY) #("u/a" PathKind.FILE) #("u/b" PathKind.FILE) #("u/c" PathKind.FILE)
                           #("u/d" PathKind.SYMLINK))
                         b"hello world" True True))
          offloaded))


(defk read-while-another-runs [path gate]
  {:pre [(: path str) (: gate threading.Event)] :post [(: % bytes)] :tags {:context "file-test" :role "program"}}
  "To start, in another task, a bytes read that stays stopped until a signal, wait for the outside signal, then send the signal and return the read's answer (same structure as the write scenario -- if the read waits on
   the scheduler's thread, this task cannot come back and cannot send the signal)."
  (<- reading Task (Spawn (ReadBytes path)))
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer 0.2 (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  (.set gate)
  (<- content (Wait reading))
  content)


(deftest test-another-task-runs-while-bytes-are-read [tmp-path monkeypatch]
  ;; The read stops at the head of Path.read_bytes until the signal (so that other turns keep running while turn-host reads the conversation record).
  (val target (/ tmp-path "f"))
  (.write-bytes target b"payload")
  (val gate (threading.Event))
  (val original Path.read-bytes)
  (.setattr monkeypatch Path "read_bytes" (fn [self] (.wait gate (* 2 SCENARIO-LIMIT)) (original self)))
  (<- content bytes (in-thread (read-while-another-runs (str target) gate) [os-file-handler offloaded-file-handler]))
  (assert (= content b"payload") content))
