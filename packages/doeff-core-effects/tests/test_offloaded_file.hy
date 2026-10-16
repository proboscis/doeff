;;; offloaded-file-handler(os_file.hy)の検(card ki-97da6e8be47f)— tick が触る同期の file の効果(WriteText・ReadText・StatPath・
;;; ListDirectory・MakeDirectory・RenamePath・ReadDiskFree)を thread で待つ答え手が、os-file-handler と同じ答えを返す事と、書きが
;;; thread で待つ間も同じ scheduler の別の task が回る事。本物の file(tmp の下)を使う。test_offloaded_tree.hy と同じ作り。
(require doeff-hy.macros [defk deftest <- val])
(import threading)
(import pathlib [Path])
(import doeff [Program with_handlers])
(import doeff_core_effects.offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects.file_effects [WriteText ReadText StatPath ListDirectory MakeDirectory RenamePath ReadDiskFree FileFailed PathKind])
(import doeff_core_effects.os_file [os-file-handler offloaded-file-handler])
(import doeff_core_effects.scheduler [scheduled CreateExternalPromise Spawn Wait Task])

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
