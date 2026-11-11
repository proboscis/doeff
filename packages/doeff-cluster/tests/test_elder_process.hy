;; 世代の受け渡しの間の Service の status.process(2026-11-02 00:20〜00:24 UTC の node-h): 同じ名の新しい世代が、前の世代がまだ走っている
;; 間に名乗った(overlapsElder)後、常駐の job は前の世代の process が動かし続ける(新しい世代は elder-runs で待つ)。直す前は、coordinator が
;; worker の名ごとに最新の報告 1 つしか持たず、前の世代の heartbeat の job の行を捨てたので、受け渡しの 3 分半の間 status.process は
;; 新しい世代の pid の無い行になり、process の木を pid から読む外の測り手が job を測れなかった。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import tests.room_rows [ROOM-JSONS])
(import tests.test_drain [Coord service K3S T])


(defn #^ dict generation-beat [#^ Coord c #^ str boot #^ list statuses #^ bool [overlaps False] #^ (| int None) [boot-at None]]  ; cannot be a defk: 模擬の世界の method を呼ぶ(状態を進める)
  "worker node-a の世代 boot の heartbeat(overlaps = 前の世代がまだ走っている間に起きたと名乗る・boot-at = 名乗る起動の刻)。"
  (c.call "POST" "/heartbeat" (| {"name" "node-a" "provides" K3S "measured" (get ROOM-JSONS 10) "versions" {} "boot" boot
                                  "statuses" statuses "stopping" False}
                                 (if overlaps {"overlapsElder" True} {})
                                 (if (is boot-at None) {} {"bootAt" boot-at}))
          :actor None))


(defn #^ dict service-status [#^ Coord c #^ str name]  ; cannot be a defk: 模擬の世界の method を呼ぶ
  (get (c.call "GET" (+ "/resources/Service/" name)) "status"))


(defk recreate-job-on-old []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "常駐の job r(recreate)が node-a の前の世代(boot old)で pid 101 で動き、新しい世代(boot new)が重なりを名乗って elder-runs で待つ。
   答え = 模擬の世界・前の世代の行・新しい世代の待ちの行。"
  (val c (Coord #("node-a")))
  (c.beat "node-a" :boot "old")
  (c.call "POST" "/resources/Service" {"name" "r" "spec" (! (service {"update" "recreate"}))} :expect 201)
  (val old-row (| (c.row "node-a" "r") {"pid" 101}))
  (generation-beat c "old" [old-row])
  (c.advance 1)
  (assert (= (get (service-status c "r") "process" "pid") 101))
  (setv (get c.boots "node-a") "new")
  (val waiting {"name" "r" "phase" "elder-runs" "desiredRevision" (get old-row "desiredRevision")})
  ;; 新しい世代は起動の刻を名乗る(前の世代の process の終わりを、知れた刻としてこの刻までと数える — #3672)。
  (generation-beat c "new" [waiting] :overlaps True :boot-at (- c.now 2000))
  #(c old-row waiting))


(deftest test-the-service-process-is-the-old-generation-row-while-it-still-runs-the-job
  ;; 失敗ケース(00:21Z の毎時の測り): 新しい世代が名乗った後、前の世代が r を pid 101 で動かしている間、status.process は前の世代の
  ;; 行(pid 101・boot old・elder 真)。前の世代の次の heartbeat の後も同じ。生きている process の版の列にも前の世代の process が載る。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (var process (get (service-status c "r") "process"))
  (assert (= #((get process "pid") (get process "phase") (get process "boot") (get process "elder")) #(101 "running" "old" True)) process)
  (c.advance 5)
  (generation-beat c "old" [old-row])
  (generation-beat c "new" [waiting] :overlaps True)
  (:= process (get (service-status c "r") "process"))
  (assert (= #((get process "pid") (get process "boot") (get process "elder")) #(101 "old" True)) process)
  (assert (= (get (service-status c "r") "version" "running") [{"revision" (get old-row "runningRevision") "retired" False}])
          (get (service-status c "r") "version")))


(deftest test-the-service-process-moves-to-the-new-generation-once-it-runs-and-the-old-row-is-dropped-with-its-run
  ;; 新しい世代が r を running(pid 202)と報告した後は、前の世代がまだ報告していても今の世代の行。前の世代が r を止め終えて組
  ;; (SupersededRun)が外れたら、前の世代の行も表から外れる(覚える数は組と同じ)。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (c.call "POST" "/workers/node-a/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@node-a")
  (generation-beat c "old" [old-row])
  (assert (= (get (service-status c "r") "process" "pid") 101))
  ;; 前の世代が r を止め終えた(running の行が無い)→ 組が外れ、前の世代の行も外れる。
  (c.advance 1)
  (generation-beat c "old" [])
  (assert (= c.state.superseded-runs #()) c.state.superseded-runs)
  (assert (= (list (.keys c.state.observations.elder-statuses)) []) "組の外れた世代の行は残らない")
  (val started (| (c.row "node-a" "r") {"pid" 202 "instance" "node-a-r-new"}))
  (generation-beat c "new" [started] :overlaps True)
  (val process (get (service-status c "r") "process"))
  (assert (= #((get process "pid") (get process "boot") (get process "elder")) #(202 "new" False)) process))


(deftest test-a-running-new-generation-row-wins-over-the-old-generation-row
  ;; 前の世代の組が残っている間でも、今の世代が running と報告した行が job を動かしている process の行。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (val started (| (c.row "node-a" "r") {"pid" 202 "instance" "node-a-r-new"}))
  (generation-beat c "new" [started] :overlaps True)
  (generation-beat c "old" [old-row])
  (val process (get (service-status c "r") "process"))
  (assert (= #((get process "pid") (get process "boot") (get process "elder")) #(202 "new" False)) process))


(deftest test-a-silent-old-generation-row-is-not-shown
  ;; 前の世代が lease の間黙った(Pod が消えた)→ その行は出さず、今の世代の行(pid 無し)に戻る。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (for [_ (range (+ (// T.lease-ms 5000) 2))]
    (c.advance 5)
    (generation-beat c "new" [waiting] :overlaps True))
  (val process (get (service-status c "r") "process"))
  (assert (= #((get process "pid") (get process "phase") (get process "boot") (get process "elder")) #(None "elder-runs" "new" False))
          process))


(deftest test-last-exit-advances-across-the-handover-and-never-goes-back
  ;; #3672 の使い手の読み: status.lastExitAtMs と status.process.lastExitAtMs は、受け渡しの間も戻らず、前の世代の process の終わり
  ;; (前の世代が報告した終わりの刻)と新しい世代の起動の後に進む。受け渡しの間の process の行(前の世代の行)は今の世代の行と同じ
  ;; 知れた刻を持つ。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (val during (service-status c "r"))
  (val known (get during "lastExitAtMs"))
  (assert (= (get during "process" "pid") 101) during)
  (assert (= (get during "process" "lastExitAtMs") known) during)
  (c.call "POST" "/workers/node-a/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@node-a")
  (c.advance 10)
  (val exited-at (- c.now 1000))
  (generation-beat c "old" [(| old-row {"phase" "stopped" "pid" None "lastExitCode" 0 "lastExitAtMs" exited-at})])
  (val started (| (c.row "node-a" "r") {"pid" 202 "instance" "node-a-r-new"}))
  (generation-beat c "new" [started] :overlaps True)
  (val after (service-status c "r"))
  (assert (= (get after "process" "pid") 202) after)
  (assert (= (get after "lastExitAtMs") exited-at) after)
  (assert (= (get after "process" "lastExitAtMs") exited-at) after)
  (assert (> exited-at known) #(known exited-at)))


(defn #^ (| bool None) elder-mark [#^ dict reply]
  "新しい世代への返事の r の印 elderRuns(印が無ければ None — worker は起こす)。"
  (.get (next (gfor j (get reply "jobs") :if (= (get j "name") "r") j)) "elderRuns"))


(deftest test-the-new-generation-waits-while-the-old-generation-is-still-stopping-the-job
  ;; 失敗ケース(11-07 06:42 JST の node-c): 前の世代が r を止め始めた(stopping — 止めの合図を送り、まだ刈っていない)だけで、新しい世代への
  ;; 返事から印 elderRuns が外れた。新しい世代は 2 秒後に r を起こし、前の世代の process が持ったままの port の bind で落ちた(前の世代の
  ;; 刈りはその 12 秒後)。印は、前の世代が r の process を刈り終える(報告から r の行が消えるか、process の無い行になる)まで外さない。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (c.call "POST" "/workers/node-a/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@node-a")
  (c.advance 1)
  (generation-beat c "old" [(| old-row {"phase" "stopping"})])
  (assert (elder-mark (generation-beat c "new" [waiting] :overlaps True)) "止めの途中は前の世代が port を持っている")
  (c.advance 6)
  (generation-beat c "old" [(| old-row {"phase" "stop-unconfirmed"})])
  (assert (elder-mark (generation-beat c "new" [waiting] :overlaps True)) "止めを確かめられない間も持っている")
  (c.advance 6)
  (generation-beat c "old" [(| old-row {"phase" "stopped" "pid" None "lastExitCode" 0 "lastExitAtMs" (- c.now 500)})])
  (assert (is (elder-mark (generation-beat c "new" [waiting] :overlaps True)) None) "刈り終えたら起こす"))


(deftest test-the-new-generation-waits-out-the-old-generation-self-stop-while-the-old-generation-is-silent
  ;; 失敗ケース(同じ朝の node-c — 両方の世代が coordinator に届かなくなった): 前の世代は途絶の柵(fence)で自分の job を止め始めるが、
  ;; その間は報告が届かない。沈黙が生死の窓(lease)を越えた所で印を外すと、新しい世代は前の世代の止め(と刈り)より先に起こす。
  ;; 沈黙した世代が自分で止まり切るまでの上限は移し替えの期限(reassign-after-ms — 条 C4 timing-outlasts-the-self-stop)なので、
  ;; 印はそれまで外さない。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (c.advance (// (+ T.lease-ms T.fence-ms) 1000))
  (assert (elder-mark (generation-beat c "new" [waiting] :overlaps True)) "柵の後の止めの間は前の世代が port を持ちうる")
  (c.advance (// T.reassign-after-ms 1000))
  (assert (is (elder-mark (generation-beat c "new" [waiting] :overlaps True)) None) "自分で止まり切る上限を過ぎたら起こす"))
