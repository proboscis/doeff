;;; 追記の log の書き手のための file の effect 3 つ — AppendBytes(bytes を足し、足した後の大きさと fsync に掛かった秒を答える)・
;;; TruncateFile(在る file を切り詰める)・SyncDirectory(dir の変更を disk へ落とす)の検(file_effects.hy の頭の註)。
;;;   - 同じ筋書きを本物(一時 dir)と I/O なし(memory-file-handler)で走らせ、答えと断りが同じになることを確かめる。
;;;   - 本物は sync = True の時だけ fsync を呼ぶこと(AppendBytes・TruncateFile・SyncDirectory)を、os.fsync の差し替えで数える。
;;;   - offloaded-file-handler(thread で待つ答え手)も同じ答えを返すこと。
;;;   - rooted-file-handler が 3 つの effect の path を root の下へ移し、断りの path を内側へ戻すこと。
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
  "断りを #(root を外した path 理由の文) にするため(本物と memory で OSError の文の形が同じ)。"
  #((.replace answer.path root "") (get (.split (get (.split answer.detail "] " 1) -1) ":") 0)))


(defk append-journey [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "log を足して切り詰め、断りの 5 つ(延ばす切り詰め・無い file の切り詰め・親の無い所への追記・file の dir 落とし・無い dir の dir 落とし)を
   返す筋: #(1 回目の大きさ 2 回目の大きさ 足した後の中身 切り詰めた後の中身 dir 落としの答え 断りの 5 つ)。"
  (<- (MakeDirectory (+ root "/d")))
  (<- first Appended (AppendBytes (+ root "/d/log") b"abc\n" :sync True))
  (<- second Appended (AppendBytes (+ root "/d/log") (.encode "あい" "utf-8") :sync False))
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
  ;; 本物と memory が、足した後の大きさ(1 回目 4 byte・2 回目は UTF-8 の「あい」6 byte を足して 10)・切り詰めた後の中身・断りの 5 つ
  ;; (今の大きさより大きい切り詰め = Invalid argument・無い file・親の無い追記・file への dir 落とし = Not a directory・無い dir)で同じ。
  (val root (os.path.realpath tmp-path))
  (<- real tuple (with_handlers [os-file-handler] (append-journey root)))
  (<- memory tuple (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #("/m")))] (append-journey "/m")))
  (assert (= real memory) #(real memory))
  (assert (= (cut real 0 2) EXPECTED-SIZES) real)
  (assert (= (get real 2) (+ b"abc\n" (.encode "あい" "utf-8"))) real)
  (assert (= (get real 3) b"abc\n") real)
  (assert (is (get real 4) None) real)
  (assert (= (cut real 5 None) EXPECTED-REFUSALS) real))


(deftest test-the-offloaded-file-handler-answers-the-same-as-the-real-one [tmp-path]
  ;; thread で待つ答え手(外側の本物の答え手の内側)も、足した後の大きさと断りを同じに答える。
  (val root (os.path.realpath tmp-path))
  (<- real tuple (with_handlers [os-file-handler] (append-journey root)))
  (.mkdir (/ tmp-path "second"))
  (<- offloaded tuple ((scheduled) (with_handlers [os-file-handler offloaded-file-handler] (append-journey (str (/ tmp-path "second"))))))
  (assert (= real offloaded) #(real offloaded)))


(deftest test-append-bytes-fsyncs-only-when-asked-and-answers-the-seconds [tmp-path monkeypatch]
  ;; sync = True の AppendBytes は fsync を 1 回呼んで、その秒(0 以上)を答える・sync = False は呼ばず 0.0。memory は常に 0.0。
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
  ;; TruncateFile は sync = True の時だけ fsync・SyncDirectory は dir を開いて fsync を 1 回。切り詰めは前の部分をそのまま残す(新しい file にしない)。
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
  (assert (= (. (os.stat (+ root "/log")) st-ino) before) "切り詰めは同じ file のまま"))


(defk rooted-journey []
  {:pre [] :post [(: % tuple)] :tags {:context "file-system" :role "program"}}
  "内側の path で追記・切り詰め・dir 落としを撃ち、file を指した dir 落としの断りと、外側の置き場の中身を返す筋: #(足した後の大きさ dir 落とし
   の答え 断り 置き場)。"
  (<- first Appended (AppendBytes "/d/log" b"abcdef"))
  (<- (TruncateFile "/d/log" 3))
  (<- synced (SyncDirectory "/d"))
  (<- refused (SyncDirectory "/d/log"))
  (<- files MemoryFiles (ReadMemoryFiles))
  #(first.size synced refused files))


(deftest test-rooted-file-handler-moves-the-three-durable-effects-under-its-root
  ;; 内側の / を外側の /r の下へ移す: 追記・切り詰め・dir 落としは /r の下の file に効き、断りの path は内側の path へ戻る。
  (<- journey tuple (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #("/r" "/r/d")))]
                      (with_handlers [(rooted-file-handler "/r")] (rooted-journey))))
  (val files (get journey 3))
  (assert (= (tuple (gfor f files.files #(f.path f.content))) #(#("/r/d/log" b"abc"))) files)
  (assert (= (get journey 0) 6) journey)
  (assert (is (get journey 1) None) journey)
  (assert (= (. (get journey 2) path) "/d/log") journey))
