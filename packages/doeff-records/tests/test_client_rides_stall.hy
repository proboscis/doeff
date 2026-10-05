;; 記録の service の HTTP の client が、置き場の止まりを 1 か所で越える形の失敗ケース(#3557 — http_client.hy の頭の註「置き場の止まり」)。
;; 確かめること:
;;   要求と答えの公開 effect の全部(公開 effect の一覧 PublicEffect から変化の待ち 2 つを除いた物)が、止まりの間は置き場の戻りを待ち、
;;   戻った刻に同じ要求を撃ち直して入る — 一覧に effect を 1 つ足すと、この検の表に無い物として赤になる
;;   待つ時間 0 秒(名のある答え手 records-unwaited)の組み立ては待たずに Unreachable を返す
;;   待つ時間を越えても戻らなければ、待った秒と上限を名指した Unreachable を上限ちょうどで返す
;;   待つ時間の答え手を置かない組み立ては、止まりの無い最初の要求で、答え手の無い ReadRequestPatience として落ちる(止まりの日まで隠れない)
;;   合図の源が止まりに耐える時間の答え手(source-patience-handler)だけを置いても、client は答えを得ない — 2 つは別の問い(#3557 — 1 つの
;;   問いに載せると、handler を並べる位置で答え分けるしかなくなる)
;; 反例 = 前の形(client が届かない答えをそのまま返す)は、1 本目の検で 0 秒で Unreachable を返して赤になる。
;; 組は http-memory(tests/interpreters.hy — client の要求を同じ scheduler の中で service の respond に渡し、止まりと戻りが 1 つの
;; 仮想の時計の上で進む)。止まりは検の口 faults.SetStoreOutage を置き場の handler に撃って起こし、戻りの問い AwaitRecordsBack は
;; 置き場の handler(memory)が止まりの解けた呼び鈴で答える。
(require doeff-hy.macros [deftest defk <- val])
(import typing [get-args])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled Spawn Wait Cancel Discard])
(import gc)
(import sys)
(import pytest)
(import doeff_time [Delay GetMonotonic SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent Unreachable])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd])
(import doeff_records.faults [SetStoreOutage])
(import doeff_records.laws [LAW-SCHEMA LawHarness MAKER as-writer])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.service [RecordsService])
(import doeff_records.wire [PublicEffect])
(import doeff_records.event_source [SignalSourcePatience source-patience-handler came-back-within])
(import doeff_records.http_client [RecordsEndpoint RequestPatience http-records-handler request-patience-handler records-unwaited])
(import tests.interpreters [LawSetup IN-PROCESS-URL in-process-records-http])

(val DETAIL "記録の service が落ちている(止まりを越える検の筋書き)")
;; 止まりの長さ(戻るまで)と、検が選ぶ待ちの上限。
(val STALL-SECONDS 5.0)
(val PATIENCE (RequestPatience :seconds 60.0))
(val SHORT-PATIENCE (RequestPatience :seconds 10.0))
;; 要求と答えの公開 effect の和(止まりの間に撃つ要求の型)。
(val RequestAsk (| ReadRow ListRows PutRow PutRows AppendEvent ReadEvents ReadStreamEnd))


(defk part [id]
  {:pre [(: id str)] :post [(: % FrozenMap)] :tags {:context "records" :role "program"}}
  "表 parts の行の値を作るため。"
  (FrozenMap {"id" id "label" "a" "state" "open"}))


(defk request-asks []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "要求と答えの公開 effect を 1 つずつ作るため(止まりの間に撃つ要求の表 — 種類は公開 effect の一覧の要求と答えの全部)。"
  (<- p8 FrozenMap (part "p8"))
  (<- p9 FrozenMap (part "p9"))
  #((ReadRow "parts" #("p1"))
    (ListRows "parts")
    (PutRow "parts" #("p9") p9 (ExpectAbsent))
    (PutRows #((RowWrite "parts" #("p8") p8 (ExpectAbsent))))
    (AppendEvent "journal" "stall-k1" {"n" 1})
    (ReadEvents "journal")
    (ReadStreamEnd "journal")))


(defk lifted-after [harness seconds]
  {:pre [(: harness LawHarness) (: seconds float)] :post [(: % None)] :tags {:context "records" :role "program"}}
  "seconds 秒後に置き場の止まりを解くための筋書きの手(待っている要求とは別の task — 検の口 SetStoreOutage を置き場の handler に撃つ)。"
  (<- (Delay seconds))
  (<- (as-writer harness MAKER (SetStoreOutage None)))
  None)


(defk asked-through-stall [harness patience ask]
  {:pre [(: harness LawHarness) (: patience RequestPatience) (: ask RequestAsk)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "置き場を止め、STALL-SECONDS 秒後に解く手を立ててから、待つ時間 patience の下で ask を撃ち、#(掛かった仮想の秒 答え) を返すため。"
  (<- (as-writer harness MAKER (SetStoreOutage DETAIL)))
  (<- lifter (Spawn (lifted-after harness STALL-SECONDS)))
  (<- began float (GetMonotonic))
  (<- answer (with_handlers [(request-patience-handler patience)] (as-writer harness MAKER ask)))
  (<- ended float (GetMonotonic))
  (<- (Wait lifter))
  #((- ended began) answer))


(deftest test-the-request-table-is-every-request-and-answer-effect
  ;; 表の種類 = 公開 effect の一覧から変化の待ち 2 つ(合図の源が自分で越える)を除いた全部。client に要求を 1 つ足すと、この表に無い物として赤。
  (<- asks tuple (request-asks))
  (val kinds (frozenset (gfor ask asks (type ask))))
  (assert (= kinds (- (frozenset (get-args PublicEffect)) (frozenset #(WatchChanges WatchEvents))))
          #(kinds (get-args PublicEffect))))


(deftest test-every-request-rides-out-a-stall-and-lands-when-the-store-comes-back
  {:interpreters ["http-memory"]}
  ;; 止まりの 5 秒の間に撃った要求と答えは、どれも Unreachable を返さずに戻りを待ち、戻った刻(5 秒)に撃ち直して入る。
  (<- harness (LawSetup))
  (<- asks tuple (request-asks))
  (for [ask asks]
    (<- seen tuple (asked-through-stall harness PATIENCE ask))
    (val seconds (get seen 0))
    (val answer (get seen 1))
    (assert (not (isinstance answer Unreachable)) #(ask answer))
    (assert (= seconds STALL-SECONDS) #(ask seconds answer))))


(deftest test-a-zero-patience-answers-unreachable-without-waiting
  {:interpreters ["http-memory"]}
  ;; 上限 0 秒(records-unwaited)を選んだ呼びは待たない — 止まりの最初の Unreachable をそのまま 0 秒で返す。
  (<- harness (LawSetup))
  (<- seen tuple (asked-through-stall harness (RequestPatience :seconds 0.0) (ReadRow "parts" #("p1"))))
  (assert (isinstance (get seen 1) Unreachable) seen)
  (assert (= (get seen 0) 0.0) seen))


(deftest test-a-stall-past-the-patience-names-the-wait
  {:interpreters ["http-memory"]}
  ;; 上限 10 秒・止まりは解かない: 上限ちょうどで、待った秒と上限と表の名を名指した Unreachable を返す。
  (<- harness (LawSetup))
  (<- (as-writer harness MAKER (SetStoreOutage DETAIL)))
  (<- began float (GetMonotonic))
  (<- answer (with_handlers [(request-patience-handler SHORT-PATIENCE)] (as-writer harness MAKER (ReadRow "parts" #("p1")))))
  (<- ended float (GetMonotonic))
  (<- (as-writer harness MAKER (SetStoreOutage None)))
  (assert (isinstance answer Unreachable) answer)
  (assert (= (- ended began) SHORT-PATIENCE.seconds) #((- ended began) answer))
  (assert (in "parts" answer.detail) answer)
  (assert (in "10 秒 待っても戻らない(上限 10 秒)" answer.detail) answer))


(deftest test-a-client-without-a-patience-answer-falls-on-its-first-request
  ;; 待つ時間の答え手を置かない組み立ては、止まりの無い最初の要求で ReadRequestPatience の答え手が無いと名指して落ちる(止まりの日まで
  ;; 隠れない)。合図の源の耐える時間の答え手(source-patience-handler)だけを置いた組み立ても同じく落ちる — client はその問いを問わない。
  (val store (MemoryStore LAW-SCHEMA))
  (val service (RecordsService LAW-SCHEMA (fn [writer] (memory-records-handler store writer))))
  (for [outer [[] [(source-patience-handler (SignalSourcePatience :seconds 60.0))]]]
    (var said None)
    (try
      (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) #* outer (in-process-records-http service)
                                      (http-records-handler (RecordsEndpoint IN-PROCESS-URL :writer MAKER))]
                                     (ReadRow "parts" #("p1")))))
      (except [error Exception]
        (:= said (str error))))
    (assert (is-not said None) #("待つ時間の答え手の無い組み立てが落ちなかった" outer))
    (assert (in "ReadRequestPatience" said) #(said outer)))
  ;; 同じ組み立てに名のある答え手を置けば、同じ要求は答える(落ちたのは答え手の欠けだけのため)。
  (val answered (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) records-unwaited (in-process-records-http service)
                                                (http-records-handler (RecordsEndpoint IN-PROCESS-URL :writer MAKER))]
                                               (ReadRow "parts" #("p1"))))))
  (assert (not (isinstance answered Unreachable)) answered))


;; --- 戻りを待っている task を止める(#3557)— 取り消しは片付けまで走り、殺された task は「generator ignored GeneratorExit」を名乗らない ---

(defk stopped-while-coming-back [store kill]
  {:pre [(: store MemoryStore) (: kill bool)] :post [(: % (| str None))] :tags {:context "records" :role "program"}}
  "筋書き: 置き場を止め(戻さない)、戻りを待つ came-back-within の task を立て、待ちに入った後に kill なら Discard・さもなくば Cancel して、
   その task の Wait で上がった例外の型の名を返す。"
  (<- (SetStoreOutage DETAIL))
  (<- waiter (Spawn (came-back-within #("parts") 60.0)))
  (<- (Delay 5.0))
  (if kill (<- (Discard waiter)) (<- (Cancel waiter)))
  (var ended None)
  (try
    (<- (Wait waiter))
    (except [error Exception]
      (:= ended (. (type error) __name__))))
  ended)


(defk unraisable-while-coming-back [kill monkeypatch]
  {:pre [(: kill bool) (: monkeypatch pytest.MonkeyPatch)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "止める筋書きを置き場の memory の handler の上で回し、#(Wait の例外の型の名 CPython が名乗った unraisable の文の列)を返す(走行の終わりに
   GC を回して捨てた generator の閉じを拾う)。"
  (setv seen [])
  (monkeypatch.setattr sys "unraisablehook" (fn [unraisable] (.append seen (.format "{} — {}" unraisable.exc-value unraisable.err-msg))))
  (val store (MemoryStore LAW-SCHEMA))
  (val ended (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)]
                               (stopped-while-coming-back store kill)))))
  (gc.collect)
  #(ended (tuple seen)))


(deftest test-a-cancelled-come-back-wait-ends-cancelled-without-an-unraisable [monkeypatch]
  ;; 取り消し(Cancel)は TaskCancelledError を投げ込み、見張りを止める片付けまで走る — CPython の名乗りは 0。
  (<- seen tuple (unraisable-while-coming-back False monkeypatch))
  (assert (= seen #("TaskCancelledError" #())) seen))


(deftest test-a-discarded-come-back-wait-closes-without-an-unraisable [monkeypatch]
  ;; 失敗ケース: 見張りを止める効果を finally に置くと、捨てた(Discard)待ちの generator を CPython が閉じる時に finally の中で yield し、
  ;; 「generator ignored GeneratorExit」を unraisable として名乗る(#3557 の前の形)。
  (<- seen tuple (unraisable-while-coming-back True monkeypatch))
  (assert (= seen #("TaskCancelledError" #())) seen))
