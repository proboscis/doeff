;; effect の記録と再生(backtest)のテスト(record_handlers.hy・record_model.hy・effect_codec.hy)。仮想の時計・メモリの盤・fake の書き先。
;;
;;   1. 符号化の往復(値・例外・handle)と差分の往復
;;   2. 並行: 3 つの task が眠りと共有の箱でつながる系を記録 → 同じ版で再生すると同じ順・全件一致。順を揃えない再生では違う順になる(対照)
;;   3. 業務の書き手の記録と再生は業務の側の検が持つ
;;   4. 判断を 1 か所変えた版 → 違いはその profile の書きだけ
;;   5. 読み方を変えた版 → 分岐として止まる(推測で答えを作らない)
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import json)
(import dataclasses)
(import dataclasses [dataclass])
(import typing [ClassVar])
(import datetime [datetime timedelta timezone])
(import doeff [EffectBase Pass with_handlers Program])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Task Wait Gather])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import tests.clock_fixtures [clock-at clock-ms])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared ANY])
(import tests.board_fake [board-handlers])
(import doeff_cluster.shared.core.effect_codec [BlobMemory intern-json resolve-refs encode-value decode-value encode-error decode-error delta-of apply-delta canonical
                                         UnrecordableEffect RecordedError HandleTable args-of type-name
                                         EffectCodec MalformedRecordSpec READ LIVE OUTPUT DECISION SPEC-FIELDS codec-of mode-of subject-of can-record
                                         register registered-types])
(import doeff_cluster.shared.intent.record_spec [RecordSpec RecordMode Unexecuted])
(import doeff_cluster.shared.intent.semaphore_model [CreateNamedSemaphore HeldLease LeaseStanding])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics ReadProcessGauges])
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
    (<- (WriteShared (+ "log/" name) (OpaqueJson.of (list box)))))
  steps)

(defk system-program []
  {:pre [] :post [(: % list)]}
  (<- a Task (Spawn (worker-task "a" 4 0.3)))
  (<- b Task (Spawn (worker-task "b" 3 0.5)))
  (<- c Task (Spawn (worker-task "c" 2 0.7)))
  (<- done list (Gather a b c))
  (<- box list (Ask "box"))
  (<- (WriteShared "final" (OpaqueJson.of (list box))))
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




;; ---- 盤の書き(WriteShared)の値は OpaqueJson — 値が素の JSON の値だった旧い記録も同じ値に読む(#2543)-----------------
;; 旧い形 = value・expect が素の JSON の値を encode-value で綴った物(tuple は $t・文字列でない鍵は $d)。新しい形 = OpaqueJson の中の
;; JSON の値。盤が持つ JSON の値が同じ書きは、記録から読んだ引数も同じ値になる(記録の突き合わせが「違う」を出さない)。

(deftest test-an-old-write-shared-record-reads-as-the-new-form
  (val cases [#({"n" 1 "xs" #(1 2) "m" {3 "x"}} {"n" 1 "xs" [1 2] "m" {"3" "x"}})
              #("text" "text") #(None None) #([1 2.5 True] [1 2.5 True]) #(#("a" #(1)) ["a" [1]])])
  (val expects [#({"$any" 1} ANY) #(None None) #((encode-value #("v" 0)) (OpaqueJson.of ["v" 0]))])
  (for [#(old new) cases]
    (for [#(old-expect new-expect) expects]
      (val line {"k" "call" "e" 0 "t" "root" "at" 0 "ty" (type-name WriteShared) "m" "output" "sj" "row/a"
                 "a" {"key" "row/a" "value" (encode-value old) "expect" old-expect} "ok" True "v" True})
      (val rec (read-recording [{"k" "run" "format" 2 "startedMs" 0 "service" "s" "run" "r0"} line]))
      (val replayed (args-of (WriteShared "row/a" (OpaqueJson.of new) new-expect) (HandleTable)))
      (assert (= (canonical (. (get rec.entries 0) args)) (canonical replayed)) #(old old-expect (. (get rec.entries 0) args) replayed)))))


(deftest test-a-recording-mixing-old-and-new-write-shared-forms-replays-without-differences
  ;; 1 つおきの WriteShared の行を旧い版の業務コードが tuple で書いた形にする(盤の JSON では同じ list)。
  (setv #(lines program store) (record-system))
  (<- recorded list program)
  (val writes (lfor l lines :if (= (.get l "ty") (type-name WriteShared)) l))
  (assert (> (len writes) 4) (len writes))
  (for [l (cut writes 0 None 2)]
    (setv (get l "a" "value") {"$t" (get l "a" "value")}))
  (setv state (ReplayState (read-recording lines)))
  (<- replayed list (with-handlers-list [(effect-replayer state)] (system-program)))
  (setv report (replay-report state "program-returned"))
  (assert (= replayed recorded) #(replayed recorded))
  (assert (= (get report "outputDiffCounts") {"changed" 0 "missing" 0 "extra" 0}) report)
  (assert (get report "identical") report))


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
  ;; worker の型(#2025 の 2 本目)— 記録に残る worker の effect(StartJob など)の旧い名
  (import doeff_cluster.worker.intent.worker_model [StartJob])
  (assert (is (resolve-type "doeff_cluster.worker_model:StartJob") StartJob))
  ;; 実行環境の準備と drain の型(#2025 の 3 本目)
  (import doeff_cluster.worker.intent.env_prepare_model [PrepareNote] doeff_cluster.shared.intent.env_marker_model [FileSha256]
          doeff_cluster.worker.intent.drain_model [CoordinatorCall])
  (assert (is (resolve-type "doeff_cluster.env_prepare:PrepareNote") PrepareNote))
  (assert (is (resolve-type "doeff_cluster.env_prepare:FileSha256") FileSha256))
  (assert (is (resolve-type "doeff_cluster.drain_client:CoordinatorCall") CoordinatorCall))
  (assert (= (len MOVED-MODULES) 17)))


;; ---- 型の宣言(__record_spec__)で記録する(#2578)--------------------------------------------------------------
;; 既定の形で足りる型は、登録表の行の代わりに class の本体に記録の形の宣言を置く。codec は登録表 → 型そのものの宣言の順に引き、
;; 親の宣言を子 class に継がない。登録も宣言も無い型・読めない宣言・登録表と宣言の両方を持つ型は、型の名を挙げて止める。

(defclass [(dataclass :frozen True)] DeclaredWrite [EffectBase]
  (setv #^ (get ClassVar RecordSpec) __record-spec__
        (RecordSpec :mode RecordMode.DECISION :subject "key" :args #("key" "value") :unexecuted Unexecuted.LANDED))
  (#^ str key)
  (#^ tuple value)
  (setv #^ int tries 0))

;; 宣言を持つ型の子(自分の宣言を持たない)。
(defclass [(dataclass :frozen True)] DeclaredWriteChild [DeclaredWrite])

;; 登録も宣言も無い型。
(defclass [(dataclass :frozen True)] UndeclaredProbe [EffectBase]
  (#^ str key))

;; 読めない宣言(語彙の外の mode)。
(defclass [(dataclass :frozen True)] MisdeclaredProbe [EffectBase]
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode "sometimes"))
  (#^ str key))

;; 対の鍵の欄が型に無い宣言。
(defclass [(dataclass :frozen True)] StraySubjectProbe [EffectBase]
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT :subject "row" :unexecuted Unexecuted.NOTHING))
  (#^ str key))

;; 宣言を持つのに登録表にも足そうとする型。
(defclass [(dataclass :frozen True)] DoublyDeclaredProbe [EffectBase]
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ))
  (#^ str key))


(deftest test-the-codec-reads-exactly-the-fields-of-the-record-spec
  ;; codec は RecordSpec を import せずに欄の名で読む — 欄の名の 2 つの置き場が食い違えば赤。
  (assert (= (set SPEC-FIELDS) (sfor f (dataclasses.fields RecordSpec) f.name)) #(SPEC-FIELDS (dataclasses.fields RecordSpec)))
  ;; 語彙の綴り(StrEnum の値)は記録の行の綴りと同じ
  (assert (= (sorted (map str RecordMode)) (sorted [READ LIVE DECISION OUTPUT]))))


(deftest test-a-declared-type-is-recordable-without-a-registry-row
  (val effect (DeclaredWrite "row/a" #(1 "two") 3))
  (val handles (HandleTable))
  (assert (not-in (type-name DeclaredWrite) (registered-types)))
  (assert (can-record DeclaredWrite))
  (assert (= (mode-of effect handles) DECISION))
  ;; args は宣言の欄だけ(tries は載せない)・値は encode-value の綴り
  (val args (args-of effect handles))
  (assert (= args {"key" "row/a" "value" {"$t" [1 "two"]}}) args)
  (assert (= (subject-of effect args) "row/a"))
  (val codec (codec-of effect))
  (assert (is codec.unexecuted True))
  (assert (is codec.binds None))
  ;; 2 度目は同じ登録(初めて見た時に表へ入れた物)
  (assert (is (codec-of effect) codec)))


(deftest test-a-child-class-does-not-inherit-the-record-declaration
  (assert (can-record DeclaredWrite))
  (assert (not (can-record DeclaredWriteChild)))
  (var message None)
  (try (codec-of (DeclaredWriteChild "row/a" #()))
       (except [e UnrecordableEffect] (:= message (str e))))
  (assert (is-not message None) "親の宣言で子を黙って記録しない")
  (assert (in (type-name DeclaredWriteChild) message) message)
  (assert (in (type-name DeclaredWrite) message) message))


(deftest test-an-effect-without-a-registry-row-or-a-declaration-is-refused-by-name
  (assert (not (can-record UndeclaredProbe)))
  (var message None)
  (try (args-of (UndeclaredProbe "k") (HandleTable))
       (except [e UnrecordableEffect] (:= message (str e))))
  (assert (is-not message None) "登録も宣言も無い型を黙って通さない")
  (assert (in (type-name UndeclaredProbe) message) message))


(deftest test-a-malformed-declaration-is-refused-by-name
  (for [#(probe word) [#(MisdeclaredProbe "sometimes") #(StraySubjectProbe "row")]]
    (var message None)
    (try (can-record probe)
         (except [e MalformedRecordSpec] (:= message (str e))))
    (assert (and (is-not message None) (in (type-name probe) message) (in word message)) #(probe message))))


(deftest test-a-type-is-not-both-registered-and-declared
  (var message None)
  (try (register (EffectCodec DoublyDeclaredProbe READ))
       (except [e MalformedRecordSpec] (:= message (str e))))
  (assert (and (is-not message None) (in (type-name DoublyDeclaredProbe) message)) message)
  (assert (not-in (type-name DoublyDeclaredProbe) (registered-types))))


;; 移す前の登録表の行(#2578 で消した 7 行)— 宣言から作った登録が同じ記録を作ることの比べの元。
(val PREVIOUS-ROWS
  {ReadShared (EffectCodec ReadShared READ)
   HeldLease (EffectCodec HeldLease READ)
   LeaseStanding (EffectCodec LeaseStanding READ)
   CreateNamedSemaphore (EffectCodec CreateNamedSemaphore READ :args (fn [e h] {"name" e.name "permits" e.permits}) :binds "named-sem")
   ReportReady (EffectCodec ReportReady OUTPUT :unexecuted None)
   ReportMetrics (EffectCodec ReportMetrics OUTPUT :unexecuted None)
   ReadProcessGauges (EffectCodec ReadProcessGauges READ)})

(deftest test-the-seven-declared-types-spell-their-records-as-the-previous-rows-did
  (val samples [(ReadShared "row/") (HeldLease "lock") (LeaseStanding "lock") (CreateNamedSemaphore "lock" 2) (CreateNamedSemaphore "solo")
                (ReportReady True "ok" "standby") (ReportReady False) (ReportMetrics {"counters" {"a" 1.0} "gauges" {} "durations" {"d" {"sum" 0.5 "count" 2}}})
                (ReadProcessGauges)])
  (assert (= (set (gfor s samples (type s))) (set PREVIOUS-ROWS)))
  (for [effect samples]
    (val previous (get PREVIOUS-ROWS (type effect)))
    (val handles (HandleTable))
    (val codec (codec-of effect))
    ;; 登録表の行ではなく宣言から作った登録
    (assert (not-in (type-name (type effect)) (registered-types)) (type effect))
    (assert (can-record (type effect)))
    (val args (args-of effect handles))
    (val old-args (if (is previous.args-fn None)
                      (dfor f (dataclasses.fields effect) f.name (encode-value (getattr effect f.name) handles))
                      (previous.args-fn effect handles)))
    (assert (= (canonical args) (canonical old-args)) #(effect args old-args))
    (assert (= (mode-of effect handles) previous.mode) effect)
    (assert (is (type (mode-of effect handles)) str) "記録の行の m は素の文字列")
    (assert (is (subject-of effect args) None) effect)
    (assert (is codec.unexecuted previous.unexecuted) #(effect codec.unexecuted previous.unexecuted))
    (assert (= codec.binds previous.binds) effect)
    (assert (or (is codec.binds None) (is (type codec.binds) str)) "handle の印は素の文字列")
    (assert (= #(codec.name codec.watch codec.recorded-fn) #(previous.name previous.watch previous.recorded-fn)) effect)))


(defk read-after-writes []
  {:pre [] :post [(: % dict)]}
  ;; 読みの答えが内容参照の閾値(INTERN-MIN-CHARS)を超える量の行を書いてから 2 度読む(2 度目の答えは同じ中身の参照 1 つ)。
  (for [i (range 12)]
    (<- (WriteShared (.format "row/{:02d}" i) (OpaqueJson.of {"n" i "text" (* "x" 40)}))))
  (<- rows dict (ReadShared "row/"))
  (<- again dict (ReadShared "row/"))
  (assert (= rows again))
  rows)

(deftest test-a-declared-read-is-recorded-and-replayed
  ;; ReadShared は登録表の行を持たず宣言だけで記録され、再生は記録の答えを返して違い 0。記録は本番の書き手(EffectLog と置き場)の
  ;; 行の形のまま(区切りの _chunk・問いと答えの行・大きな答えの内容参照 $ref と blob の行)。
  (val sink (MemorySink))
  (val log (EffectLog sink {"service" "s" "run" "r1"} :strict True :wall-ms (fn [] 0)))
  (<- rows dict (with-handlers-list [(sim-time-handler :clock (clock-at 1000000)) #* (board-handlers {}) (effect-recorder log)]
                                    (read-after-writes)))
  (assert (= (len rows) 12) rows)
  (val reads (lfor l sink.lines :if (= (.get l "ty") (type-name ReadShared)) l))
  (assert (and reads (all (gfor l reads (= (get l "m") "read")))) sink.lines)
  (assert (all (gfor l sink.lines (in "_chunk" l))) sink.lines)
  (assert (in "blob" (sfor l sink.lines (get l "k"))) (sfor l sink.lines (get l "k")))
  (assert (in "\"$ref\"" (json.dumps sink.lines)))
  (val state (ReplayState (read-recording sink.lines)))
  (<- replayed dict (with-handlers-list [(effect-replayer state)] (read-after-writes)))
  (assert (= replayed rows) #(replayed rows))
  (assert (get (replay-report state "program-returned") "identical")))
