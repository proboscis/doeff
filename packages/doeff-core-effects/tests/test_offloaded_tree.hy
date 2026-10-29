;;; offloaded-tree-handler(os_file.hy)の検(agora-redesign #3715・card ki-0da6a0186492)— 木の数え(MeasureTree)と消し(RemoveTree)と
;;; 様子つきの歩き(StatTree)を thread で待つ答え手が、os-file-handler と同じ答えを返す事と、数えや歩きが thread で待つ間も同じ scheduler の
;;; 別の task が回る事。本物の file(tmp の下)を使う。
;;; 数えを止めておく所は os.walk(measure-tree が呼ぶ)の差し替え — 検の殻の thread が合図を出すまで walk の頭で待つ。os-file-handler だけ
;;; (数えを呼んだ thread で待つ)だと、別の task が合図を出せず SCENARIO-LIMIT で赤になる。
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
(import doeff_core_effects.offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects.file_effects [MeasureTree RemoveTree StatTree FileFailed PathKind])
(import doeff_core_effects.os_file [os-file-handler offloaded-tree-handler TREE-THREADS])
(import doeff_core_effects [scheduled CreateExternalPromise Spawn Wait Task])

;; 筋書き 1 つの上限(秒)。普段は 1 秒の内に終わる。
(val SCENARIO-LIMIT 5.0)


(defrecord Measured
  "見た事: whole = 木の大きさ・missing = 無い path の答え・removed = 消した後に木が無いか。"
  (#^ int whole)
  (#^ object missing)
  (#^ bool removed))


(defk measured-and-removed [root]
  {:pre [(: root str)] :post [(: % Measured)] :tags {:context "file-test" :role "program"}}
  "木を数え、無い path を数え、木を消すため。"
  (<- whole (MeasureTree (+ root "/t")))
  (<- missing (MeasureTree (+ root "/none")))
  (<- (RemoveTree (+ root "/t")))
  (Measured :whole whole :missing missing :removed (not (os.path.exists (+ root "/t")))))


(defk walked-with-stats [path]
  {:pre [(: path str)] :post [(: % (| tuple FileFailed))] :tags {:context "file-test" :role "program"}}
  "path の下を様子つきで歩いた答えを返すため(in-thread に渡す Program)。"
  (<- stats (StatTree path))
  stats)


(defk outside-signal [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "seconds 秒後に検の殻の thread が完了させる合図を待つため(待つ間 scheduler は他の task を回す — test_offloaded_lock.hy と同じ)。"
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk walking-while-another-runs [request gate]
  {:pre [(: request (| MeasureTree StatTree)) (: gate threading.Event)] :post [(: % "request の答え")] :tags {:context "file-test" :role "program"}}
  "合図まで止まる木の数えか歩き(request)を別の task で始め、外の合図を待ってから(request が walk の頭で止まっている間)walk の合図を出し、
   request の答えを待つため。request が scheduler の thread で待つと、この task は外の合図の後に戻れず、合図を出せない。"
  (<- walking Task (Spawn request))
  (<- (outside-signal 0.2))
  (.set gate)
  (<- answer (Wait walking))
  answer)


(defk tree-at [tmp]
  {:pre [(: tmp Path)] :post [(: % str)] :tags {:context "file-test" :role "entry"}}
  "tmp の下に file 2 つ(3 byte と 5 byte)の木を置き、tmp の path を返すため。"
  (.mkdir (/ tmp "t" "sub") :parents True)
  (.write-bytes (/ tmp "t" "a") b"abc")
  (.write-bytes (/ tmp "t" "sub" "b") b"hello")
  (str tmp))


(defk in-thread [program]
  {:pre [(: program Program)] :post [(: % "program の答え")] :tags {:context "file-test" :role "program"}}
  "program を scheduled と os-file-handler・offloaded-tree-handler(内側)の下で、検の thread とは別の daemon の thread の新しい VM で 1 回
   回し、SCENARIO-LIMIT 秒まで答えを待つため(過ぎたら scheduler の thread が塞がったと名指して落とす — test_offloaded_lock.hy と同じ作法)。"
  (val running (.submit (ThreadPerCall) run-detached
                        (scheduled (with_handlers [os-file-handler offloaded-tree-handler] program))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} 秒で答えが無い(scheduler の thread が塞がった)" SCENARIO-LIMIT))))))


(deftest test-the-offloaded-tree-answers-like-the-os-file-handler [tmp-path]
  (<- root str (tree-at tmp-path))
  (<- got Measured (in-thread (measured-and-removed root)))
  (assert (= got.whole 8) got)
  (assert (isinstance got.missing FileFailed) got)
  (assert got.removed got))


(deftest test-another-task-runs-while-a-tree-is-measured [tmp-path monkeypatch]
  ;; 数えは合図まで walk の頭で止まる。合図を出すのは同じ scheduler の別の task — 数えが thread で待つ間に回れば終わる(頭の註)。
  (<- root str (tree-at tmp-path))
  (val gate (threading.Event))
  (val original os.walk)
  (.setattr monkeypatch os "walk" (fn [path] (.wait gate (* 2 SCENARIO-LIMIT)) (original path)))
  (<- size int (in-thread (walking-while-another-runs (MeasureTree (+ root "/t")) gate)))
  (assert (= size 8) size))


(deftest test-the-offloaded-stat-tree-answers-like-the-os-file-handler [tmp-path]
  ;; card ki-0da6a0186492: 様子つきの歩き(StatTree)も thread で待ち、答えは os-file-handler と同じ(名の順・file の大きさ・無い path の断り)。
  (<- root str (tree-at tmp-path))
  (<- direct tuple (with_handlers [os-file-handler] (StatTree (+ root "/t"))))
  (<- offloaded tuple (in-thread (walked-with-stats (+ root "/t"))))
  (<- missing (in-thread (walked-with-stats (+ root "/none"))))
  (assert (= offloaded direct) #(offloaded direct))
  (assert (= (tuple (gfor e offloaded #(e.name e.kind))) #(#("a" PathKind.FILE) #("sub" PathKind.DIRECTORY) #("sub/b" PathKind.FILE)))
          offloaded)
  (assert (= (tuple (gfor e offloaded :if (= e.kind PathKind.FILE) e.size)) #(3 5)) offloaded)
  (assert (isinstance missing FileFailed) missing))


(deftest test-another-task-runs-while-a-tree-is-walked-with-stats [tmp-path monkeypatch]
  ;; card ki-0da6a0186492: 様子つきの歩きは合図まで walk の頭で止まる。合図を出すのは同じ scheduler の別の task — 歩きが thread で待つ間に
  ;; 回れば終わる(頭の註と同じ形)。
  (<- root str (tree-at tmp-path))
  (val gate (threading.Event))
  (val original os.walk)
  (.setattr monkeypatch os "walk" (fn [path] (.wait gate (* 2 SCENARIO-LIMIT)) (original path)))
  (<- stats tuple (in-thread (walking-while-another-runs (StatTree (+ root "/t")) gate)))
  (assert (= (tuple (gfor e stats e.name)) #("a" "sub" "sub/b")) stats))


(defk io-class-in-thread [pool]
  {:pre [(: pool ThreadPerCall)] :post [(: % int)] :tags {:context "file-test" :role "program"}}
  "pool の thread の中から ioprio_get の syscall で IO 優先度の種類を読むため。"
  (val libc (ctypes.CDLL None :use-errno True))
  (val get-syscall {"x86_64" 252 "aarch64" 31})
  (val got (.result (.submit pool (fn [] (.syscall libc (get get-syscall (platform.machine)) 1 0)))))
  (>> got 13))


(deftest test-tree-threads-run-at-idle-io-priority-on-linux []
  ;; 掃除の thread は IDLE(種類 3)で IO を出す。呼んだ側(検の thread)の優先度は変わらない。Linux 以外では何もしない。
  (when (not (and (= sys.platform "linux") (in (platform.machine) #("x86_64" "aarch64"))))
    (pytest.skip "ioprio_set は Linux の x86_64 と aarch64 だけ"))
  (<- idle int (io-class-in-thread TREE-THREADS))
  (<- plain int (io-class-in-thread (ThreadPerCall)))
  (assert (= idle 3) idle)
  (assert (!= plain 3) plain))
