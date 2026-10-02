;; effect の記録と再生(backtest)のテスト(record_handlers.hy・record_log.hy・record_codec.hy)。仮想の時計・メモリの盤・fake の書き先。
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
(import doeff_cluster.foundation.record_codec [BlobMemory intern-json resolve-refs content-hash encode-value decode-value encode-error decode-error delta-of apply-delta canonical
                                         UnrecordableEffect RecordedError HandleTable args-of type-name
                                         EffectCodec MalformedRecordSpec READ LIVE OUTPUT DECISION SPEC-FIELDS codec-of mode-of subject-of can-record
                                         register registered-types])
(import doeff_cluster.shared.intent.record_spec [RecordSpec RecordMode Unexecuted])
(import doeff_cluster.shared.intent.semaphore_model [CreateNamedSemaphore HeldLease LeaseStanding])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics ReadProcessGauges])
(import doeff_cluster.foundation.record_log [read-recording ReplayFinished ReplayDiverged LEGACY-ANY CURRENT-ANY WatchedRef recorded-args])
(import pathlib)
(import doeff_cluster.foundation.record_handlers [MemorySink EffectLog effect-recorder ReplayState effect-replayer replay-report deliver-recorded])


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


(deftest test-the-same-recording-replays-twice-to-the-same-history
  ;; #2581: 値は読む時点で 1 度だけ戻す。業務と再生の共有の箱が書き換えても、同じ Recording の 2 度目の再生は同じ答えを返す
  ;; (deliver-recorded は Entry.value の写しを渡す — 写しを外すと 2 度目が書き換え済みの箱を受けて赤)。
  (setv #(lines program store) (record-system))
  (<- recorded list program)
  (setv rec (read-recording lines))
  (<- first list (with-handlers-list [(effect-replayer (ReplayState rec))] (system-program)))
  (<- second list (with-handlers-list [(effect-replayer (ReplayState rec))] (system-program)))
  (assert (= first recorded) #(first recorded))
  (assert (= second recorded) #(second recorded)))


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
      (val replayed (! (args-of (WriteShared "row/a" (OpaqueJson.of new) new-expect) (HandleTable))))
      (assert (= (. (get rec.entries 0) args-text) (canonical replayed)) #(old old-expect (. (get rec.entries 0) args-text) replayed)))))


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


;; ---- 読んだ記録の持ち方を変える前に書いた記録の file を、今の再生で読む(#2727)--------------------------------------------
;; doeff 128a86555(Entry が引数を名 → 値の dict で持ち、比べるたびに canonical を作っていた版)の記録係で record-system を記録した行。
;; 引数を比べる形の文字列で持ち、問いを番号で引く tuple・task の問いの列を表で持つ今の読みでも、既に書いた記録の再生は同じ。
(val BEFORE-2727 (/ (. (pathlib.Path __file__) parent) "fixtures" "system_record_before_2727.json"))

(deftest test-a-recording-written-before-2727-replays-the-same
  (setv #(lines program store) (record-system))
  (<- recorded list program)
  (val before (json.loads (.read-text BEFORE-2727 :encoding "utf-8")))
  ;; 失敗ケース: 問いの引数(a)の鍵の順を逆にした同じ記録。比べる形の文字列が記録の行の鍵の順に依れば、再生の引数と食い違って赤。
  (val reordered (lfor l before (if (isinstance (.get l "a") dict)
                                    (| l {"a" (dfor k (reversed (list (get l "a"))) k (get l "a" k))})
                                    l)))
  (assert (any (gfor #(a b) (zip before reordered) (!= (list (.get a "a" {})) (list (.get b "a" {}))))) "鍵の順を替えた行が無い")
  (for [written [before reordered]]
    (val state (ReplayState (read-recording written)))
    (<- replayed list (with-handlers-list [(effect-replayer state)] (system-program)))
    (val report (replay-report state "program-returned"))
    (assert (= replayed recorded) #(replayed recorded))
    (assert (get report "identical") report)
    (assert (= (get report "consumed") (get report "events")) report)))


(deftest test-a-recorded-failure-is-delivered-as-a-fresh-exception-of-the-same-kind
  ;; #2727: 失敗の答え(err)は読む時に中継の OpaqueJson で持ち、再生が渡す時に decode-error で戻す — 同じ型・同じ文の例外を、
  ;; 渡すたびに別の object で(読む時に例外へ戻すと、同じ object を再生のたびに投げ直す)。task の終わりの行は終わった task の名に入る。
  (val head {"k" "run" "format" 2 "startedMs" 0 "service" "s" "run" "r0"})
  (val failed {"k" "call" "e" 0 "t" "root" "at" 0 "ty" (type-name ReadShared) "m" "read" "a" {"prefix" "row/"} "ok" False
               "err" (encode-error (ValueError "盤に届かない"))})
  (val rec (read-recording [head failed {"k" "end" "e" 2 "t" "root" "ok" False}]))
  (val entry (get rec.entries 0))
  (assert (isinstance entry.error OpaqueJson) entry.error)
  (assert (= rec.ended (frozenset ["root"])) rec.ended)
  (val state (ReplayState rec))
  (val codec (codec-of (ReadShared "row/")))
  (val first (deliver-recorded state entry codec))
  (val second (deliver-recorded state entry codec))
  (assert (not (or (get first 0) (get second 0))) #(first second))
  (assert (and (isinstance (get first 1) ValueError) (= (str (get first 1)) "盤に届かない")) first)
  (assert (and (isinstance (get second 1) ValueError) (is-not (get first 1) (get second 1))) #(first second)))


;; ---- WriteShared の記録は型の宣言から・OpaqueJson の欄は中の JSON の値で・ANY は値の汎用の綴りで(#2579)----------------------

(deftest test-write-shared-is-recorded-from-its-declaration
  (val handles (HandleTable))
  (assert (not-in (type-name WriteShared) (registered-types)))
  (assert (can-record WriteShared))
  (val codec (codec-of (WriteShared "row/a" (OpaqueJson.of 1))))
  (assert (= #(codec.mode codec.unexecuted codec.binds) #(OUTPUT True None)) codec)
  ;; OpaqueJson の欄は中の JSON の値で綴る(包みの型の $c にしない)・期限(ttl-seconds)は問いを見分けない
  (val args (! (args-of (WriteShared "row/a" (OpaqueJson.of {"n" [1 2]}) (OpaqueJson.of "old") 30) handles)))
  (assert (= args {"key" "row/a" "value" {"n" [1 2]} "expect" "old"}) args)
  (assert (= (subject-of (WriteShared "row/a" (OpaqueJson.of 1)) args) "row/a"))
  ;; ANY は欄の無い値の型の汎用の綴り($c)で、record_log が旧い綴りを揃える先と同じ。復号すると ANY と等しい値。
  (val any-args (! (args-of (WriteShared "row/a" (OpaqueJson.of 1)) handles)))
  (assert (= (get any-args "expect") (encode-value ANY) CURRENT-ANY) any-args)
  (assert (= (decode-value (get any-args "expect")) ANY))
  (assert (is (get (! (args-of (WriteShared "row/a" (OpaqueJson.of 1) None) handles)) "expect") None)))


;; 本番の記録(effect_records.effect_logs・2026-09-26〜10-01 の 1 run)の形を写し、値は伏せた: run の頭・内容参照の中身(blob)2 行・
;; WriteShared の call 4 行(旧い型の名 doeff_cluster.shared_model・値の $ref・ANY の旧い綴り {"$any": 1})。区切りの番号は置き場の属性
;; chunk を、記録係の書き手(MemorySink)と同じ _chunk の欄にした。名・URL・host・run の id・token は中立の値に替え、blob の h と $ref は
;; 替えた中身から記録の規則(record_codec.content-hash)で作り直した。run の頭は読みが使わない欄(factory・env・config など)を除いた。
;; 行の列(1 行 1 要素の JSON の配列 — 置き場の *.jsonl は repo の .gitignore が外すので配列にした)。
(val PRODUCTION-FRAGMENT (/ (. (pathlib.Path __file__) parent) "fixtures" "write_shared_production_record.json"))

(deftest test-a-production-write-shared-record-reads-as-the-current-form
  (val lines (json.loads (.read-text PRODUCTION-FRAGMENT :encoding "utf-8")))
  (val calls (lfor l lines :if (= (.get l "k") "call") l))
  (assert (= (len calls) 4) lines)
  (assert (all (gfor l lines (in "_chunk" l))))
  (assert (any (gfor l calls (in "$ref" (get l "a" "value")))) calls)
  (assert (all (gfor l calls (= (get l "a" "expect") LEGACY-ANY))) calls)
  (val blobs (dfor l lines :if (= (.get l "k") "blob") (get l "h") (get l "v")))
  ;; 伏せた中身の blob の h は記録の規則どおり(内容の hash)
  (assert (all (gfor #(h v) (.items blobs) (= (content-hash (canonical v)) h))) blobs)
  (val rec (read-recording lines))
  (for [l calls]
    (val entry (get rec.entries (get l "e")))
    (val value (resolve-refs (get l "a" "value") blobs))
    (val replayed (! (args-of (WriteShared (get l "a" "key") (OpaqueJson.of value)) (HandleTable))))
    (assert (= entry.args-text (canonical replayed)) #(entry.args-text replayed))
    (assert (= (get (recorded-args entry) "expect") CURRENT-ANY) entry.args-text)))


;; ---- 答えの値は記録を読む時に戻す — 再生の handler は JSON を読まない(#2581)-------------------------------------------
;; Entry.value は閉じた和 RestoredAnswer: 戻した値(decode-value の答え)か、先の答えの共有の箱の参照 WatchedRef({"$w": n})。

(deftest test-read-recording-restores-the-recorded-answers
  ;; 本番の記録の断片(書きの答え)と、時計・共有の箱(Ask の答え)・handle を含む系の記録の両方で、読んだ答えは記録の値を
  ;; decode-value で戻した物に等しい。JSON の印の付いた値(時刻 $dt・handle $h・tuple $t)は戻した型で持ち、$w は WatchedRef のまま。
  (setv #(system-lines program store) (record-system))
  (<- recorded list program)
  (for [lines [(json.loads (.read-text PRODUCTION-FRAGMENT :encoding "utf-8")) system-lines]]
    (val blobs (dfor l lines :if (= (.get l "k") "blob") (get l "h") (get l "v")))
    (val answers (dfor l lines :if (and (in (.get l "k") #("call" "ans")) (.get l "ok") (in "v" l))
                       (if (= (get l "k") "call") (get l "e") (get l "s")) (resolve-refs (get l "v") blobs)))
    (assert answers lines)
    (val rec (read-recording lines))
    (for [#(e raw) (.items answers)]
      (val value (. (get rec.entries e) value))
      (match raw
        {"$w" n} (assert (= value (WatchedRef :entry n)) #(e raw value))
        ;; 印の付いた答え($dt・$h)は JSON の dict のまま残らない(decode-value の答えと等しい = 戻した型)
        _ (assert (= value (decode-value raw)) #(e raw value)))))
  ;; 系の記録は 3 つの印をすべて含む(検が空振りしない)
  (val system-answers (json.dumps system-lines))
  (for [tag ["\"$w\"" "\"$dt\"" "\"$h\""]]
    (assert (in tag system-answers) tag)))


(deftest test-an-answer-that-cannot-be-restored-fails-when-the-recording-is-read
  ;; 戻せない答え(import できない型・読めない印・壊れた時刻・型の欄と合わない中身)は、読む時に問いの番号と effect の型を名指して断る。
  ;; 黙って None や JSON の dict のまま Entry に入れない(以前は再生が答えを返す時まで JSON のまま運び、知らない印は素の dict になった)。
  (val head {"k" "run" "format" 2 "startedMs" 0 "service" "s" "run" "r0"})
  (for [bad [{"$c" "doeff_cluster.no_such_model:Gone" "f" {}}
             {"$zz" 1}
             {"n" {"$unknown" [1]}}
             {"$dt" "not a time"}
             {"$c" (type-name WriteShared) "f" {"no_such_field" 1}}]]
    (val line {"k" "call" "e" 7 "t" "root" "at" 0 "ty" (type-name ReadShared) "m" "read" "a" {"key" "row/a"} "ok" True "v" bad})
    (var refused None)
    (try (read-recording [head line])
         (except [err ValueError] (:= refused (str err))))
    (assert (is-not refused None) #(bad "読めたことにしてはいけない"))
    (assert (in "問い 7" refused) refused)
    (assert (in (type-name ReadShared) refused) refused)))


;; ---- 置き場を移した module の旧い型の名を読む(#2105・#2021 の決め 2a)-----------------------------------------
(deftest test-a-type-name-written-before-the-move-resolves-to-the-type-in-its-new-place
  (import doeff_cluster.foundation.record_codec [resolve-type type-name MOVED-MODULES])
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
  (import doeff_cluster.foundation.record_log [Entry])
  (import doeff_cluster.foundation.record_handlers [MemorySink])
  (assert (is (resolve-type "doeff_cluster.record_model:Entry") Entry))
  (assert (is (resolve-type "doeff_cluster.record_handlers:MemorySink") MemorySink))
  (assert (is (resolve-type "doeff_cluster.effect_codec:RecordedError")
              (resolve-type "doeff_cluster.shared.core.effect_codec:RecordedError")))
  ;; foundation へ移した後(#2580)— shared/core と shared/protocol に在った間の記録の名も、今の置き場の同じ class を引く
  (import doeff_cluster.foundation.record_codec [RecordedError])
  (assert (= (type-name Entry) "doeff_cluster.foundation.record_log:Entry"))
  (assert (is (resolve-type "doeff_cluster.shared.core.effect_codec:RecordedError") RecordedError))
  (assert (is (resolve-type "doeff_cluster.effect_codec:RecordedError") RecordedError))
  (assert (is (resolve-type "doeff_cluster.shared.core.record_model:Entry") Entry))
  (assert (is (resolve-type "doeff_cluster.shared.protocol.record_handlers:MemorySink") MemorySink))
  ;; module の一部の型だけを移した物(#2025 — worker_model の JobSpec・JobPhase を shared/intent/job_model へ)も旧い名で引ける。
  ;; 引く先は表の名で決まる(worker_model が後で別の置き場へ移っても、旧い記録の JobSpec は job_model を引く)。
  (import doeff_cluster.foundation.record_codec [MOVED-TYPES])
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
  (assert (= (len MOVED-MODULES) 20)))


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
  (val args (! (args-of effect handles)))
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
  (try (! (args-of (UndeclaredProbe "k") (HandleTable)))
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
    (val args (! (args-of effect handles)))
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
    (assert (= #(codec.name codec.watch) #(previous.name previous.watch)) effect)))


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
