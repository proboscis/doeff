;; drain 中の worker は、drain の間に宣言し直された Service の新しい版を受けない(#3684)。
;;   直す前は、drain 中の worker が新しい spec を受けて、入れ替え(handoff)の job ではその worker の上に新しい版を起こし(移し先で新しい版が
;;   Ready になると捨てられる — 無駄な準備と起動)、入れ替えでない(recreate)job では旧い版を止めてから起こし直していた(止まりも出る)。
;;   直した後は、heartbeat の返事の draining が真の間、返事の job の宣言が版を据え置く印(JobSpec.hold-version)を持ち、worker の判断
;;   (worker_policy.plan-job)は動いている旧い版をそのまま動かす。宣言から消えた job は今までどおり止め、drain が解けた次の拍で普通の
;;   入れ替えへ進む。系は本物の coordinator と本物の worker の判断の上で、仮想の時計で走らせる(sim-cluster)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])
(import httpx)
(import pytest)
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.sim.local [sim-cluster SimWorker DrainWorker Redeclare ProcessesOf])
(import doeff_cluster.worker.intent.worker_model [DesiredUnreadable])
(import doeff_cluster.worker.protocol.declared [DeclaredReplyMalformed declared-reply-of-json])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons beacons-v2 handoff-beacons handoff-beacons-v2])
(import tests.link_rig [LinkRig])


(defrecord HeldDrain
  "drain の間に宣言し直した筋書きの読み: drain の頼みの答え・drain した worker の名・drain を頼んだ刻・drain の前・途中・後の
   beacon の process の列。"
  (#^ dict answer)
  (#^ str host)
  (#^ int drained-at)
  (#^ tuple before)
  (#^ tuple mid)
  (#^ tuple after))


(defk drain-then-redeclare [settle-seconds ttl-seconds next-system mid-seconds after-seconds]
  {:pre [(: settle-seconds float) (: ttl-seconds float) (: next-system System) (: mid-seconds float) (: after-seconds float)]
   :post [(: % HeldDrain)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: settle 秒待って beacon の worker の drain を(期限 ttl 秒で)頼み、0.5 秒後に next-system を宣言し直し、mid 秒後と、さらに
   after 秒後に beacon の process を読む。"
  (<- (Delay settle-seconds))
  (<- before tuple (ProcessesOf "beacon"))
  (val host (. (get before 0) worker))
  (<- drained-at int (now-epoch-ms))
  (<- answer dict (DrainWorker host ttl-seconds))
  (<- (Delay 0.5))
  (<- (Redeclare next-system))
  (<- (Delay mid-seconds))
  (<- mid tuple (ProcessesOf "beacon"))
  (<- (Delay after-seconds))
  (<- after tuple (ProcessesOf "beacon"))
  (HeldDrain :answer answer :host host :drained-at drained-at :before before :mid mid :after after))


(defk handoff-workers [prepare-seconds]
  {:pre [(: prepare-seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "能力 cluster-net を持つ worker 2 台(どちらもコードの木の準備に prepare 秒かかる — drain 中の worker が新しい版を起こす窓を広げる)。"
  #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0 :prepare-seconds prepare-seconds)
    (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0 :prepare-seconds prepare-seconds)))


(defk assert-the-drained-worker-kept-the-old-version [seen]
  {:pre [(: seen HeldDrain)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "移せる入れ替えの job の drain の読みが条を満たすか確かめるため: drain 中の worker に新しい process が 0・旧い版は移し先の新しい版が
   起きた後まで動き続けて止まる・移し先の新しい版(旧と違う指紋)が動いている。"
  (assert (= (get seen.answer "status") 200) seen.answer)
  (val old (get seen.before 0))
  (val fresh-on-host (lfor p seen.after :if (and (= p.worker seen.host) (>= p.started-ms seen.drained-at)) p))
  (assert (= fresh-on-host []) #(seen.host fresh-on-host))
  (val old-now (get (lfor p seen.after :if (= p.instance old.instance) p) 0))
  (val moved (lfor p seen.after :if (and (!= p.worker seen.host) (is p.exit-code None)) p))
  (assert (= (len moved) 1) seen.after)
  (val new (get moved 0))
  (assert (!= new.spec-hash old.spec-hash) #(old new))
  (assert (= old-now.exit-code -15) old-now)
  (assert (>= old-now.ended-ms new.started-ms) #(old-now new))
  True)


(deftest test-a-draining-worker-does-not-start-the-new-version-of-a-movable-handoff-service
  ;; 失敗ケース A(#3684): 移せる入れ替えの job・どちらの worker も準備に 10 秒。drain を頼み 0.5 秒後に版 2 を宣言し直す。直す前は
  ;; drain 中の worker も版 2 を準備して起こし、移し先の版 2 が Ready になった後で捨てた。直した後は drain 中の worker に新しい process は
  ;; 起きず、旧い版(版 1)は移し先の版 2 が起きた後まで動き続ける。
  (<- workers tuple (handoff-workers 10.0))
  (<- seen HeldDrain (sim-cluster (handoff-beacons sim-foundation)
                                  (drain-then-redeclare 20.0 120.0 (handoff-beacons-v2 sim-foundation) 0.0 40.0)
                                  :workers workers))
  (<- (assert-the-drained-worker-kept-the-old-version seen)))


(deftest test-a-draining-worker-does-not-start-the-new-version-even-when-preparing-is-instant
  ;; 失敗ケース A0(#3684): A と同じで準備 0 秒。直す前は drain 中の worker が版 2 を次の拍で起こした。
  (<- workers tuple (handoff-workers 0.0))
  (<- seen HeldDrain (sim-cluster (handoff-beacons sim-foundation)
                                  (drain-then-redeclare 10.0 120.0 (handoff-beacons-v2 sim-foundation) 0.0 25.0)
                                  :workers workers))
  (<- (assert-the-drained-worker-kept-the-old-version seen)))


(deftest test-an-unmovable-job-keeps-its-old-version-until-the-drain-expires-then-is-replaced
  ;; 失敗ケース B(#3684): 他に能力の合う worker が無い入れ替えでない(recreate)job・drain の期限 20 秒。drain を頼み 0.5 秒後に版 2 を
  ;; 宣言し直す。直す前は drain 中の worker が版 1 を止めて版 2 を起こし直した(10 秒後の読みで版 1 は終わり版 2 が動いていた)。
  ;; 直した後は期限まで版 1 が動き続け、期限が過ぎて返事の draining が偽に戻った後の拍で、普通の入れ替え(止めてから起こす)で版 2 へ移る。
  (val workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)))
  (<- seen HeldDrain (sim-cluster (beacons sim-foundation)
                                  (drain-then-redeclare 10.0 20.0 (beacons-v2 sim-foundation) 10.0 30.0)
                                  :workers workers))
  (assert (= (get seen.answer "status") 200) seen.answer)
  (val old (get seen.before 0))
  ;; 期限の前(drain を頼んで約 10.5 秒後): 動いているのは版 1 の process だけ。
  (assert (= (lfor p seen.mid p.instance) [old.instance]) seen.mid)
  (assert (is (. (get seen.mid 0) exit-code) None) seen.mid)
  ;; 期限の後: 版 1 は期限の後に止まり、同じ worker の上で版 2(旧と違う指紋)が期限の後に起きて動いている。
  (val expires (+ seen.drained-at 20000))
  (val old-now (get (lfor p seen.after :if (= p.instance old.instance) p) 0))
  (assert (= old-now.exit-code -15) old-now)
  (assert (>= old-now.ended-ms expires) #(expires old-now))
  (val live (lfor p seen.after :if (is p.exit-code None) p))
  (assert (= (len live) 1) seen.after)
  (val new (get live 0))
  (assert (= new.worker "w1") new)
  (assert (!= new.spec-hash old.spec-hash) #(old new))
  (assert (>= new.started-ms expires) #(expires new)))


(defrecord BadReply
  "形の違う heartbeat の返事 1 つと、読みが名指すはずの欄(reply = 返事の本文・field = 欄の場所)。"
  (#^ (get dict #(str object)) reply)
  (#^ str field))


(val JOB-ROW {"name" "web" "entry" "m" "revision" "r2"})
(val BAD-REPLIES #((BadReply :reply {"jobs" [JOB-ROW]} :field "draining")
                   (BadReply :reply {"jobs" [JOB-ROW] "draining" "yes"} :field "draining")
                   (BadReply :reply {"draining" False} :field "jobs")
                   (BadReply :reply {"jobs" {"web" JOB-ROW} "draining" False} :field "jobs")
                   (BadReply :reply {"jobs" [1] "draining" False} :field "jobs.0")))


(deftest test-a-reply-without-draining-is-refused-by-name
  ;; 失敗ケース C(#3684 の読みの条件): 返事の draining が無い・真偽でない、jobs が無い・列でない・行が写像でない時、読み(JSON の境界
  ;; declared-reply-of-json)は黙って偽や空で埋めず、DeclaredReplyMalformed で欄を名指して落ちる。埋めると drain 中の worker が新しい版を
  ;; 起こす(直す前の読みは欄の無い draining を偽と読んだ)。
  (for [bad BAD-REPLIES]
    (with [caught (pytest.raises DeclaredReplyMalformed)]
      (! (declared-reply-of-json bad.reply)))
    (assert (in (+ bad.field " ") (str caught.value)) #(bad (str caught.value)))))


(deftest test-the-worker-link-does-not-take-the-jobs-of-a-reply-without-draining [tmp-path]
  ;; 失敗ケース D(#3684 の読みの条件・本番の coordinator への口): draining の無い返事を受けた拍は、返事の job を宣言にしない —
  ;; 「読めない」(DesiredUnreadable — 理由に DeclaredReplyMalformed)を返し、拍は前の宣言のまま動く。直す前の口は欄の無い返事を
  ;; 「drain 中でない」と読み、返事の job をそのまま宣言にした。
  (val handle (fn #^ httpx.Response [#^ httpx.Request request]
                (httpx.Response 200 :json {"jobs" [JOB-ROW] "tasks" [] "warm" []})))
  (val link (LinkRig "http://coord" "w" #() 1 0 20000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (val seen (.poll link))
  (assert (isinstance seen DesiredUnreadable) seen)
  (assert (in "DeclaredReplyMalformed" seen.reason) seen.reason)
  (assert (= link.state.last-jobs #()) link.state.last-jobs))
