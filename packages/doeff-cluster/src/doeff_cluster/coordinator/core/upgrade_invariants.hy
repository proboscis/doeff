;;; worker と coordinator を新しい版へ入れ替える順の条 V1〜V5(#3366 — 2026-10-05 の版上げ 12 回(#3156)で通した順を条にし、sim で
;;; 測った落ち方(tests/test_upgrade_swaps.hy)に合わせた。V5 は #3725)。
;;;
;;; 判じる物 = 入れ替えを始めた瞬間の記録(UpgradeStart — 型は shared/intent/upgrade_model.hy)の列: 何を(worker の名か coordinator)・どの版へ・その瞬間の名簿の写し
;;; (worker ごとの live と動いている版)と、終わっていない task の写し(queued か、どの worker に置かれた assigned か)。記録を作るのは、
;;; 版上げの Program を走らせる筋書き(模擬の Flux が当てた瞬間に名簿と task を写す)か、同じ形の合成の列(検の失敗ケース)。
;;; V5 だけは、同じ瞬間の保存先のスナップショット(BootRootsAtStart — 入れ替えの記録と、その瞬間に保存先に準備済みで在った自己起動の root の版)の
;;; 列を判定する — 保存先は名簿にも task にも出ず、準備の handler(PrepareBootRoot)だけが読むため。
;;;
;;; sim で測った落ち方(本物の coordinator の置き方の code を通す):
;;;   - worker の入れ替えの間の queued は落ちない(担い手が名簿に在る間は silent-worker-wait-ms まで待ち、戻ると走る — 本番 5 時間)。
;;;   - 入れ替える worker の上で走っている task は失う(lease が切れて lost — 別の live な worker が居ても走らせ直さない)。本番の
;;;     preStop は drain の空くのを待ってから止めるので失わない — 空くのを待たずに止めると失う。
;;;   - coordinator の作り直しで worker の行を読めない(2026-10-05 の形: 古い coordinator が書いた行に taskReserve が無い)と、queued は
;;;     「合う worker が無い」で即 落ちる(#2440 の落ちはこれ)。走り中の task は残る。
;;;
;;;   V1 coordinator-after-every-worker — coordinator の入れ替えを始めるのは、名簿の worker が全部、その coordinator と同じ版で live に
;;;      なった後だけ(新しい coordinator は新しい欄の無い heartbeat を断り、古い worker は 20 秒で job を止める — 版上げの調べ #3156)。
;;;   V2 worker-swap-waits-for-its-tasks — worker の入れ替えを始めるのは、その worker に置かれた task(assigned・走り中)が無い時だけ
;;;      (drain の空くのを待つ)。queued は入れ替えで落ちないので数えない。
;;;   V3 one-worker-at-a-time — 次の worker の入れ替えを始めるのは、前に入れ替えを始めた worker が新しい版で live に戻ったのを読んだ後
;;;      だけ(戻りが来なければ次へ進まない)。
;;;   V4 coordinator-swap-on-an-empty-queue — coordinator の入れ替えを始めるのは、queued の task が無い時だけ(作り直した coordinator が
;;;      worker の行を読めないと、queued は即 落ちる — 版を上げる作り直しでは行の形が変わり得る)。
;;;   V5 swap-after-boot-root-prepared — worker / coordinator の入れ替えを始めるのは、入れ替える対象の保存先に入れ替え先の版の自己起動の root が
;;;      準備済み(完成のマークつき)で在る時だけ(無いと、作り直した process が起動の中で root を準備して初回の import をする間 — 実測
;;;      15〜25 秒 — その上の service に届かない)。守るのは版上げの Program の順: 準備(PrepareBootRoot)が済んでから宣言を書く。
(require doeff-hy.macros [val defk])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [dataclass])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeKind PendingPhase RosterEntry PendingTask UpgradeStart BootRootsAtStart])


(defrecord UpgradeBreach
  "条 V1〜V5 の違反 1 つ: rule = 条の名・at-ms = 違反した入れ替えを始めた時刻・target = 何を入れ替え始めたか・detail = 何が足りなかったか。"
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
                              :detail (.format "worker {} は live={}・版 {}(入れ替え先 {})" e.worker e.live e.doeff-commit
                                               s.doeff-commit)))))


(defk worker-swap-waits-for-its-tasks [starts]
  {:pre [(: starts (get tuple #(UpgradeStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V2: worker の入れ替えを始めた瞬間に、その worker に置かれた task(assigned・走り中)を返す(空なら緑)。入れ替える worker の上で
   走っている task は lease が切れて失われ、別の live な worker が居ても走らせ直されない(sim で測った・本番の preStop は drain の空くのを
   待つ)— drain の空くのを待たずに止める順を、版上げの記録から判じるため。queued は入れ替えで落ちないので数えない。"
  (tuple (gfor s starts
               :if (= s.kind UpgradeKind.WORKER)
               t s.tasks
               :if (and (= t.phase PendingPhase.ASSIGNED) (= t.worker s.target))
               (UpgradeBreach :rule "V2 worker-swap-waits-for-its-tasks" :at-ms s.at-ms :target s.target
                              :detail (.format "task {} が {} に置かれたまま(drain の空くのを待っていない)" t.task s.target)))))


(defk one-worker-at-a-time [starts]
  {:pre [(: starts (get tuple #(UpgradeStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V3: worker の入れ替えを始めた瞬間に、その前に入れ替えを始めた worker が新しい版で live に戻っていなければ返す(空なら緑)。
   戻りを読まずに次を当てて、2 台が同時に居なくなる順を、版上げの記録から判じるため。記録は時刻の順。"
  (val workers (tuple (gfor s (sorted starts :key (fn [s] s.at-ms)) :if (= s.kind UpgradeKind.WORKER) s)))
  (tuple (gfor #(before after) (zip workers (cut workers 1 None))
               :setv back (tuple (gfor e after.roster
                                       :if (and (= e.worker before.target) e.live (= e.doeff-commit before.doeff-commit))
                                       e))
               :if (not back)
               (UpgradeBreach :rule "V3 one-worker-at-a-time" :at-ms after.at-ms :target after.target
                              :detail (.format "前の {} がまだ版 {} で live に戻っていない" before.target before.doeff-commit)))))


(defk coordinator-swap-on-an-empty-queue [starts]
  {:pre [(: starts (get tuple #(UpgradeStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V4: coordinator の入れ替えを始めた瞬間に queued だった task を返す(空なら緑)。作り直した coordinator が worker の行を読めない
   (版を上げると行の形が変わり得る — 2026-10-05 の taskReserve)と、queued は「合う worker が無い」で即 落ちる(sim で測った・#2440)—
   待ち行列が空でない時に coordinator を入れ替える順を、版上げの記録から判じるため。"
  (tuple (gfor s starts
               :if (= s.kind UpgradeKind.COORDINATOR)
               t s.tasks
               :if (= t.phase PendingPhase.QUEUED)
               (UpgradeBreach :rule "V4 coordinator-swap-on-an-empty-queue" :at-ms s.at-ms :target s.target
                              :detail (.format "task {} が queued のまま" t.task)))))


(defk swap-after-boot-root-prepared [places]
  {:pre [(: places (get tuple #(BootRootsAtStart ...)))] :post [(: % (get tuple #(UpgradeBreach ...)))]
   :tags {:context "coordinator" :role "judgment"}}
  "条 V5: worker / coordinator の入れ替えを始めた瞬間に、入れ替える対象の保存先に入れ替え先の版の自己起動の root が準備済みで無ければ返す
   (空なら緑)。root の無い版へ入れ替えると、作り直した process が起動の中で root を準備して初回の import をする間(実測 15〜25 秒)
   その上の service に届かない — 準備を通さずに(準備の前に・準備が拒否されたのに・組んでいないのに組んだと答えて)宣言を当てる順を、
   入れ替えの瞬間の保存先のスナップショットから判定するため(#3725)。"
  (tuple (gfor p places
               :if (not-in p.start.doeff-commit p.prepared)
               (UpgradeBreach :rule "V5 swap-after-boot-root-prepared" :at-ms p.start.at-ms :target p.start.target
                              :detail (.format "入れ替え先の版 {} の自己起動の root が保存先に無い(準備済みの版: {})" p.start.doeff-commit
                                               (or (.join "・" p.prepared) "無し"))))))
