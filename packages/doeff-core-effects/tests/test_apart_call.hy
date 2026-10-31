;;; run-apart(apart_call.hy)の検: program の計算は撃った task の scheduler の thread の外で回り、program が出す effect は
;;; 撃った所の handler が答える。
;;;   - 計算の間も撃った run の他の task が回る(時計の拍が COMPUTE-SECONDS の間に MIN-TICKS 回以上 進む)。計算は別の thread で回り、effect の
;;;     答えは撃った thread の handler が出す。反例: 同じ program を run-apart なしで撃つと、計算の間に拍が進まない(thread を占める)。
;;;   - 失敗は両向きに渡る: 答え手が断った effect は program へ例外で投げられ(program が捕まえられる)、program の例外は run-apart の例外。
;;;   - Try の program は別の thread に留まる(撃った所へ送り返さない)。
;;;   - 撃った task を取り消すと通い路が閉じ、答えを待っていた program へ ApartClosed が投げられる(program の後始末が回る)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.handle [defhandler])
(import threading)
(import time)
(import dataclasses [dataclass])
(import doeff [with_handlers EffectBase Program])
(import doeff_core_effects [Spawn Wait Cancel TaskCancelledError])
(import doeff_core_effects [Try])
(import doeff_core_effects [await-handler])
(import doeff_time [Delay async-time-handler])
(import doeff_core_effects [ApartClosed run-apart])

;; program が effect を出さずに計算する秒・撃った run の他の task の拍の秒・計算の間に進むべき拍の下限・取り消しの後に program の後始末を
;; 待つ上限の秒。
(val COMPUTE-SECONDS 0.5)
(val TICK-SECONDS 0.01)
(val MIN-TICKS 10)
(val CLOSE-SECONDS 5.0)


(defclass [(dataclass :frozen True)] AnsweringThread [EffectBase]
  "答えた thread の番号を問う effect(撃った所の handler が答えたかを読むため)。")


(defclass [(dataclass :frozen True)] Refused [EffectBase]
  "撃った所の答え手が断る effect(断りが program へ渡るかを読むため)。")


(defhandler caller-answers
  ;; 撃った所にだけ在る答え手 — 別の thread の program の effect がここで答えられることを読むため。
  (AnsweringThread [] (resume (threading.get-ident)))
  (Refused [] (raise (ValueError "撃った所の答え手が断った"))))


(defk next-count [n]
  {:pre [(: n int)] :post [(: % int)]}
  "計算の 1 歩(effect を出さない Program)。"
  (+ n 1))


(defk computed-on [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)]}
  "seconds 秒 effect を出さずに計算し、最後に 1 度 effect を出して、#(歩の数 計算した thread 答えた thread) を返すため。"
  (val until (+ (time.monotonic) seconds))
  (var steps 0)
  (while (< (time.monotonic) until)
    (<- counted int (next-count steps))
    (:= steps counted))
  (<- answered int (AnsweringThread))
  #(steps (threading.get-ident) answered))


(defk ticking [stop]
  {:pre [(: stop threading.Event)] :post [(: % int)]}
  "撃った run の他の task として、stop まで時計の拍を数えるため。"
  (var ticks 0)
  (while (not (.is-set stop))
    (<- (Delay TICK-SECONDS))
    (:= ticks (+ ticks 1)))
  ticks)


(defk with-ticker [program]
  {:pre [(: program Program)] :post [(: % tuple)]}
  "拍を数える task を横に立てて program を撃ち、#(program の答え 拍の数) を返すため。"
  (val stop (threading.Event))
  (<- ticker (Spawn (ticking stop)))
  (<- answer program)
  (.set stop)
  (<- ticks int (Wait ticker))
  #(answer ticks))


(deftest test-the-program-computes-apart-and-its-effects-are-answered-here
  (<- seen tuple (with_handlers [await-handler (async-time-handler) caller-answers]
                                (with-ticker (run-apart (computed-on COMPUTE-SECONDS)))))
  (val answer (get seen 0))
  (val ticks (get seen 1))
  (val here (threading.get-ident))
  (assert (> (get answer 0) 0) answer)
  (assert (!= (get answer 1) here) "計算が撃った thread で回った")
  (assert (= (get answer 2) here) "effect を撃った所の handler が答えなかった")
  (assert (>= ticks MIN-TICKS) (.format "計算の間に撃った run の拍が {} 回しか進まない" ticks)))


(deftest test-computing-in-place-holds-the-thread
  ;; 反例: run-apart なしの同じ program は、計算の間 撃った run の他の task を止める。
  (<- seen tuple (with_handlers [await-handler (async-time-handler) caller-answers]
                                (with-ticker (computed-on COMPUTE-SECONDS))))
  (assert (<= (get seen 1) 1) (.format "その場の計算の間に拍が {} 回 進んだ" (get seen 1))))


(defk refusal-caught []
  {:pre [] :post [(: % str)]}
  "答え手が断る effect を撃ち、断りの例外を捕まえて文を返すため。"
  (try
    (<- (Refused))
    "断られなかった"
    (except [error ValueError]
      (str error))))


(defk broken []
  {:pre [] :post [(: % str)]}
  "落ちる program。"
  (raise (KeyError "別の thread の program が落ちた")))


(deftest test-failures-cross-in-both-directions
  (<- caught str (with_handlers [caller-answers] (run-apart (refusal-caught))))
  (assert (= caught "撃った所の答え手が断った") caught)
  (var raised None)
  (try
    (<- (run-apart (broken)))
    (except [error KeyError]
      (:= raised error)))
  (assert (is-not raised None) "program の例外が run-apart から上がらない"))


(defk computed-thread []
  {:pre [] :post [(: % int)]}
  "計算した thread の番号を返すため(Try の program として回す)。"
  (threading.get-ident))


(defk thread-inside-try []
  {:pre [] :post [(: % int)]}
  "Try の program の中で計算した thread の番号を返すため。"
  (<- attempted (Try (computed-thread)))
  attempted.value)


(deftest test-try-runs-apart
  ;; Try は別の thread の VM の中で答える(撃った所へ送り返すと、Try の program は撃った thread で回る)。
  (<- inside int (run-apart (thread-inside-try)))
  (assert (!= inside (threading.get-ident)) "Try の program が撃った thread で回った"))


(defk waits-long [closed]
  {:pre [(: closed threading.Event)] :post [(: % str)]}
  "長い待ちを撃ち、通い路が閉じたら closed を立てるため(取り消しの後始末が program に届くかを読む)。"
  (try
    (<- (Delay 30.0))
    "起きた"
    (except [error ApartClosed]
      (.set closed)
      (raise))))


(defk cancelled-while-waiting [closed]
  {:pre [(: closed threading.Event)] :post [(: % bool)]}
  "run-apart を撃った task を、program が effect の答えを待つ間に取り消し、取り消しで終わったかを返すため。"
  (<- task (Spawn (run-apart (waits-long closed))))
  (<- (Delay 0.2))
  (<- (Cancel task))
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError]
      True)))


(deftest test-cancelling-the-caller-closes-the-lane
  (val closed (threading.Event))
  (<- cancelled bool (with_handlers [await-handler (async-time-handler)] (cancelled-while-waiting closed)))
  (assert cancelled "撃った task が取り消しで終わらない")
  (assert (.wait closed CLOSE-SECONDS) "通い路が閉じたのに program へ ApartClosed が届かない"))


(deftest test-the-apart-thread-ends-without-raising
  ;; 答え・例外のどちらでも、別の thread が事後条件の違反などの例外で終わらない。
  (val raised [])
  (val previous threading.excepthook)
  (setv threading.excepthook (fn [args] (.append raised args.exc-value)))
  (try
    (<- (run-apart (next-count 1)))
    (try
      (<- (run-apart (broken)))
      (except [error KeyError]
        None))
    (for [thread (threading.enumerate)]
      (when (= thread.name "doeff-apart")
        (.join thread CLOSE-SECONDS)))
    (finally
      (setv threading.excepthook previous)))
  (assert (= raised []) raised))
