;;; 汎用の計算の effect(compute_effects.hy)の本物の答え手 thread-pool-compute-handler(agora-redesign #802 便 4)。program を呼び手が渡す
;;; thread の pool で回し、外から完了させる promise(scheduler の CreateExternalPromise)で待つ。待つのは撃った task だけで、scheduler の他の
;;; task はその間も回る — scheduled の下で使う(doeff-time の sync-time-handler の ScheduleAt と同じ形)。
;;;
;;; pool の持ち主は呼び手(作るのも閉じるのも組み立ての側 — 同時に回す数は pool の max_workers)。待っている task が Cancel されたら、まだ
;;; 始まっていない仕事は pool から外す(始まった仕事は止めない — 答えは捨てられる)。
;;; ⚠ Python の thread なので、純 Python の重い計算は GIL を分け合う。得られるのは「処理のループが計算の間も他の出来事に答え続ける」ことで、
;;; CPU の本数ぶんの速さではない。
(require doeff-hy.macros [defhandler deff <- val])
(val MODULE-TAGS {:context "compute" :role "foundation"})
(import concurrent.futures [Executor Future])
(import doeff [Program])
(import doeff_vm [PyVM])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise Wait])
(import doeff_core_effects.compute_effects [Compute])
(import doeff_core_effects.inline_compute [computed])


(deff settle [promise future]  ; defk にできない: pool の thread から呼ばれる完了の callback(add-done-callback に渡す関数の本体・VM の外)
  {:pre [(: promise ExternalPromise) (: future Future)] :post [(: % None)]}
  "pool の仕事の終わりを promise へ渡す(computed は例外を値にするので、ここで落ちるのは BaseException だけ)。"
  (cond
    (.cancelled future) None
    (is-not (.exception future) None) (.fail promise (.exception future))
    True (.complete promise (.result future))))


(deff run-computed [program]  ; defk にできない: pool の thread で回す入口(pool.submit に渡す・VM の外から新しい VM を起こす)
  {:pre [(: program Program)] :post [(: % "program の答え(computed が例外を値にした物)")]}
  "pool の thread で program の答えを値にする。"
  (.run (PyVM) (computed program)))


(defhandler thread-pool-compute-handler [#^ Executor pool]
  ;; 引数に残す理由: pool は組み立ての側が作って閉じる資源(同時に回す数もそこで決まる)。
  (Compute [program]
    (<- promise (CreateExternalPromise))
    (val job (.submit pool run-computed program))
    (.on-cancel promise (fn [] (.cancel job)))
    (.add-done-callback job (fn [done] (settle promise done)))
    (<- outcome (Wait promise.future))
    (resume outcome)))
