;;; worker と coordinator を新しい版へ入れ替える順の条 V1〜V3(#3366 の単位 2a — 2026-10-05 の版上げ 12 回(#3156)で通した順を条にした)。
;;;
;;; 判じる物 = 入れ替えを始めた瞬間の記録(UpgradeStart)の列: 何を(worker の名か coordinator)・どの版へ・その瞬間の名簿の写し
;;; (worker ごとの名乗り WorkerInfo・live・動いている版)と、queued / assigned の task の写し(needs)。入れ替えの間と起動の隙間は、
;;; その worker が能力の合う担い手として居ない扱いになる(待ち行列の task が待たされずに落ちた #2440 の形)ので、順の条は「始める
;;; 瞬間」に判じる。記録を作るのは、版上げの Program を走らせる筋書き(模擬の Flux が当てた瞬間に名簿と task を写す)か、同じ形の
;;; 合成の列(検の失敗ケース)。
;;;
;;;   V1 coordinator-after-every-worker — coordinator の入れ替えを始めるのは、名簿の worker が全部、その coordinator と同じ版で live に
;;;      なった後だけ(新しい coordinator は新しい欄の無い heartbeat を断り、古い worker は 20 秒で job を止める — 版上げの調べ #3156)。
;;;   V2 worker-swap-leaves-a-taker — worker の入れ替えを始めるのは、その worker に合う queued / assigned の task が無いか、合う task の
;;;      全部を受けられる live な worker が別に居る時だけ。退いている間は合う worker が居ない扱いになり、待ち行列の task が待たされずに
;;;      落ちるため(#2440)。「合う」は coordinator の置き場所の規則 placeable そのもの(2 つ目を書かない)。
;;;   V3 one-worker-at-a-time — 次の worker の入れ替えを始めるのは、前に入れ替えを始めた worker が新しい版で live に戻ったのを読んだ後
;;;      だけ(戻りが来なければ次へ進まない)。coordinator の入れ替えの前の最後の 1 台も同じ(V1 が全部を見る)。
(require doeff-hy.macros [val defk])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo])
(import doeff_cluster.coordinator.core.cluster_policy [placeable])


;; 入れ替える物の種類。
(defenum UpgradeKind WORKER COORDINATOR)

;; 写しに載せる task の phase(V2 が数える母集団 = coordinator の /resources/Task の queued と assigned)。
(defenum PendingPhase QUEUED ASSIGNED)


(defrecord RosterEntry
  "入れ替えを始めた瞬間の名簿の 1 台の写し: info = coordinator が持つ名乗り(置き場所の規則 placeable がそのまま読む)・live = その瞬間に
   生きていたか・doeff-commit = その worker が動いている doeff の版。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ WorkerInfo info)
  (#^ bool live)
  (#^ str doeff-commit))


(defrecord PendingTask
  "入れ替えを始めた瞬間の、まだ終わっていない task の写し(queued か assigned)。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str task)
  (#^ PendingPhase phase)
  (#^ (get tuple #(str ...)) needs))


(defrecord UpgradeStart
  "入れ替えを始めた瞬間の記録 1 つ: kind と target(worker の名・coordinator なら \"coordinator\")・doeff-commit = 入れ替え先の版・
   roster と tasks = その瞬間の写し。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ int at-ms)
  (#^ UpgradeKind kind)
  (#^ str target)
  (#^ str doeff-commit)
  (#^ (get tuple #(RosterEntry ...)) roster)
  (#^ (get tuple #(PendingTask ...)) tasks))


(defrecord UpgradeBreach
  "条 V1〜V3 の破り 1 つ: rule = 条の名・at-ms = 破った入れ替えを始めた時刻・target = 何を入れ替え始めたか・detail = 何が足りなかったか。"
  {:tags {:context "coordinator" :role "type"}}
  (#^ str rule)
  (#^ int at-ms)
  (#^ str target)
  (#^ str detail))


(defk coordinator-after-every-worker [starts]
  {:pre [(: starts (get tuple #(UpgradeStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V1: coordinator の入れ替えを始めた瞬間に、名簿の worker のうち同じ版で live でない物を返す(空なら緑)。新しい coordinator が
   古い worker の heartbeat を断って job を止めさせる順を、版上げの記録から判じるため。"
  (tuple (gfor s starts
               :if (= s.kind UpgradeKind.COORDINATOR)
               e s.roster
               :if (not (and e.live (= e.doeff-commit s.doeff-commit)))
               (UpgradeBreach :rule "V1 coordinator-after-every-worker" :at-ms s.at-ms :target s.target
                              :detail (.format "worker {} は live={}・版 {}(入れ替え先 {})" e.info.name e.live e.doeff-commit
                                               s.doeff-commit)))))


(defk worker-swap-leaves-a-taker [starts]
  {:pre [(: starts (get tuple #(UpgradeStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V2: worker の入れ替えを始めた瞬間に、その worker に合う queued / assigned の task のうち、受けられる live な worker が別に居ない
   物を返す(空なら緑)。入れ替えで worker が退いている間と起動の隙間は、本物の coordinator がその worker を能力の合う担い手が居ない
   扱いにし、待ち行列の task を待たせずに落とす(#2440 — 2026-10-02 に日次の回 2 本)。その順を、版上げの記録から判じるため。"
  (tuple (gfor s starts
               :if (= s.kind UpgradeKind.WORKER)
               :setv swapped (tuple (gfor e s.roster :if (= e.info.name s.target) e))
               t s.tasks
               :if (any (gfor e swapped (placeable t.needs e.info)))
               :if (not (any (gfor e s.roster
                                   (and e.live (!= e.info.name s.target) (placeable t.needs e.info)))))
               (UpgradeBreach :rule "V2 worker-swap-leaves-a-taker" :at-ms s.at-ms :target s.target
                              :detail (.format "task {}({}・needs {})を受けられる live な worker が {} の外に居ない" t.task t.phase
                                               (list t.needs) s.target)))))


(defk one-worker-at-a-time [starts]
  {:pre [(: starts (get tuple #(UpgradeStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V3: worker の入れ替えを始めた瞬間に、その前に入れ替えを始めた worker が新しい版で live に戻っていなければ返す(空なら緑)。
   戻りを読まずに次を当てて、2 台が同時に居なくなる順を、版上げの記録から判じるため。記録は時刻の順。"
  (val workers (tuple (gfor s (sorted starts :key (fn [s] s.at-ms)) :if (= s.kind UpgradeKind.WORKER) s)))
  (tuple (gfor #(before after) (zip workers (cut workers 1 None))
               :setv back (tuple (gfor e after.roster
                                       :if (and (= e.info.name before.target) e.live (= e.doeff-commit before.doeff-commit))
                                       e))
               :if (not back)
               (UpgradeBreach :rule "V3 one-worker-at-a-time" :at-ms after.at-ms :target after.target
                              :detail (.format "前の {} がまだ版 {} で live に戻っていない" before.target before.doeff-commit)))))
