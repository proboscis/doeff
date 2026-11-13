;; 同じ名の worker の世代の入れ替え(2026-11-11 — 44 分の沈黙): 置き場の dir を移した新しい世代(Pod)は、前の世代の dir の錠を見ないので
;; 重なり(overlapsElder)を名乗らなかった。coordinator は新しい世代の名乗りを前の世代の終わりと読み、
;;   1. 新しい世代が準備の上限で落ちて能力(job の needs)が足りないのに、常駐の job の置き先を名から外し、まだ動いている前の世代の
;;      job を止めさせた(前の世代への返事から job が消えた)。
;;   2. 新しい世代の Pod が消えた後も、生きている前の世代の heartbeat を起動の時刻の古さで名乗りとして受けず、名が沈黙した。
;; 直した後: 新しい世代が名乗った後に前の世代の heartbeat が届けば重なりと判じ、新しい世代が job を受け取れる(能力と準備を揃えて
;; elder-runs を報告する)まで前の世代に残す。名乗りから前の世代の次の heartbeat までは、前の世代の最新の報告で置き先を保つ。今の
;; 世代が lease を越えて黙ったら、届いた前の世代の heartbeat を今の世代として受け直す。前の世代の heartbeat が届かない(突然止まった
;; worker の作り直し)時は今どおり、新しい世代がすぐ job を起こす。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import tests.room_rows [ROOM-JSONS])
(import tests.test_drain [Coord service K3S T])

(val AGENT ["cluster-net" "agent"])
;; 世代ごとの起動の刻(heartbeat の bootAt — 新しい世代ほど大きい)。
(val BOOT-AT {"old" 1000 "new" 2000})


(defn #^ dict pod-beat [#^ Coord c #^ str boot #^ str pod-ip #^ list provides #^ list statuses]  ; cannot be a defk: 模擬の世界の method を呼ぶ(状態を進める)
  "worker node-a の世代 boot(Pod の IP pod-ip・能力 provides・起動の刻 BOOT-AT)の heartbeat。重なり(overlapsElder)は名乗らない。
   答え = 返事の本文。"
  (c.call "POST" "/heartbeat" {"name" "node-a" "provides" provides "measured" (get ROOM-JSONS 10) "versions" {} "boot" boot
                               "podIp" pod-ip "bootAt" (get BOOT-AT boot) "statuses" statuses "stopping" False}
          :actor None))


(defn #^ (| dict None) job-in [#^ dict reply #^ str name]
  "返事の jobs のうち名 name の job(載っていなければ None)。"
  (next (gfor j (get reply "jobs") :if (= (get j "name") name) j) None))


(defk agent-job-on-old []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "常駐の job r(recreate・needs に agent)が node-a の前の世代(boot old・Pod の IP 10.0.0.1)で pid 101 で動いている。
   答え = 模擬の世界・前の世代の行。"
  (val c (Coord #("node-a")))
  (pod-beat c "old" "10.0.0.1" AGENT [])
  (c.call "POST" "/resources/Service" {"name" "r" "spec" (! (service {"update" "recreate" "needs" AGENT}))} :expect 201)
  (assert (= (c.placed "r") "node-a"))
  (val old-row (| (c.row "node-a" "r") {"pid" 101}))
  (pod-beat c "old" "10.0.0.1" AGENT [old-row])
  (c.advance 1)
  #(c old-row))


(deftest test-a-new-pod-without-the-needed-capability-leaves-the-job-on-the-old-generation
  ;; 失敗ケース 1(能力が足りない): 新しい世代(別の Pod)が agent を持たずに名乗った。直す前は r の置き先が外れ、前の世代への返事から
  ;; r が消えた(前の世代は r を宣言に無い job として止めた)。直した後は置き先が node-a に残り、前の世代が r を動かし続け、新しい世代
  ;; には r を載せず、新しい世代は受け渡しを待つ(Pod の ready の file を書かない)。
  (<- #(c old-row) (agent-job-on-old))
  (setv (get c.boots "node-a") "new")
  (var fresh (pod-beat c "new" "10.0.0.2" K3S []))
  (assert (is (job-in fresh "r") None) "前の世代の次の heartbeat の前も、能力の足りない新しい世代には r を載せない")
  (assert (= (c.placed "r") "node-a") "前の世代の次の heartbeat の前も置き先を保つ")
  (c.advance 1)
  (val elder (pod-beat c "old" "10.0.0.1" AGENT [old-row]))
  (assert (.get elder "superseded") elder)
  (assert (= (c.placed "r") "node-a") (.row c.state.placements "r"))
  (assert (is-not (job-in elder "r") None) "前の世代は r を動かし続ける")
  (:= fresh (pod-beat c "new" "10.0.0.2" K3S []))
  (assert (is (job-in fresh "r") None) "能力の足りない新しい世代には r を載せない")
  (assert (.get fresh "handoverPending") "新しい世代は r を受け取れるまで ready を書かない")
  ;; 新しい世代が agent を持ったら、r を印 elderRuns 付きで受け取り始める(前の世代はまだ動かす)。
  (:= fresh (pod-beat c "new" "10.0.0.2" AGENT []))
  (assert (.get (job-in fresh "r") "elderRuns" False) fresh)
  (assert (is-not (job-in (pod-beat c "old" "10.0.0.1" AGENT [old-row]) "r") None)))


(deftest test-a-new-pod-still-preparing-waits-while-the-old-generation-runs-the-job
  ;; 失敗ケース 2(準備中): 新しい世代(別の Pod)は能力を持つが、まだ r を準備している。直す前は前の世代への返事から r が消え(前の世代は
  ;; r を止める)、新しい世代への返事に印 elderRuns が付かなかった。直した後は、前の世代の heartbeat が届いた時から前の世代が r を
  ;; 動かし続け、新しい世代は elderRuns で準備だけをして待ち、受け渡しを待つ(ready を書かない)。
  (<- #(c old-row) (agent-job-on-old))
  (setv (get c.boots "node-a") "new")
  (val preparing {"name" "r" "phase" "preparing" "desiredRevision" (get old-row "desiredRevision")})
  (pod-beat c "new" "10.0.0.2" AGENT [preparing])
  (c.advance 1)
  (val elder (pod-beat c "old" "10.0.0.1" AGENT [old-row]))
  (assert (is-not (job-in elder "r") None) "前の世代は r を動かし続ける")
  (assert (= (c.placed "r") "node-a"))
  (val fresh (pod-beat c "new" "10.0.0.2" AGENT [preparing]))
  (assert (.get (job-in fresh "r") "elderRuns" False) fresh)
  (assert (.get fresh "handoverPending") fresh))


(deftest test-the-old-generation-is-taken-back-after-the-new-pod-vanished
  ;; 失敗ケース 3(消えた新しい世代): 新しい世代の Pod が消えて heartbeat が lease を越えて途絶えた。直す前は前の世代の heartbeat を
  ;; 古い世代(起動の時刻が古い)として受けず、名は退いた世代への返事(superseded)のまま沈黙した。直した後は前の世代を今の世代として
  ;; 受け直し、r の置き先は前の世代に戻る(返事は superseded でなく、r を載せる)。
  (<- #(c old-row) (agent-job-on-old))
  (setv (get c.boots "node-a") "new")
  (pod-beat c "new" "10.0.0.2" K3S [])
  (c.advance 1)
  (assert (.get (pod-beat c "old" "10.0.0.1" AGENT [old-row]) "superseded"))
  ;; 新しい世代は黙った。前の世代だけが報告を続ける。
  (var reply None)
  (for [_ (range (+ (// T.lease-ms 5000) 2))]
    (c.advance 5)
    (:= reply (pod-beat c "old" "10.0.0.1" AGENT [old-row])))
  (assert (not (.get reply "superseded")) reply)
  (assert (= (. (.row c.state.workers "node-a") boot) "old"))
  (assert (in "new" (. (.row c.state.workers "node-a") retired)))
  (assert (= (c.placed "r") "node-a"))
  (assert (is-not (job-in reply "r") None) reply)
  (assert (not (.get (job-in reply "r") "elderRuns" False)) "今の世代に戻した世代は r を待たずに動かす")
  (assert (= c.state.superseded-runs #()) "今の世代に戻した世代の組は残らない")
  ;; 消えた世代が戻れば、起動の時刻で新しい世代として名乗り直す(古い世代の扱いで断らない)。
  (setv (get c.boots "node-a") "new")
  (pod-beat c "new" "10.0.0.2" AGENT [])
  (assert (= (. (.row c.state.workers "node-a") boot) "new")))


(deftest test-a-new-generation-after-an-abrupt-stop-starts-the-job-without-waiting
  ;; 境: 前の世代が突然止まって作り直された(新しい世代が名乗った後に前の世代の heartbeat が届かない)時は、前の世代の process が
  ;; 終わった印のまま(#3770)。新しい世代へは r を印 elderRuns 無しで載せる(前の世代の終わりを待たずに起こす — 今どおり)。
  (<- #(c old-row) (agent-job-on-old))
  (setv (get c.boots "node-a") "new")
  (var fresh (pod-beat c "new" "10.0.0.2" AGENT []))
  (assert (is-not (job-in fresh "r") None) fresh)
  (assert (not (.get (job-in fresh "r") "elderRuns" False)) fresh)
  (assert (not (.get fresh "handoverPending")) fresh)
  (c.advance 5)
  (:= fresh (pod-beat c "new" "10.0.0.2" AGENT []))
  (assert (not (.get (job-in fresh "r") "elderRuns" False)) fresh))
