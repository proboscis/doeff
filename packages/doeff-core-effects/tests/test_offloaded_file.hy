;;; offloaded-file-handler(os_file.hy)の検(card ki-97da6e8be47f)— tick が触る同期の file の効果(WriteText・ReadText・StatPath・
;;; ListDirectory・MakeDirectory・RenamePath・ReadDiskFree)を thread で待つ答え手が、os-file-handler と同じ答えを返す事と、書きが
;;; thread で待つ間も同じ scheduler の別の task が回る事。本物の file(tmp の下)を使う。test_offloaded_tree.hy と同じ作り。
(require doeff-hy.macros [defk deftest <- val])
(import threading)
(import pathlib [Path])
(import doeff [Program with_handlers])
(import doeff_core_effects.offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects.file_effects [WriteText ReadText StatPath ListDirectory MakeDirectory RenamePath ReadDiskFree FileFailed PathKind
                                         ReadBytes WriteBytes AppendText CopyFile CopyTree WalkTree LinkFile MakeSymlink ReadDiskUsage])
(import doeff_core_effects.os_file [os-file-handler offloaded-file-handler])
(import doeff_core_effects [scheduled CreateExternalPromise Spawn Wait Task])

(val SCENARIO-LIMIT 5.0)


(defk touched [root]
  {:pre [(: root str)] :post [(: % tuple)] :tags {:context "file-test" :role "program"}}
  "dir を作り・書き・読み・様子を読み・改名し・一覧し・空きを読んだ答えを並べて返すため。"
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
  "bytes を書き・読み・追記し・写し・張り・木を写して歩き・総量と空きを読んだ答えを並べて返すため(card ki-24ee30105583 で広げた効果)。"
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
  "合図まで止まる書きを別の task で始め、外の合図を待ってから合図を出し、書きの終わりを待つため。書きが scheduler の thread で待つと、
   この task は外の合図の後に戻れず、合図を出せない。"
  (<- writing Task (Spawn (WriteText path "x" None True False)))
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer 0.2 (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  (.set gate)
  (<- (Wait writing))
  None)


(defk in-thread [program handlers]
  {:pre [(: program Program) (: handlers list)] :post [(: % "program の答え")] :tags {:context "file-test" :role "program"}}
  "program を scheduled と handlers の下で、検の thread とは別の thread の新しい VM で 1 回回し、SCENARIO-LIMIT 秒まで答えを待つため。"
  (val running (.submit (ThreadPerCall) run-detached (scheduled (with_handlers handlers program))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} 秒で答えが無い(scheduler の thread が塞がった)" SCENARIO-LIMIT))))))


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
  ;; 書きは合図まで write-content の頭(tempfile を作る所)で止まる。合図を出すのは同じ scheduler の別の task — 書きが thread で待つ間に回れば終わる。
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
  "合図まで止まる bytes の読みを別の task で始め、外の合図を待ってから合図を出し、読みの答えを返すため(書きの筋書きと同じ作り — 読みが
   scheduler の thread で待つと、この task は戻れず合図を出せない)。"
  (<- reading Task (Spawn (ReadBytes path)))
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer 0.2 (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  (.set gate)
  (<- content (Wait reading))
  content)


(deftest test-another-task-runs-while-bytes-are-read [tmp-path monkeypatch]
  ;; 読みは合図まで Path.read_bytes の頭で止まる(card ki-24ee30105583 — turn-host が会話の記録を読む間も他の手番が回る事)。
  (val target (/ tmp-path "f"))
  (.write-bytes target b"payload")
  (val gate (threading.Event))
  (val original Path.read-bytes)
  (.setattr monkeypatch Path "read_bytes" (fn [self] (.wait gate (* 2 SCENARIO-LIMIT)) (original self)))
  (<- content bytes (in-thread (read-while-another-runs (str target) gate) [os-file-handler offloaded-file-handler]))
  (assert (= content b"payload") content))
