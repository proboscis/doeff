;;; offloaded-lock-handler(os_file.hy)の検(agora-redesign #3051)— 1 つの scheduler の上で、錠を持った task が待ちへ移った間に別の task が
;;; 同じ錠を取りに来ても、scheduler の thread が塞がらない事。本物の錠の file(tmp の下)を使う。
;;;
;;;   (a) 錠を持つ task A が外の合図を待つ間に、task B が同じ錠を取りに来る。A は合図の後に錠を返し、B が取って返す。os-file-handler だけ
;;;       (錠の取りを呼んだ thread で待つ)だと、B の待ちが scheduler の thread を塞ぎ、A の合図の受けが回らず、A が錠を返せないまま
;;;       SCENARIO-LIMIT で赤になる(この検の失敗の形)。
;;;   (b) B が錠を待つ間に B を取り消す。A が錠を返した後に B の thread が取った錠は、後始末がその場で返す — 次の task C が同じ錠を取れる
;;;       (返さないと C は持ち主の居ない錠を待ち続け、SCENARIO-LIMIT で赤になる)。
;;;
;;; 筋書きは検の thread とは別の daemon の thread の新しい VM で回し、SCENARIO-LIMIT 秒で答えが無ければ名指して落とす(塞がった thread は
;;; daemon のまま置く — test_offloaded_process_cancel.hy と同じ作法)。
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import doeff [Program with_handlers])
(import doeff_core_effects.file_effects [AcquireLock ReleaseLock LockHeld FileFailed])
(import doeff_core_effects.os_file [os-file-handler offloaded-lock-handler])
(import doeff_core_effects.offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects.scheduler [scheduled CreateExternalPromise Task Wait Spawn Cancel TaskCancelledError])

;; 筋書き 1 つの上限(秒)。普段は 1 秒の内に終わる。過ぎたら scheduler の thread が塞がったと見て落とす。
(val SCENARIO-LIMIT 5.0)
;; A が錠を持ったまま外の合図を待つ秒(この間に B が錠を取りに来る)。
(val HOLD-SECONDS 0.2)


(defrecord Contended
  "(a) で見た事: holder-released = A が錠を返せたか・waiter-got = B が錠を取れたか。"
  (#^ bool holder-released)
  (#^ bool waiter-got))


(defrecord Abandoned
  "(b) で見た事: ended-by-cancel = 取り消した B が TaskCancelledError で終わったか・next-got = 後から来た C が錠を取れたか。"
  (#^ bool ended-by-cancel)
  (#^ bool next-got))


(defk outside-signal [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "seconds 秒後に検の殻の thread が完了させる合図を待つため(待つ間 scheduler は他の task を回す)。"
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk take-and-release [path]
  {:pre [(: path str)] :post [(: % bool)] :tags {:context "file-test" :role "program"}}
  "錠を取って返すため(答え = 取れたか)。"
  (<- held (| LockHeld FileFailed) (AcquireLock path))
  (when (isinstance held FileFailed)
    (return False))
  (<- (ReleaseLock held))
  True)


(defk contended [path]
  {:pre [(: path str)] :post [(: % Contended)] :tags {:context "file-test" :role "program"}}
  "(a) の筋書き: A が錠を取り、B を立て、外の合図を待ってから錠を返し、B の終わりを待つ。"
  (<- held LockHeld (AcquireLock path))
  (<- waiter Task (Spawn (take-and-release path)))
  (<- (outside-signal HOLD-SECONDS))
  (<- released (| FileFailed None) (ReleaseLock held))
  (<- got bool (Wait waiter))
  (Contended :holder-released (is released None) :waiter-got got))


(defk ended-by-cancel [task]
  {:pre [(: task Task)] :post [(: % bool)] :tags {:context "file-test" :role "program"}}
  "取り消した task が TaskCancelledError で終わったかを読むため。"
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError] True)))


(defk abandoned [path]
  {:pre [(: path str)] :post [(: % Abandoned)] :tags {:context "file-test" :role "program"}}
  "(b) の筋書き: A が錠を取り、B を立てて錠を待たせ、B を取り消してから錠を返し、後から来た C が取れるかを見る。"
  (<- held LockHeld (AcquireLock path))
  (<- waiter Task (Spawn (take-and-release path)))
  ;; B が錠の取りを thread へ渡すまで待つ(合図の間に scheduler が B を回す)。
  (<- (outside-signal HOLD-SECONDS))
  (<- (Cancel waiter))
  (<- cancelled bool (ended-by-cancel waiter))
  (<- (ReleaseLock held))
  (<- got bool (take-and-release path))
  (Abandoned :ended-by-cancel cancelled :next-got got))


(defk settled-within [scenario what]
  {:pre [(: scenario Program) (: what str)] :post [(: % "筋書きの答え(型は筋書きごと)")]
   :tags {:context "file-test" :role "program"}}
  "筋書きを、検の thread とは別の daemon の thread の新しい VM で os-file-handler と offloaded-lock-handler(内側)と scheduled の下に
   1 回回し、SCENARIO-LIMIT 秒まで答えを待つため。過ぎたら what を名指して落とす(頭の註)。"
  (val running (.submit (ThreadPerCall) run-detached
                        (scheduled (with_handlers [os-file-handler offloaded-lock-handler] scenario))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} が {} 秒で終わらない(scheduler の thread が塞がった)" what SCENARIO-LIMIT))))))


(deftest test-a-task-waiting-for-a-held-lock-does-not-block-the-holder [tmp-path]
  ;; (a)(頭の註)。
  (<- seen Contended (settled-within (contended (str (/ tmp-path "lock"))) "錠を持つ task と待つ task"))
  (assert seen.holder-released seen)
  (assert seen.waiter-got seen))


(deftest test-a-lock-taken-after-its-waiter-was-cancelled-is-given-back [tmp-path]
  ;; (b)(頭の註)。
  (<- seen Abandoned (settled-within (abandoned (str (/ tmp-path "lock"))) "取り消した待ちの後の錠"))
  (assert seen.ended-by-cancel seen)
  (assert seen.next-got seen))
