;; 世代の受け渡しの間の Service の status.process(2026-11-02 00:20〜00:24 UTC の zeus): 同じ名の新しい世代が、前の世代がまだ走っている
;; 間に名乗った(overlapsElder)後、常駐の job は前の世代の process が動かし続ける(新しい世代は elder-runs で待つ)。直す前は、coordinator が
;; worker の名ごとに最新の報告 1 つしか持たず、前の世代の heartbeat の job の行を捨てたので、受け渡しの 3 分半の間 status.process は
;; 新しい世代の pid の無い行になり、process の木を pid から読む外の測り手が job を測れなかった。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import tests.room_rows [ROOM-JSONS])
(import tests.test_drain [Coord service K3S T])


(defn #^ dict generation-beat [#^ Coord c #^ str boot #^ list statuses #^ bool [overlaps False] #^ (| int None) [boot-at None]]  ; defk にできない: 模擬の世界の method を呼ぶ(状態を進める)
  "worker atlas の世代 boot の heartbeat(overlaps = 前の世代がまだ走っている間に起きたと名乗る・boot-at = 名乗る起動の刻)。"
  (c.call "POST" "/heartbeat" (| {"name" "atlas" "provides" K3S "measured" (get ROOM-JSONS 10) "versions" {} "boot" boot
                                  "statuses" statuses "stopping" False}
                                 (if overlaps {"overlapsElder" True} {})
                                 (if (is boot-at None) {} {"bootAt" boot-at}))
          :actor None))


(defn #^ dict service-status [#^ Coord c #^ str name]  ; defk にできない: 模擬の世界の method を呼ぶ
  (get (c.call "GET" (+ "/resources/Service/" name)) "status"))


(defk recreate-job-on-old []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "常駐の job r(recreate)が atlas の前の世代(boot old)で pid 101 で動き、新しい世代(boot new)が重なりを名乗って elder-runs で待つ。
   答え = 模擬の世界・前の世代の行・新しい世代の待ちの行。"
  (val c (Coord #("atlas")))
  (c.beat "atlas" :boot "old")
  (c.call "POST" "/resources/Service" {"name" "r" "spec" (! (service {"update" "recreate"}))} :expect 201)
  (val old-row (| (c.row "atlas" "r") {"pid" 101}))
  (generation-beat c "old" [old-row])
  (c.advance 1)
  (assert (= (get (service-status c "r") "process" "pid") 101))
  (setv (get c.boots "atlas") "new")
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
  (c.call "POST" "/workers/atlas/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@atlas")
  (generation-beat c "old" [old-row])
  (assert (= (get (service-status c "r") "process" "pid") 101))
  ;; 前の世代が r を止め終えた(running の行が無い)→ 組が外れ、前の世代の行も外れる。
  (c.advance 1)
  (generation-beat c "old" [])
  (assert (= c.state.superseded-runs #()) c.state.superseded-runs)
  (assert (= (list (.keys c.state.observations.elder-statuses)) []) "組の外れた世代の行は残らない")
  (val started (| (c.row "atlas" "r") {"pid" 202 "instance" "atlas-r-new"}))
  (generation-beat c "new" [started] :overlaps True)
  (val process (get (service-status c "r") "process"))
  (assert (= #((get process "pid") (get process "boot") (get process "elder")) #(202 "new" False)) process))


(deftest test-a-running-new-generation-row-wins-over-the-old-generation-row
  ;; 前の世代の組が残っている間でも、今の世代が running と報告した行が job を動かしている process の行。
  (<- #(c old-row waiting) (recreate-job-on-old))
  (val started (| (c.row "atlas" "r") {"pid" 202 "instance" "atlas-r-new"}))
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
  (c.call "POST" "/workers/atlas/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@atlas")
  (c.advance 10)
  (val exited-at (- c.now 1000))
  (generation-beat c "old" [(| old-row {"phase" "stopped" "pid" None "lastExitCode" 0 "lastExitAtMs" exited-at})])
  (val started (| (c.row "atlas" "r") {"pid" 202 "instance" "atlas-r-new"}))
  (generation-beat c "new" [started] :overlaps True)
  (val after (service-status c "r"))
  (assert (= (get after "process" "pid") 202) after)
  (assert (= (get after "lastExitAtMs") exited-at) after)
  (assert (= (get after "process" "lastExitAtMs") exited-at) after)
  (assert (> exited-at known) #(known exited-at)))
