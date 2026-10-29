;;; 汎用の計算の effect(compute_effects.hy)の本物の答え手 thread-pool-compute-handler(agora-redesign #802 便 4)。program を呼び手が渡す
;;; thread の pool で回し、外から完了させる promise(scheduler の CreateExternalPromise)で待つ。待つのは撃った task だけで、scheduler の他の
;;; task はその間も回る — scheduled の下で使う(doeff-time の sync-time-handler の ScheduleAt と同じ形)。
;;;
;;; pool の持ち主は呼び手(作るのも閉じるのも組み立ての側 — 同時に回す数は pool の max_workers)。待っている task が Cancel されたら、まだ
;;; 始まっていない仕事は pool から外す(始まった仕事は止めない — 答えは捨てられる)。
;;; ⚠ Python の thread なので、純 Python の重い計算は GIL を分け合う。得られるのは「処理のループが計算の間も他の出来事に答え続ける」ことで、
;;; CPU の本数ぶんの速さではない。
;;;
;;; pool の負荷(ReadComputeLoad の答え): pool ごとに 1 つの断面(ComputeLoad — 変わらない値)を process に 1 つの置き場 LOADS に置き、
;;; 仕事を出す時(並び + 1)・pool の thread で回り始める時(並び − 1・走り + 1・待った秒 = 出した刻から回り始めた刻まで)・終わる時(走り − 1・
;;; 回った秒 = 回り始めた刻から終わった刻まで)・
;;; 始まる前に取り消された時(並び − 1)に、LOADS-LOCK の中で差し替える。刻は time.monotonic(出す刻は scheduler の thread・回り始めの
;;; 刻は pool の thread — 同じ時計)。答え手を組むたびでなく pool ごとに 1 つ: 同じ pool に答え手を 2 つ組んでも pool の列は 1 つ。数える
;;; のはこの答え手が出した仕事だけ(呼び手が同じ pool に直に出した仕事は数えない)。pool が捨てられれば断面も消える(弱い参照の鍵)。
(require doeff-hy.macros [defhandler deff <- val])
(val MODULE-TAGS {:context "compute" :role "foundation"})
(import concurrent.futures [Executor Future])
(import threading)
(import time)
(import weakref)
(import doeff [Program])
(import doeff [PyVM])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise Wait])
(import doeff_core_effects.compute_effects [Compute ComputeLoad ReadComputeLoad])
(import doeff_core_effects.inline_compute [computed])

;; 仕事を 1 つも出していない pool の負荷。
(val IDLE-LOAD (ComputeLoad :queued 0 :running 0 :started 0 :waited-seconds 0.0 :ran-seconds 0.0))
;; pool → その pool に出した仕事の負荷の断面(頭の註)。書きは LOADS-LOCK の中で差し替える 1 回だけ。
(val LOADS (weakref.WeakKeyDictionary))
(val LOADS-LOCK (threading.Lock))


(deff shift-load [pool queued running started waited ran]  ; defk にできない: pool の thread(仕事の入口と出口)と完了の callback からも呼ぶ VM の外の書き
  {:pre [(: pool Executor) (: queued int) (: running int) (: started int) (: waited float) (: ran float)] :post [(: % None)]}
  "pool の負荷の断面に差を足して差し替えるため(頭の註 — 並びと走りの数は同じ 1 回の差し替えで動く)。"
  (with [LOADS-LOCK]
    (setv load (.get LOADS pool IDLE-LOAD))
    (setv (get LOADS pool) (ComputeLoad :queued (+ load.queued queued) :running (+ load.running running)
                                        :started (+ load.started started) :waited-seconds (+ load.waited-seconds waited)
                                        :ran-seconds (+ load.ran-seconds ran))))
  None)


(deff load-of [pool]  ; defk にできない: 答え手の節が置き場を読む 1 行(VM の外の置き場 — 書きと同じ lock の中で読む)
  {:pre [(: pool Executor)] :post [(: % ComputeLoad)]}
  "pool の負荷の今の断面を読むため(仕事を 1 つも出していない pool は IDLE-LOAD)。"
  (with [LOADS-LOCK]
    (.get LOADS pool IDLE-LOAD)))


(deff settle [pool promise future]  ; defk にできない: pool の thread から呼ばれる完了の callback(add-done-callback に渡す関数の本体・VM の外)
  {:pre [(: pool Executor) (: promise ExternalPromise) (: future Future)] :post [(: % None)]}
  "pool の仕事の終わりを promise へ渡す(computed は例外を値にするので、ここで落ちるのは BaseException だけ)。始まる前に取り消された仕事は
   並びから外す(回り始めていないので、走りと待ちの累計には入っていない)。"
  (cond
    (.cancelled future) (shift-load pool -1 0 0 0.0 0.0)
    (is-not (.exception future) None) (.fail promise (.exception future))
    True (.complete promise (.result future))))


(deff run-computed [program pool submitted]  ; defk にできない: pool の thread で回す入口(pool.submit に渡す・VM の外から新しい VM を起こす)
  {:pre [(: program Program) (: pool Executor) (: submitted float)] :post [(: % "program の答え(computed が例外を値にした物)")]}
  "pool の thread で program の答えを値にし、回り始めと終わりを pool の負荷に書くため(待った秒 = 出した刻 submitted から回り始めた刻まで・
   回った秒 = 回り始めた刻から終わった刻まで)。"
  (setv began (time.monotonic))
  (shift-load pool -1 1 1 (max 0.0 (- began submitted)) 0.0)
  (try
    (.run (PyVM) (computed program))
    (finally
      (shift-load pool 0 -1 0 0.0 (max 0.0 (- (time.monotonic) began))))))


(defhandler thread-pool-compute-handler [#^ Executor pool]
  ;; 引数に残す理由: pool は組み立ての側が作って閉じる資源(同時に回す数もそこで決まる)。
  (Compute [program]
    (<- promise (CreateExternalPromise))
    ;; 並びに足してから出す(pool の thread が回り始めて並びから引くのは、出した後だけ)。閉じた pool が出しを断れば足した分を戻す。
    (shift-load pool 1 0 0 0.0 0.0)
    (val job (try (.submit pool run-computed program pool (time.monotonic))
                  (except [refused RuntimeError]
                    (shift-load pool -1 0 0 0.0 0.0)
                    (raise refused))))
    (.on-cancel promise (fn [] (.cancel job)))
    (.add-done-callback job (fn [done] (settle pool promise done)))
    (<- outcome (Wait promise.future))
    (resume outcome))
  (ReadComputeLoad []
    (resume (load-of pool))))
