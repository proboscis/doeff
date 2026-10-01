;; effect の記録と再生(backtest)のテスト(record_handlers.hy・record_model.hy・effect_codec.hy)。仮想の時計・メモリの盤・fake の書き先。
;;
;;   1. 符号化の往復(値・例外・handle)と差分の往復
;;   2. 並行: 3 つの task が眠りと共有の箱でつながる系を記録 → 同じ版で再生すると同じ順・全件一致。順を揃えない再生では違う順になる(対照)
;;   3. 業務の書き手の記録と再生は業務の側の検が持つ
;;   4. 判断を 1 か所変えた版 → 違いはその profile の書きだけ
;;   5. 読み方を変えた版 → 分岐として止まる(推測で答えを作らない)
(require doeff-hy.macros [deftest defk defhandler <- var])
(import json)
(import datetime [datetime timedelta timezone])
(import doeff [EffectBase Pass with_handlers Program])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Task Wait Gather])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import tests.clock_fixtures [clock-at clock-ms])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared])
(import tests.board_fake [board-handlers])
(import doeff_cluster.shared.core.effect_codec [BlobMemory intern-json resolve-refs encode-value decode-value encode-error decode-error delta-of apply-delta canonical
                                         UnrecordableEffect RecordedError])
(import doeff_cluster.shared.core.record_model [read-recording ReplayFinished ReplayDiverged])
(import doeff_cluster.shared.protocol.record_handlers [MemorySink EffectLog effect-recorder ReplayState effect-replayer replay-report])


;; --- 1. 符号化 ---------------------------------------------------------------------------------

(deftest test-values-round-trip-without-losing-types
  (setv v {"a" [1 2.5 None True "x"] "t" #(1 "two") "b" b"\x00\x01" "$weird" 3 "nan" (float "inf")
           "nested" {"k" #(#(1 2) [3])}})
  (setv j (encode-value v))
  (json.dumps j)                                       ; JSON にできる
  (setv back (decode-value (json.loads (json.dumps j))))
  (assert (= back v) back)
  (assert (isinstance back dict) back)
  (assert (isinstance (get back "t") tuple))
  (setv e (decode-error (json.loads (json.dumps (encode-error (KeyError "missing"))))))
  (assert (and (isinstance e KeyError) (= e.args #("missing"))))
  (setv gone (decode-error {"$e" "no.such.module:Err" "args" ["x"] "msg" "x" "attrs" {}}))
  (assert (isinstance gone RecordedError)))

(deftest test-clock-answers-round-trip-with-their-timezone
  ;; 時計の答え: GetTime = timezone つきの datetime・GetMonotonic = float・Delay = None。datetime は JSON を通っても同じ時刻・同じ offset で戻る。
  (setv jst (timezone (timedelta :hours 9)))
  (for [at [(datetime 2026 9 25 1 2 3 456789 :tzinfo timezone.utc) (datetime 2026 9 25 10 2 3 :tzinfo jst)]]
    (setv back (decode-value (json.loads (json.dumps (encode-value {"at" at "mono" 1790000000.25 "slept" None})))))
    (assert (= back {"at" at "mono" 1790000000.25 "slept" None}) back)
    (assert (isinstance back dict) back)
    (assert (isinstance (get back "at") datetime))
    (assert (= (.utcoffset (get back "at")) (.utcoffset at))))
  ;; timezone の無い時刻は GetTime が返さない形 — 黙って記録せず断る。
  (var raised False)
  (try (encode-value (datetime 2026 9 25)) (except [TypeError] (:= raised True)))
  (assert raised "timezone の無い時刻は投げる"))

(deftest test-clock-effects-are-recorded-and-replayed
  ;; 記録に doeff-time の効果(GetTime / Delay)が載り、再生は記録の時刻を返す(眠らない)。
  (setv #(lines program store) (record-system))
  (<- recorded list program)
  (setv names (json.dumps lines :ensure-ascii False))
  (assert (in "doeff_time.effects.time:GetTimeEffect" names) names)
  (assert (in "doeff_time.effects.time:DelayEffect" names))
  ;; 記録の時刻(1000000 ms から 0.3 秒ごと)が再生でそのまま返る。
  (assert (in "a0@1000300" recorded) recorded)
  (setv state (ReplayState (read-recording lines)))
  (<- replayed list (with-handlers-list [(effect-replayer state)] (system-program)))
  (assert (= replayed recorded) #(replayed recorded)))

(deftest test-unknown-values-fail-instead-of-being-dropped
  (var raised False)
  (try (encode-value (object)) (except [TypeError] (:= raised True)))
  (assert raised "知らない値は投げる"))

(deftest test-delta-round-trip
  (setv prev {"items" (lfor i (range 300) {"id" i "t" (* "x" 20)}) "n" 1}
        new {"items" (+ [{"id" "new"}] (cut (get prev "items") 0 150) (cut (get prev "items") 151 None)) "n" 2})
  (setv d (delta-of prev new))
  (assert (= (apply-delta prev d) new))
  (assert (< (len (canonical d)) (// (len (canonical new)) 20)) (len (canonical d))))


;; --- 2. 並行の順 --------------------------------------------------------------------------------

(defk worker-task [name steps nap]
  {:pre [(: name str) (: steps int) (: nap float)] :post [(: % int)]}
  ;; 眠って、共有の箱(Ask "box")に自分の名を積み、盤に書く。箱は task の間で共有される(順が変われば中身の順が変わる)。
  (<- box list (Ask "box"))
  (for [i (range steps)]
    (<- (Delay nap))
    (<- now int (now-epoch-ms))
    (.append box (.format "{}{}@{}" name i now))
    (<- (WriteShared (+ "log/" name) (list box))))
  steps)

(defk system-program []
  {:pre [] :post [(: % list)]}
  (<- a Task (Spawn (worker-task "a" 4 0.3)))
  (<- b Task (Spawn (worker-task "b" 3 0.5)))
  (<- c Task (Spawn (worker-task "c" 2 0.7)))
  (<- done list (Gather a b c))
  (<- box list (Ask "box"))
  (<- (WriteShared "final" (list box)))
  (list box))

(defn #^ tuple record-system []
  (setv sink (MemorySink) clock (clock-at 1000000) box [] store {})
  (setv log (EffectLog sink {"service" "system" "run" "r1"} :strict True :wall-ms (fn [] (clock-ms clock))))
  (setv result (with-handlers-list [(sim-time-handler :clock clock) (reader {"box" box}) #* (board-handlers store) (effect-recorder log)]
                                   (system-program)))
  #(sink.lines result store))

(defn #^ Program with-handlers-list [#^ list handlers #^ Program program]
  "handler の list(外側が先)で包む。"
  (with_handlers handlers program))

(deftest test-concurrent-order-is-kept-by-replay
  (setv #(lines program store) (record-system))
  (<- recorded list program)
  (setv rec (read-recording lines))
  (assert (= (sorted (.keys rec.queues)) ["root" "root.0" "root.1" "root.2"]) (sorted (.keys rec.queues)))
  (setv state (ReplayState rec))
  (<- replayed list (with-handlers-list [(effect-replayer state)] (system-program)))
  (setv report (replay-report state "program-returned"))
  (assert (= replayed recorded) #(replayed recorded))
  (assert (get report "identical") report)
  (assert (= (get report "consumed") (get report "events")) report))


(deftest test-replay-without-the-order-gives-a-different-history
  ;; 対照: 出来事の番号の順を待たない再生は、同じ答えを返しても task の交互の順が変わり、共有の箱の中身の順が記録と違う。
  (setv #(lines program store) (record-system))
  (<- recorded list program)
  (setv state (ReplayState (read-recording lines) :ordered False))
  (<- replayed list (with-handlers-list [(effect-replayer state)] (system-program)))
  (assert (!= replayed recorded) replayed)
  (assert (> (get (get (replay-report state "program-returned") "outputDiffCounts") "changed") 0)))




;; ---- 置き場を移した module の旧い型の名を読む(#2105・#2021 の決め 2a)-----------------------------------------
(deftest test-a-type-name-written-before-the-move-resolves-to-the-type-in-its-new-place
  (import doeff_cluster.shared.core.effect_codec [resolve-type type-name MOVED-MODULES])
  (import doeff_cluster.shared.intent.detached_model [SubmitDetached])
  (import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
  ;; 書くのは今の名だけ
  (assert (= (type-name SubmitDetached) "doeff_cluster.shared.intent.detached_model:SubmitDetached"))
  ;; 移しの前に書いた記録の名(旧い module)も、今の名も、同じ class を引く
  (assert (is (resolve-type "doeff_cluster.detached_model:SubmitDetached") SubmitDetached))
  (assert (is (resolve-type "doeff_cluster.runtime_env_model:RuntimeEnv") RuntimeEnv))
  (assert (is (resolve-type (type-name SubmitDetached)) SubmitDetached))
  ;; 盤と lease の effect(#2107)— 記録に最も多く残る型
  (assert (is (resolve-type "doeff_cluster.shared_model:WriteShared") WriteShared))
  (assert (is (resolve-type "doeff_cluster.semaphore_model:HeldLease")
              (resolve-type "doeff_cluster.shared.intent.semaphore_model:HeldLease")))
  ;; 記録の綴りと記録の handler(#2108)— shared/core と foundation へ移した型も旧い名で引ける
  (import doeff_cluster.shared.core.record_model [Entry])
  (import doeff_cluster.shared.protocol.record_handlers [MemorySink])
  (assert (is (resolve-type "doeff_cluster.record_model:Entry") Entry))
  (assert (is (resolve-type "doeff_cluster.record_handlers:MemorySink") MemorySink))
  (assert (is (resolve-type "doeff_cluster.effect_codec:RecordedError")
              (resolve-type "doeff_cluster.shared.core.effect_codec:RecordedError")))
  ;; module の一部の型だけを移した物(#2025 — worker_model の JobSpec・JobPhase を shared/intent/job_model へ)も旧い名で引ける。
  ;; 引く先は表の名で決まる(worker_model が後で別の置き場へ移っても、旧い記録の JobSpec は job_model を引く)。
  (import doeff_cluster.shared.core.effect_codec [MOVED-TYPES])
  (import doeff_cluster.shared.intent.job_model [JobSpec JobPhase])
  (assert (= (type-name JobSpec) "doeff_cluster.shared.intent.job_model:JobSpec"))
  (assert (is (resolve-type "doeff_cluster.worker_model:JobSpec") JobSpec))
  (assert (is (resolve-type "doeff_cluster.worker_model:JobPhase") JobPhase))
  (assert (= (get MOVED-TYPES "doeff_cluster.worker_model:JobSpec") "doeff_cluster.shared.intent.job_model:JobSpec"))
  ;; 受け口の effect(#2180)— 移しの前の記録の NextRequests は調停ループが idle 付きで出した物なので、子 class を引く
  (import doeff_cluster.coordinator.intent.cluster_model [IdleNextRequests])
  (assert (is (resolve-type "doeff_cluster.coordinator.intent.cluster_model:NextRequests") IdleNextRequests))
  ;; record-store の effect(#2030)— 旧い module の名 doeff_cluster.record_store は今は dir(package)なので、表が無ければ引けない
  (import doeff_cluster.record_store.intent.record_store_model [AppendRecordLines])
  (assert (is (resolve-type "doeff_cluster.record_store:AppendRecordLines") AppendRecordLines))
  ;; 表に無い旧い名は引けない(黙って別の型へ倒れない)
  (assert (is (resolve-type "doeff_cluster.no_such_model:SubmitDetached") None))
  (assert (= (len MOVED-MODULES) 13)))
