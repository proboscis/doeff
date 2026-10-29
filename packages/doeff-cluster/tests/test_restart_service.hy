;;; 同じ版の Service を重ねて起こし直す RestartService の判断(declaration_requests.restart-read)と、その書きを受けた coordinator と worker の
;;; 本物の判断の筋書き(card ki-0019be4b9939 — 手で replicas 1→0→1 にする「止めてから起こす」は、旧が止まってから新が起きるまで受ける相手の
;;; 居ない間を作った・2026-10-12 05:18 UTC の常駐の job で 12.6 秒)。
;;;   (a) 判断: handoff で動いている Service には、spec の環境変数 RESTART_MARK を 1 つ進めた書きを、それ以外の spec を読んだままで読んだ版つきに決める。
;;;       Service が無い・台数 0 の取り下げ・入れ替えの形が recreate の時は書かない(理由つきの RestartRefused)。409 の後の読み直しは宣言の書きと同じ。
;;;   (b) 筋書き(tests/test_handoff_deadline.hy の Sim — 本物の coordinator の判断と worker の判断・子 process の中身だけ模す): 印を進める書きは新を旧と
;;;       並べて起こし(書き手の空白が無い)、同じ版のまま起こし直す。失敗ケース: replicas を 0 にして止まったのを見てから 1 に戻す手では、
;;;       書き手の空白ができる。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import doeff_hy._json_value [OpaqueJson])
(import doeff_cluster.shared.intent.cluster_control [RestartRefused])
(import doeff_cluster.shared.protocol.declaration_requests [RESTART-MARK RestartPlan ServiceRead body-of reread-after-conflict restart-read])
(import tests.program_rows [SAMPLE-RUN])
(import tests.test_handoff_deadline [Sim HANDOFF gapless started steps])

;; handoff で動く Service の宣言の spec(環境変数 STEP は宣言の値 — 起こし直しの印はこれに足される)。
(val DECLARED (| HANDOFF {"environ" {"STEP" "1"} "owner" "ops"}))
(val TARGET "/resources/Service/writer-a")


(defk resource-of [spec version]
  {:pre [(: spec dict) (: version int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の資源の口(GET)が返す Service 1 つの形(読んだ版つき)を作るため。"
  {"name" "writer-a" "spec" spec "resourceVersion" version "status" {"ready" "Ready"}})


(defk written-spec-of [plan]
  {:pre [(: plan RestartPlan)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "起こし直しの書きの本文の spec を読むため。"
  (<- body dict (body-of plan.read))
  (get body "spec"))


(deftest test-a-restart-of-a-handoff-service-advances-only-the-restart-mark
  ;; (a) 印が無ければ 1・読んだ版つき・環境変数は宣言の値に印を足した物・それ以外の spec(版・引数・台数・所有者・readiness)は読んだまま。
  (<- resource dict (resource-of DECLARED 7))
  (<- plan (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET resource))
  (assert (isinstance plan RestartPlan) plan)
  (assert (= plan.mark "1") plan)
  (<- body dict (body-of plan.read))
  (assert (= (get body "resourceVersion") 7) body)
  (assert (= (get body "spec") (| DECLARED {"environ" {"STEP" "1" RESTART-MARK "1"}})) body)
  (assert (= plan.read.version 7) plan.read)
  (assert (= (json.loads plan.read.spec.text) DECLARED) plan.read)
  ;; 2 度目は書いた印の次の値(前の印と同じ値は書かない)。
  (<- written dict (resource-of (get body "spec") 8))
  (<- again (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET written))
  (assert (and (isinstance again RestartPlan) (= again.mark "2")) again)
  ;; 印が数字でなければ 1 から。
  (for [odd ["x" "" "1.5" "-1" "٣"]]
    (<- marked dict (resource-of (| DECLARED {"environ" {RESTART-MARK odd}}) 9))
    (<- from-odd (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET marked))
    (assert (and (isinstance from-odd RestartPlan) (= from-odd.mark "1")) #(odd from-odd))))


(deftest test-a-restart-is-refused-without-writing-for-a-missing-service-a-withdrawn-one-and-a-recreate-one
  ;; 書かない 3 つ: Service が無い・台数 0 の取り下げ・入れ替えの形が recreate(欄が無い = recreate)。理由は 1 行で形の名を含む。
  (<- missing (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET None))
  (assert (= missing (RestartRefused :name "writer-a" :reason "Service が無い")) missing)
  (<- stopped dict (resource-of (| DECLARED {"replicas" 0}) 3))
  (<- withdrawn (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET stopped))
  (assert (and (isinstance withdrawn RestartRefused) (in "台数 0" withdrawn.reason)) withdrawn)
  (for [recreate [(dfor #(k v) (.items DECLARED) :if (!= k "update") k v) (| DECLARED {"update" "recreate"})]]
    (<- stays dict (resource-of recreate 3))
    (<- refused (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET stays))
    (assert (and (isinstance refused RestartRefused) (in "handoff" refused.reason) (in "recreate" refused.reason)) refused)))


(deftest test-a-restart-rewritten-after-a-conflict-keeps-the-mark-and-takes-the-new-version
  ;; 読んでから書くまでに変わったのが状態の欄だけなら、同じ書き(同じ印)を今の版で送り直す。spec が変わっていれば(誰かが書いた)送り直さない。
  (<- resource dict (resource-of DECLARED 7))
  (<- plan (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET resource))
  (<- body dict (body-of plan.read))
  (<- stateful dict (resource-of DECLARED 9))
  (<- again (| ServiceRead None) (reread-after-conflict plan.read stateful))
  (assert (is-not again None) again)
  (<- rewritten dict (body-of again))
  (assert (= rewritten {"resourceVersion" 9 "spec" (get body "spec")}) rewritten)
  (<- rewritten-by-someone dict (resource-of (| DECLARED {"revision" "r2"}) 9))
  (<- changed (| ServiceRead None) (reread-after-conflict plan.read rewritten-by-someone))
  (assert (is changed None) changed))


;; --- 筋書き(本物の coordinator の判断と worker の判断・tests/test_handoff_deadline.hy の Sim)-------------------------------------

(defk restart-writer [sim]
  {:pre [(: sim Sim)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "模擬の世界の Service writer-a を、RestartService と同じ判断で起こし直すため: 今の資源を読み、restart-read の書き(読んだ版つきの PUT)を送る。"
  (val current (sim.call "GET" TARGET))
  (<- plan (| RestartPlan RestartRefused) (restart-read "writer-a" TARGET current))
  (assert (isinstance plan RestartPlan) plan)
  (<- body dict (body-of plan.read))
  (sim.call "PUT" TARGET body)
  None)


(defk overlapped-beats [sim]
  {:pre [(: sim Sim)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "動いている process が 2 つ以上だった拍の数(旧と新が並んだ拍)を数えるため。"
  (len (lfor row sim.log :if (>= (get row 1) 2) row)))


(deftest test-restarting-a-handoff-service-starts-the-new-process-beside-the-old-one
  ;; (b) 印を進める書きで、同じ版 r1 のまま新が旧と並んで起きて(並んだ拍が在る)、新が Ready になった後に旧が止まる — 書き手の空白が無い。
  ;; 新旧の違いは環境変数の印だけ(spec の指紋が違う)。
  (val sim (Sim DECLARED))
  (<- (steps sim 12))
  (assert (= (get (sim.status) "ready") "Ready") sim.log)
  (val first (get (sim.live "r1") 0))
  (assert (= (dict first.spec.environ) {"STEP" "1"}) first)
  (<- (restart-writer sim))
  (<- (steps sim 20))
  (assert (= (! (started sim "r1")) 2) sim.starts)
  (assert (> (! (overlapped-beats sim)) 0) sim.log)
  (assert (! (gapless sim)) sim.log)
  (val live (sim.live "r1"))
  (assert (= (len live) 1) sim.processes)
  (assert (!= (. (get live 0) pid) first.pid) live)
  (assert (= (dict (. (get live 0) spec environ)) {"STEP" "1" RESTART-MARK "1"}) live)
  (assert (= (get (sim.status) "ready") "Ready") (sim.status)))


(deftest test-stopping-then-starting-a-handoff-service-leaves-the-writer-gap
  ;; 失敗ケース: 今の手(台数を 0 にして止まったのを見てから 1 に戻す)では、旧が止まってから新が起きるまで動く process が 0 の拍ができ、
  ;; 並んだ拍は無い — 上の筋書きが「空白が無い」を赤にできることの確かめ。
  (val sim (Sim DECLARED))
  (<- (steps sim 12))
  (sim.redeclare (| DECLARED {"replicas" 0}))
  (<- (steps sim 8))
  (assert (= (sim.live "r1") []) sim.processes)
  (sim.redeclare DECLARED)
  (<- (steps sim 20))
  (assert (= (len (sim.live "r1")) 1) sim.processes)
  (assert (= (! (started sim "r1")) 2) sim.starts)
  (assert (= (! (overlapped-beats sim)) 0) sim.log)
  (assert (not (! (gapless sim))) sim.log))
