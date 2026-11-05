;;; Three file effects for append-log writers -- AppendBytes (appends bytes and answers the size after appending and the seconds spent on fsync),
;;; TruncateFile (truncates an existing file), SyncDirectory (flushes directory changes to disk) -- tests (header comment of file_effects.hy).
;;;   - Run the same scenario against the real handler (a temp dir) and the I/O-free one (memory-file-handler), and check that answers and refusals are the same.
;;;   - Count, by replacing os.fsync, that the real handler calls fsync only when sync = True (AppendBytes, TruncateFile, SyncDirectory).
;;;   - offloaded-file-handler (the handler that waits in a thread) returns the same answers too.
;;;   - rooted-file-handler maps the paths of the three effects under the root and maps the refusal's path back to the inner one.
(require doeff-hy.macros [defk deftest <- val])
(import os)
(import unittest.mock [Mock])
(import doeff [with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [FileFailed Appended MemoryFiles ReadMemoryFiles MakeDirectory ReadBytes AppendBytes TruncateFile SyncDirectory])
(import doeff_core_effects [os-file-handler offloaded-file-handler])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [rooted-file-handler])
(import doeff_core_effects [scheduled])


(defk reason-of [answer root]
  {:pre [(: answer FileFailed) (: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "judgment"}}
  "To make a refusal #(path-with-root-removed reason-text) (the OSError text has the same shape for the real handler and memory)."
  #((.replace answer.path root "") (get (.split (get (.split answer.detail "] " 1) -1) ":") 0)))


(defk append-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "Append to the log and truncate, and return the five refusals (an extending truncate, truncating a missing file, appending where the parent is missing, a directory sync on a file, a directory sync on a missing dir):
   #(size-after-first size-after-second content-after-append content-after-truncate directory-sync-answer the-five-refusals)."
  (<- (MakeDirectory (+ root "/d")))
  (<- first Appended (AppendBytes (+ root "/d/log") b"abc\n" :sync True))
  (<- second Appended (AppendBytes (+ root "/d/log") (.encode "€€" "utf-8") :sync False))
  (<- body bytes (ReadBytes (+ root "/d/log")))
  (<- (TruncateFile (+ root "/d/log") 4 :sync True))
  (<- kept bytes (ReadBytes (+ root "/d/log")))
  (<- synced (SyncDirectory (+ root "/d")))
  (<- grown (TruncateFile (+ root "/d/log") 99))
  (<- absent (TruncateFile (+ root "/d/none") 0))
  (<- orphan (AppendBytes (+ root "/nodir/log") b"x"))
  (<- on-file (SyncDirectory (+ root "/d/log")))
  (<- on-none (SyncDirectory (+ root "/none")))
  #(first.size second.size body kept synced
    (! (reason-of grown root)) (! (reason-of absent root)) (! (reason-of orphan root)) (! (reason-of on-file root)) (! (reason-of on-none root))))


(val EXPECTED-SIZES #(4 10))
(val EXPECTED-REFUSALS #(#("/d/log" "Invalid argument") #("/d/none" "No such file or directory") #("/nodir/log" "No such file or directory")
                         #("/d/log" "Not a directory") #("/none" "No such file or directory")))


(deftest test-append-truncate-and-sync-answer-the-same-on-the-real-and-memory-file-systems [tmp-path]
  ;; The real handler and memory agree on the size after appending (4 bytes the first time; the second time appends 6 bytes of UTF-8 "€€" (3 bytes each), giving 10), the content after truncating, and the five refusals
  ;; (a truncate larger than the current size = Invalid argument, a missing file, an append with no parent, a directory sync on a file = Not a directory, a missing dir).
  (val root (os.path.realpath tmp-path))
  (<- real tuple (with_handlers [os-file-handler] (append-journey root)))
  (<- memory tuple (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (append-journey "/m")))
  (assert (= real memory) #(real memory))
  (assert (= (cut real 0 2) EXPECTED-SIZES) real)
  (assert (= (get real 2) (+ b"abc\n" (.encode "€€" "utf-8"))) real)
  (assert (= (get real 3) b"abc\n") real)
  (assert (is (get real 4) None) real)
  (assert (= (cut real 5 None) EXPECTED-REFUSALS) real))


(deftest test-the-offloaded-file-handler-answers-the-same-as-the-real-one [tmp-path]
  ;; The handler that waits in a thread (inside the outer real handler) also answers the size after appending and the refusals identically.
  (val root (os.path.realpath tmp-path))
  (<- real tuple (with_handlers [os-file-handler] (append-journey root)))
  (.mkdir (/ tmp-path "second"))
  (<- offloaded tuple (scheduled (with_handlers [os-file-handler offloaded-file-handler] (append-journey (str (/ tmp-path "second"))))))
  (assert (= real offloaded) #(real offloaded)))


(deftest test-append-bytes-fsyncs-only-when-asked-and-answers-the-seconds [tmp-path monkeypatch]
  ;; AppendBytes with sync = True calls fsync once and answers its seconds (0 or more); with sync = False it does not call it and answers 0.0. memory always answers 0.0.
  (val root (os.path.realpath tmp-path))
  (val watched (Mock :wraps os.fsync))
  (.setattr monkeypatch os "fsync" watched)
  (<- synced Appended (with_handlers [os-file-handler] (AppendBytes (+ root "/log") b"a" :sync True)))
  (assert (= watched.call-count 1) watched.call-count)
  (assert (and (isinstance synced.synced-seconds float) (>= synced.synced-seconds 0.0)) synced)
  (<- plain Appended (with_handlers [os-file-handler] (AppendBytes (+ root "/log") b"b" :sync False)))
  (assert (= watched.call-count 1) watched.call-count)
  (assert (= plain (Appended :size 2 :synced-seconds 0.0)) plain)
  (<- remembered Appended (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (AppendBytes "/m/log" b"a" :sync True)))
  (assert (= remembered (Appended :size 1 :synced-seconds 0.0)) remembered))


(deftest test-truncate-file-and-sync-directory-fsync-only-when-asked [tmp-path monkeypatch]
  ;; TruncateFile calls fsync only when sync = True; SyncDirectory opens the dir and calls fsync once. Truncating leaves the earlier part as is (it does not make a new file).
  (val root (os.path.realpath tmp-path))
  (.write-bytes (/ tmp-path "log") b"0123456789")
  (val before (. (os.stat (+ root "/log")) st-ino))
  (val watched (Mock :wraps os.fsync))
  (.setattr monkeypatch os "fsync" watched)
  (<- (with_handlers [os-file-handler] (TruncateFile (+ root "/log") 8 :sync False)))
  (assert (= watched.call-count 0) watched.call-count)
  (<- (with_handlers [os-file-handler] (TruncateFile (+ root "/log") 5 :sync True)))
  (assert (= watched.call-count 1) watched.call-count)
  (<- (with_handlers [os-file-handler] (SyncDirectory root)))
  (assert (= watched.call-count 2) watched.call-count)
  (assert (= (.read-bytes (/ tmp-path "log")) b"01234"))
  (assert (= (. (os.stat (+ root "/log")) st-ino) before) "truncating keeps the same file"))


(defk rooted-journey []
  {:pre [] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "Fire an append, a truncate and a directory sync on the inner path, and return the refusal of the directory sync pointed at a file and the contents of the outer storage: #(size-after-append directory-sync
   -answer refusal storage)."
  (<- first Appended (AppendBytes "/d/log" b"abcdef"))
  (<- (TruncateFile "/d/log" 3))
  (<- synced (SyncDirectory "/d"))
  (<- refused (SyncDirectory "/d/log"))
  (<- files MemoryFiles (ReadMemoryFiles))
  #(first.size synced refused files))


(deftest test-rooted-file-handler-moves-the-three-durable-effects-under-its-root
  ;; Map the inner / under the outer /r: append, truncate and directory sync act on the file under /r, and the refusal's path goes back to the inner path.
  (<- journey tuple (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #("/r" "/r/d")))]
                      (with_handlers [(rooted-file-handler "/r")] (rooted-journey))))
  (val files (get journey 3))
  (assert (= (tuple (gfor f files.files #(f.path f.content))) #(#("/r/d/log" b"abc"))) files)
  (assert (= (get journey 0) 6) journey)
  (assert (is (get journey 1) None) journey)
  (assert (= (. (get journey 2) path) "/d/log") journey))
