;;; Tests for offloaded-tree-handler (os_file.hy): the responder that waits on a thread for tree measuring (MeasureTree), removal (RemoveTree) and
;;; walking with stat info (StatTree) returns the same answers as os-file-handler, and another task of the same scheduler
;;; keeps running while measuring and walking wait on a thread. Uses real files (under tmp).
;;; The place where measuring is held is a replacement of os.walk (which measure-tree calls) -- it waits at the head of the walk until the test harness's thread signals. With os-file-handler alone
;;; (waiting on the thread that called the measure), another task cannot signal, and it goes red on SCENARIO-LIMIT.
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import ctypes)
(import os)
(import platform)
(import sys)
(import threading)
(import pathlib [Path])
(import pytest)
(import doeff [Program with_handlers])
(import doeff_core_effects._offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects [MeasureTree RemoveTree StatTree LinkFiles FileFailed PathKind])
(import doeff_core_effects [os-file-handler offloaded-tree-handler]
        doeff_core_effects._os_file [TREE-THREADS])
(import doeff_core_effects [scheduled CreateExternalPromise Spawn Wait Task])

;; Upper limit of one scenario (seconds). Normally it finishes within 1 second.
(val SCENARIO-LIMIT 5.0)


(defrecord Measured
  "What was seen: whole = size of the tree, missing = answer for a nonexistent path, removed = whether the tree is gone after removal."
  (#^ int whole)
  (#^ object missing)
  (#^ bool removed))


(defk measured-and-removed [root]
  {:pre [(: root str)] :post [(: % Measured)] :tags {:context "file-test" :role "program"}}
  "To measure a tree, measure a nonexistent path, and remove the tree."
  (<- whole (MeasureTree (+ root "/t")))
  (<- missing (MeasureTree (+ root "/none")))
  (<- (RemoveTree (+ root "/t")))
  (Measured :whole whole :missing missing :removed (not (os.path.exists (+ root "/t")))))


(defk linked-files [pairs]
  {:pre [(: pairs tuple)] :post [(: % (| FileFailed None))] :tags {:context "file-test" :role "program"}}
  "To return the answer of linking many files at once (a Program passed to in-thread)."
  (<- answer (LinkFiles pairs))
  answer)


(defk walked-with-stats [path]
  {:pre [(: path str)] :post [(: % (| tuple FileFailed))] :tags {:context "file-test" :role "program"}}
  "To return the answer of walking under a path with stat info (a Program passed to in-thread)."
  (<- stats (StatTree path))
  stats)


(defk outside-signal [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "To wait for a signal that the test harness's thread completes after `seconds` seconds (while waiting, the scheduler runs other tasks -- same as test_offloaded_lock.hy)."
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk walking-while-another-runs [request gate]
  {:pre [(: request (| MeasureTree StatTree)) (: gate threading.Event)] :post [(: % "the answer of request")] :tags {:context "file-test" :role "program"}}
  "To start, in another task, a tree measure or walk (request) that stops until a signal, wait for the outside signal (while request is stopped at the head of the walk), then emit the walk's signal,
   and wait for request's answer. If request waits on the scheduler's thread, this task cannot return after the outside signal and cannot emit the signal."
  (<- walking Task (Spawn request))
  (<- (outside-signal 0.2))
  (.set gate)
  (<- answer (Wait walking))
  answer)


(defk tree-at [tmp]
  {:pre [(: tmp Path)] :post [(: % str)] :tags {:context "file-test" :role "entry"}}
  "To place a tree of 2 files (3 bytes and 5 bytes) under tmp and return the tmp path."
  (.mkdir (/ tmp "t" "sub") :parents True)
  (.write-bytes (/ tmp "t" "a") b"abc")
  (.write-bytes (/ tmp "t" "sub" "b") b"hello")
  (str tmp))


(defk in-thread [program]
  {:pre [(: program Program)] :post [(: % "the answer of program")] :tags {:context "file-test" :role "program"}}
  "To run program once, under scheduled, os-file-handler and offloaded-tree-handler (inner), in a new VM on a daemon thread separate from the test thread,
   and wait up to SCENARIO-LIMIT seconds for the answer (if exceeded, fail naming that the scheduler's thread is blocked -- same convention as test_offloaded_lock.hy)."
  (val running (.submit (ThreadPerCall) run-detached
                        (scheduled (with_handlers [os-file-handler offloaded-tree-handler] program))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "no answer in {} seconds (the scheduler's thread is blocked)" SCENARIO-LIMIT))))))


(deftest test-the-offloaded-tree-answers-like-the-os-file-handler [tmp-path]
  (<- root str (tree-at tmp-path))
  (<- got Measured (in-thread (measured-and-removed root)))
  (assert (= got.whole 8) got)
  (assert (isinstance got.missing FileFailed) got)
  (assert got.removed got))


(deftest test-another-task-runs-while-a-tree-is-measured [tmp-path monkeypatch]
  ;; Measuring stops at the head of the walk until the signal. The one that emits the signal is another task of the same scheduler -- it finishes if it can run while the measure waits on a thread (see the header comment).
  (<- root str (tree-at tmp-path))
  (val gate (threading.Event))
  (val original os.walk)
  (.setattr monkeypatch os "walk" (fn [path] (.wait gate (* 2 SCENARIO-LIMIT)) (original path)))
  (<- size int (in-thread (walking-while-another-runs (MeasureTree (+ root "/t")) gate)))
  (assert (= size 8) size))


(deftest test-the-offloaded-stat-tree-answers-like-the-os-file-handler [tmp-path]
  ;; The walk with stat info (StatTree) also waits on a thread, and the answer is the same as os-file-handler (name order, file sizes, refusal for a nonexistent path).
  (<- root str (tree-at tmp-path))
  (<- direct tuple (with_handlers [os-file-handler] (StatTree (+ root "/t"))))
  (<- offloaded tuple (in-thread (walked-with-stats (+ root "/t"))))
  (<- missing (in-thread (walked-with-stats (+ root "/none"))))
  (assert (= offloaded direct) #(offloaded direct))
  (assert (= (tuple (gfor e offloaded #(e.name e.kind))) #(#("a" PathKind.FILE) #("sub" PathKind.DIRECTORY) #("sub/b" PathKind.FILE)))
          offloaded)
  (assert (= (tuple (gfor e offloaded :if (= e.kind PathKind.FILE) e.size)) #(3 5)) offloaded)
  (assert (isinstance missing FileFailed) missing))


(deftest test-the-offloaded-link-files-links-like-the-os-file-handler [tmp-path]
  ;; Linking many files at once (LinkFiles -- the env tree copy) also waits on a thread: the links and the made parent dirs are the same as
  ;; os-file-handler's, and a missing source is the same refusal.
  (<- root str (tree-at tmp-path))
  (<- linked (in-thread (linked-files #(#((+ root "/t/a") (+ root "/u/a")) #((+ root "/t/sub/b") (+ root "/u/sub/b"))))))
  (<- missing (in-thread (linked-files #(#((+ root "/t/none") (+ root "/v/none"))))))
  (assert (is linked None) linked)
  (assert (= (. (os.stat (+ root "/t/sub/b")) st-ino) (. (os.stat (+ root "/u/sub/b")) st-ino)) "a hard link into a made parent dir")
  (assert (and (isinstance missing FileFailed) (= missing.path (+ root "/t/none"))) missing))


(deftest test-another-task-runs-while-a-tree-is-walked-with-stats [tmp-path monkeypatch]
  ;; The walk with stat info stops at the head of the walk until the signal. The one that emits the signal is another task of the same scheduler -- it finishes if it can run while the walk waits on a thread
  ;; (same shape as the header comment).
  (<- root str (tree-at tmp-path))
  (val gate (threading.Event))
  (val original os.walk)
  (.setattr monkeypatch os "walk" (fn [path] (.wait gate (* 2 SCENARIO-LIMIT)) (original path)))
  (<- stats tuple (in-thread (walking-while-another-runs (StatTree (+ root "/t")) gate)))
  (assert (= (tuple (gfor e stats e.name)) #("a" "sub" "sub/b")) stats))


(defk io-class-in-thread [pool]
  {:pre [(: pool ThreadPerCall)] :post [(: % int)] :tags {:context "file-test" :role "program"}}
  "To read the IO priority class with the ioprio_get syscall from inside a pool thread."
  (val libc (ctypes.CDLL None :use-errno True))
  (val get-syscall {"x86_64" 252 "aarch64" 31})
  (val got (.result (.submit pool (fn [] (.syscall libc (get get-syscall (platform.machine)) 1 0)))))
  (>> got 13))


(deftest test-tree-threads-run-at-idle-io-priority-on-linux []
  ;; The cleanup thread issues IO at IDLE (class 3). The priority of the caller (the test thread) does not change. Does nothing on non-Linux.
  (when (not (and (= sys.platform "linux") (in (platform.machine) #("x86_64" "aarch64"))))
    (pytest.skip "ioprio_set is only on Linux x86_64 and aarch64"))
  (<- idle int (io-class-in-thread TREE-THREADS))
  (<- plain int (io-class-in-thread (ThreadPerCall)))
  (assert (= idle 3) idle)
  (assert (!= plain 3) plain))
