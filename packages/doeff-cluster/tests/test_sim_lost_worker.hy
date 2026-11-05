;; 同じ Pod の中で worker の process だけが消えた時(LoseWorker — 本番の入れ物 shim の --orphan-seconds の写し)の、job の process と子孫の寿命:
;; 退きの寿命 retiredSeconds を宣言した handoff の service(outlives-worker)の process は止まらず退く知らせを受け、その子は仕事を終えて結果の
;; file を書く。process は自分で終わらなければ寿命の上限で取り消され(exit -15)、その時に残った子孫(別の session の孫)を片づける。宣言の
;; 無い service の process とその子孫は、消えた刻に -9 で終わる(KillWorker と同じ)。
;;
;; 筋書きの service は tests/fixtures/descendant_programs.hy の descendant-hosts-outliving(keeper と parent — どちらも子 spawner を起こし、
;; 子が別の session に孫を起こす)。
;; 失敗ケース: 世代の終わりで子孫を全部片づける世界(消失を KillWorker と同じに扱う — 直す前の shim の写し)では、keeper の子 spawner が
;; 消えた刻に -9 で終わり、結果の file を書かない — 検の「spawner が 0 で終わった」と「結果の file が在る」が名指す。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_core_effects [ReadText FileFailed])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [SimOutside])
(import doeff_cluster [sim-cluster SimWorker SimProcess SimDescendant ProcessesOf DescendantsOf LoseWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.descendant_programs [descendant-hosts-outliving descendant-outside result-path SPAWNER-SECONDS OUTLIVE-SECONDS])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"])))
;; 子と孫が起きた後に worker を消す刻と、寿命の上限を越えて読む刻(秒)。
(val LOSE-AT 3.0)
(val READ-AT (+ LOSE-AT OUTLIVE-SECONDS 20.0))


(defrecord LossSeen
  "筋書きが読んだ姿: lost-at = worker を消した刻・stopped = 消失で止めた process の数・keeper / parent = 各 service の最初の process・
   keeper-under / parent-under = その子孫・written = keeper の子 spawner が結果の file を書いたか。"
  (#^ int lost-at)
  (#^ int stopped)
  (#^ SimProcess keeper)
  (#^ SimProcess parent)
  (#^ (get tuple #(SimDescendant ...)) keeper-under)
  (#^ (get tuple #(SimDescendant ...)) parent-under)
  (#^ bool written))


(defk first-process [name]
  {:pre [(: name str)] :post [(: % SimProcess)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job name の最初の process の記録を読むため。"
  (<- processes tuple (ProcessesOf name))
  (assert processes name)
  (get (sorted processes :key (fn [p] p.started-ms)) 0))


(defk under-root [name pid]
  {:pre [(: name str) (: pid int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job name の process pid を根とする子孫の記録を読むため(子と孫が揃っている事も見る)。"
  (<- seen tuple (DescendantsOf name))
  (val under (tuple (gfor d seen :if (= d.origin.root pid) d)))
  (assert (= (sorted (gfor d under (get d.argv 0))) ["sleeper-grandchild" "spawner"]) seen)
  under)


(defk worker-lost []
  {:pre [] :post [(: % LossSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 子と孫が起きた後(LOSE-AT)に worker の process だけを消し、寿命の上限を越えて待って読む。読んだ姿(LossSeen)を返す。"
  (<- (Delay LOSE-AT))
  (<- lost-at int (now-epoch-ms))
  (<- stopped int (LoseWorker "w1"))
  (<- (Delay (- READ-AT LOSE-AT)))
  (<- keeper SimProcess (first-process "keeper"))
  (<- parent SimProcess (first-process "parent"))
  (<- keeper-under tuple (under-root "keeper" keeper.pid))
  (<- parent-under tuple (under-root "parent" parent.pid))
  (<- path str (result-path "spawner" keeper.instance))
  (<- read (| str FileFailed) (ReadText path))
  (LossSeen :lost-at lost-at :stopped stopped :keeper keeper :parent parent :keeper-under keeper-under :parent-under parent-under
            :written (isinstance read str)))


(defk named [under name]
  {:pre [(: under tuple) (: name str)] :post [(: % SimDescendant)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "子孫の列 under から命令の名 name の物を引くため。"
  (next (gfor d under :if (= (get d.argv 0) name) d)))


(deftest test-a-lost-worker-leaves-the-outliving-service-and-its-child-to-finish
  ;; outlives-worker の keeper: worker が消えても process は止まらず、子 spawner は仕事を終えて 0 で終わり結果の file を書く。keeper は
  ;; 寿命の上限(消えた刻 + OUTLIVE-SECONDS)で取り消され(-15)、その時に残っていた孫を同じ刻に片づける。
  (<- outside SimOutside (descendant-outside True))
  (<- got LossSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :outside outside
                                (descendant-hosts-outliving sim-foundation) (worker-lost)))
  (<- spawner SimDescendant (named got.keeper-under "spawner"))
  (<- grandchild SimDescendant (named got.keeper-under "sleeper-grandchild"))
  (assert (and (= spawner.exit-code 0) (not spawner.reaped)) spawner)
  (assert got.written "残した service の子が結果の file を書いていない")
  (assert (and (= got.keeper.exit-code -15) (= got.keeper.ended-ms (+ got.lost-at (* 1000 OUTLIVE-SECONDS)))) got.keeper)
  (assert (and grandchild.reaped (= grandchild.exit-code -9) (= grandchild.ended-ms got.keeper.ended-ms)) #(grandchild got.keeper)))


(deftest test-a-lost-worker-stops-the-other-services-and-their-descendants-at-once
  ;; 宣言の無い parent: worker が消えた刻に process は -9 で終わり、その子と孫も同じ刻に片づく。止めた数は parent の 1 本だけ。
  (<- outside SimOutside (descendant-outside True))
  (<- got LossSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :outside outside
                                (descendant-hosts-outliving sim-foundation) (worker-lost)))
  (assert (= got.stopped 1) got)
  (assert (and (= got.parent.exit-code -9) (= got.parent.ended-ms got.lost-at)) got.parent)
  (for [d got.parent-under]
    (assert (and d.reaped (= d.exit-code -9) (= d.ended-ms got.lost-at)) #(d got.lost-at))))
