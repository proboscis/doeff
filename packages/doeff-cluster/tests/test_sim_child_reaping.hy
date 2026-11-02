;; 模擬の子 process の答え手(_sim/child_processes)の片づけ(#2940 の条 C4b — 本番の入れ物 shim の子孫の引き取りと SIGKILL の写し):
;; job の process が StartProcess で起こした子と、子が別の session に切り離した孫は、(1) job の process が退く時(版の入れ替えで旧い
;; process が抜ける)と (2) worker の世代が終わる時(StopElder・死んだ worker・止め)に -9 で終わり、結果の file を書かない。
;; 引き取りを外した変種(SimChildren の adoption 偽)は、job の process の終わりの後も切り離した孫を残すが、世代の終わりでは残さない
;; (Pod ごと消える)。
;;
;; 筋書きの service は tests/fixtures/descendant_programs.hy の parent(子 spawner → 別の session の孫 sleeper-grandchild)。
;; 失敗ケース: 片づけの無い答え手(子孫を世界が終わらせない)では、旧い process の子が SPAWNER-SECONDS の後に結果の file を書き、
;; 孫は走り続ける — 各検の「-9 で終わった」と「結果の file が無い」が名指す。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_core_effects [ReadText FileFailed])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [SimOutside])
(import doeff_cluster [sim-cluster SimWorker SimProcess SimDescendant ProcessesOf DescendantsOf Redeclare KillWorker StopWorker RollWorker
                       StopElder])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.descendant_programs [descendant-hosts descendant-hosts-v2 descendant-hosts-off descendant-outside result-path
                                            SPAWNER-SECONDS])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"])))


(defrecord RolloutSeen
  "筋書き (1) が読んだ姿: old = 旧い process・under = その子孫・spawned / slept = 子と孫が旧い process の下で結果の file を書いたか。"
  (#^ SimProcess old)
  (#^ (get tuple #(SimDescendant ...)) under)
  (#^ bool spawned)
  (#^ bool slept))


(defrecord GenerationSeen
  "筋書き (2) が読んだ姿: first = 最初の process・under = その子孫。"
  (#^ SimProcess first)
  (#^ (get tuple #(SimDescendant ...)) under))


(defk first-process [name]
  {:pre [(: name str)] :post [(: % SimProcess)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job name の最初の process の記録を読むため。"
  (<- processes tuple (ProcessesOf name))
  (assert processes name)
  (get (sorted processes :key (fn [p] p.started-ms)) 0))


(defk descendants-under [name pid]
  {:pre [(: name str) (: pid int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job name の process pid を根とする子孫の記録を読むため(子と孫が揃っている事も見る)。"
  (<- seen tuple (DescendantsOf name))
  (val under (tuple (gfor d seen :if (= d.origin.root pid) d)))
  (assert (= (sorted (gfor d under (get d.argv 0))) ["sleeper-grandchild" "spawner"]) seen)
  under)


(defk result-written? [name instance]
  {:pre [(: name str) (: instance str)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子孫 name が job の process instance の下で結果の file を書いたかを読むため。"
  (<- path str (result-path name instance))
  (<- read (| str FileFailed) (ReadText path))
  (isinstance read str))


(defk reaped-at [descendants ended-ms]
  {:pre [(: descendants tuple) (: ended-ms int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "子孫が全部、刻 ended-ms(根の process か世代の終わり)に片づけで -9 で終わった事を確かめるため。"
  (for [d descendants]
    (assert (and d.reaped (= d.exit-code -9) (= d.ended-ms ended-ms)) #(d ended-ms)))
  None)


(defk rolled-out []
  {:pre [] :post [(: % RolloutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き (1): 子と孫が起きた後(3 秒)に版 2 を宣言し、旧い process が抜けた後、子の仕事の長さを越えて待って読む。
   読んだ姿(RolloutSeen)を返す。"
  (<- (Delay 3.0))
  (<- (Redeclare (descendant-hosts-v2 sim-foundation)))
  (<- (Delay (+ SPAWNER-SECONDS 10.0)))
  (<- old SimProcess (first-process "parent"))
  (<- under tuple (descendants-under "parent" old.pid))
  (<- spawned bool (result-written? "spawner" old.instance))
  (<- slept bool (result-written? "sleeper-grandchild" old.instance))
  (RolloutSeen :old old :under under :spawned spawned :slept slept))


(deftest test-the-retired-process-takes-its-child-and-detached-grandchild-with-it
  ;; (1) 版の入れ替えで旧い process が抜けると、その子と別の session の孫は同じ刻に -9 で終わり、子は結果の file を書かない。
  (<- outside SimOutside (descendant-outside True))
  (<- got RolloutSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :outside outside
                             (descendant-hosts sim-foundation) (rolled-out)))
  (val old got.old)
  (val under got.under)
  (val spawned got.spawned)
  (val slept got.slept)
  (assert (is-not old.ended-ms None) old)
  (<- (reaped-at under old.ended-ms))
  (assert (not spawned) "片づけられた子が結果の file を書いた")
  (assert (not slept) "片づけられた孫が結果の file を書いた"))


(defk killed-or-stopped [end]
  {:pre [(: end str)] :post [(: % GenerationSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き (2): 子と孫が起きた後(3 秒)に worker の世代を終わらせ(end = kill・stop・elder)、子の仕事の長さを越えて待って読む。
   読んだ姿(GenerationSeen)を返す。elder = 新しい世代を先に起こしてから旧い世代を止める。"
  (<- (Delay 3.0))
  (match end
    "kill" (<- _killed int (KillWorker "w1"))
    "stop" (<- (StopWorker "w1"))
    "elder" (do (<- elder (| str None) (RollWorker "w1"))
                (assert (is-not elder None))
                (<- (Delay 3.0))
                (<- (StopElder "w1" elder))))
  (<- (Delay (+ SPAWNER-SECONDS 10.0)))
  (<- first SimProcess (first-process "parent"))
  (<- under tuple (descendants-under "parent" first.pid))
  (GenerationSeen :first first :under under))


(deftest test-a-dead-worker-takes-every-descendant-of-its-generation-at-the-kill
  ;; (2) 死んだ worker(node ごと): その世代の子孫は殺した刻に -9 で終わる。
  (<- outside SimOutside (descendant-outside True))
  (<- got GenerationSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :outside outside
                             (descendant-hosts sim-foundation) (killed-or-stopped "kill")))
  (val first got.first)
  (val under got.under)
  (assert (= first.exit-code -9) first)
  (<- (reaped-at under first.ended-ms)))


(deftest test-the-detached-grandchild-escapes-the-job-without-adoption-but-not-the-generation
  ;; 引き取りを外した変種: worker の止め(StopWorker)で job の process が終わると、process group の中の子は一緒に -9 で終わるが、
  ;; 別の session の孫は残り、世代の終わり(worker が抜けた刻)に -9 で終わる。
  (<- outside SimOutside (descendant-outside False))
  (<- got GenerationSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :outside outside
                             (descendant-hosts sim-foundation) (killed-or-stopped "stop")))
  (val first got.first)
  (val under got.under)
  (val child (next (gfor d under :if (= (get d.argv 0) "spawner") d)))
  (val grandchild (next (gfor d under :if (= (get d.argv 0) "sleeper-grandchild") d)))
  (assert (and (= child.exit-code -9) (= child.ended-ms first.ended-ms)) #(child first))
  (assert (and grandchild.escaped (= grandchild.exit-code -9) grandchild.reaped (>= grandchild.ended-ms first.ended-ms))
          #(grandchild first)))


(deftest test-stopping-the-elder-generation-takes-its-descendants
  ;; (2) 退いた世代の止め(StopElder — 旧い Pod の SIGTERM): 旧い世代の process の子孫は、引き取りを外した変種でも残らない。
  (<- outside SimOutside (descendant-outside False))
  (<- got GenerationSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :outside outside
                             (descendant-hosts sim-foundation) (killed-or-stopped "elder")))
  (val first got.first)
  (val under got.under)
  (assert (is-not first.ended-ms None) first)
  (for [d under]
    (assert (and (= d.exit-code -9) d.reaped (>= d.ended-ms first.ended-ms)) #(d first))))
