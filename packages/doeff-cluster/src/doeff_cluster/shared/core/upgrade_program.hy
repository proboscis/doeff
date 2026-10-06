;;; 版上げの Program(#3366 の単位 3)— worker を 1 台ずつ新しい版へ入れ替え、最後に coordinator を入れ替える。2026-10-05 の版上げ
;;; 12 回(#3156)で手でした順と待ちを、条 V1〜V5(coordinator/core/upgrade_invariants.hy)を自分で守る形にした:
;;;
;;;   worker ごとに: その worker に置かれた task が終わるのを待つ(V2)→ 空の機体の起動を確かめる(ConfirmCleanBoot — 断られたら
;;;                  UpgradeRefused で止まる・単位 5a)→ 入れ替え先の版の自己起動の root を今の置き場に先に準備する(PrepareBootRoot —
;;;                  V5・断られたら UpgradeRefused で止まる・#3725)→ DesireWorker → PublishDeclarations → ApplyDeclarations →
;;;                  新しい版で live に戻るのを待つ(V3 — 戻りが来なければ次へ進まない)
;;;   最後に:       worker が全部新しい版で live(V1)・待ち行列が空(V4)を待つ → 空の起動を確かめる → root を準備する(V5)→
;;;                  DesireCoordinator → 公開 → 当てる → 戻りを待つ
;;;
;;; 入れ替えの前の 2 つの手(確かめ・root の準備)は宣言を書く前に通す — どちらが断られても、宣言にも名簿にも何も書かずに止まる。
;;; root の準備は effect 1 つで、答え手が準備の終わりまで受け持って答える(ここは答えを 1 回受けるだけ — 時間で読み直さない)。
;;;
;;; worker の輪だけの upgrade-workers は、coordinator を入れ替えない回(今の coordinator が新しい worker を受ける版の組)の入口でもある。
;;; V1 は名簿の worker の全部で判じる — 読み手が版を読めない worker(RosterEntry の doeff-commit が None)は新しい版と数えない(#3366)。
;;;
;;; 待ちは時間で読み直さない — coordinator の版の変化(AwaitRunnersChange — task の phase と worker の変化で進む)で起きて読み直す。
;;; coordinator に届かない間だけ、上限の内で短く待ってから問い直す(版の変化を待つ口が無いため)。どの待ちも上限(UpgradeLimits —
;;; 宣言の値)を持ち、越えたら UpgradeStalled で、どの待ちで止まったかを名指しで落ちる(黙って待ち続けない)。
(require doeff-hy.macros [val var defk <-])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import collections.abc [Callable])
(import functools [partial])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange RunnersChange])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch DesireWorker DesireCoordinator])
(import doeff_cluster.shared.intent.upgrade_model [PendingPhase RosterEntry UpgradeState UpgradeLimits UpgradeStalled ReadUpgradeState
                                                   PublishDeclarations ApplyDeclarations ConfirmCleanBoot CleanBootPassed
                                                   CleanBootRefused UpgradeRefused PrepareBootRoot BootRootAlreadyPrepared
                                                   BootRootBuilt BootRootRefused])


;; coordinator に届かない間に問い直すまでの秒(版の変化を待つ口が答えない時だけ — 上限の内)と、1 回の版の変化の待ちの上限の秒
;; (版に入らない変化 = worker の生死の切り替わりを読み直すため)。
(val UNREACHABLE-RETRY-SECONDS 1.0)
(val WATCH-SECONDS 30.0)


(defk no-task-on [name state]
  {:pre [(: name str) (: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name に置かれた task(assigned・走り中)が無いか — 入れ替える worker の上で走る task を失わないため(条 V2)。"
  (not (any (gfor t state.tasks (and (= t.phase PendingPhase.ASSIGNED) (= t.worker name))))))


(defk back-on [name commit state]
  {:pre [(: name str) (: commit str) (: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name が版 commit で live に戻ったか — 戻りを読まずに次を入れ替えないため(条 V3)。"
  (any (gfor e state.roster (and (= e.worker name) e.live (= e.doeff-commit commit)))))


(defk all-back-on [commit state]
  {:pre [(: commit str) (: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿の worker が全部、版 commit で live か — coordinator を入れ替えてよいか(条 V1)。名簿が空なら偽(読めていない)。"
  (and (bool state.roster) (all (gfor e state.roster (and e.live (= e.doeff-commit commit))))))


(defk queue-empty [state]
  {:pre [(: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "待ち行列に task が無いか — 作り直した coordinator が worker の行を読めない間に queued を落とさないため(条 V4)。"
  (not (any (gfor t state.tasks (= t.phase PendingPhase.QUEUED)))))


(defk all-live [state]
  {:pre [(: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿の worker が全部 live か — 作り直した coordinator に worker が名乗り直したか。名簿が空なら偽。"
  (and (bool state.roster) (all (gfor e state.roster e.live))))


(defk entry-line [e]
  {:pre [(: e RosterEntry)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿の 1 台を、止まった時の文に載せる 1 句にするため — live と版、版を読めない時はその訳(何が戻らないのかを名指す)。"
  (if (is e.doeff-commit None)
      (.format "{}(live={}・版を読めない: {})" e.worker e.live (or e.unread-reason "訳は読み手が書いていない"))
      (.format "{}(live={}・版 {})" e.worker e.live (cut e.doeff-commit 0 10))))


(defk joined-lines [entries]
  {:pre [(: entries (get tuple #(RosterEntry ...)))] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿の何台かを、止まった時の文の 1 つの句にするため(「・」で並べる)。"
  (var lines #())
  (for [e entries]
    (<- line str (entry-line e))
    (:= lines (+ lines #(line))))
  (.join "・" lines))


(defk tasks-on-line [name state]
  {:pre [(: name str) (: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "no-task-on の待ちが止まった時に、worker name に置かれたままの task を名指すため。"
  (val placed (tuple (gfor t state.tasks :if (and (= t.phase PendingPhase.ASSIGNED) (= t.worker name)) t.task)))
  (.format "worker {} に置かれた task {}" name (.join "・" placed)))


(defk worker-line [name state]
  {:pre [(: name str) (: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "back-on の待ちが止まった時に、戻らない worker の最後の読み(live・版・版を読めない訳)を名指すため。"
  (val found (tuple (gfor e state.roster :if (= e.worker name) e)))
  (if found
      (do (<- line str (joined-lines found)) line)
      (.format "worker {} は名簿に居ない" name)))


(defk not-back-line [commit state]
  {:pre [(: commit str) (: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "all-back-on の待ちが止まった時に、版 commit で live でない worker を名指すため。"
  (if (not state.roster)
      "名簿が空"
      (do (<- line str (joined-lines (tuple (gfor e state.roster :if (not (and e.live (= e.doeff-commit commit))) e))))
          line)))


(defk queued-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "queue-empty の待ちが止まった時に、待ち行列に残る task を名指すため。"
  (.format "queued の task {}" (.join "・" (gfor t state.tasks :if (= t.phase PendingPhase.QUEUED) t.task))))


(defk not-live-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "all-live の待ちが止まった時に、名乗り直していない worker を名指すため。"
  (if (not state.roster)
      "名簿が空"
      (do (<- line str (joined-lines (tuple (gfor e state.roster :if (not e.live) e)))) line)))


(defk await-until [step done observe limit-seconds]
  {:pre [(: step str) (: done Callable) (: observe Callable) (: limit-seconds float)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "版上げの次の手の前の条件 done(UpgradeState → bool の Program)が真になるまで待つため。coordinator の版の変化で起きて読み直し、
   届かない間は UNREACHABLE-RETRY-SECONDS だけ待って問い直す。limit-seconds を越えたら UpgradeStalled(step を名指し、最後の読みを
   observe(UpgradeState → str の Program)で文にして載せる — 待ちの名だけでは何が戻らないのか分からないため・#3366)。"
  (<- started int (now-epoch-ms))
  (val deadline (+ started (int (* limit-seconds 1000))))
  (var revision 0)
  (var reached False)
  (var last "まだ 1 度も読めていない")
  (while (not reached)
    (<- state (ReadUpgradeState))
    (var ok False)
    (if (isinstance state UpgradeState)
        (do (<- judged bool (done state))
            (:= ok judged)
            (<- seen str (observe state))
            (:= last seen))
        (:= last (.format "名簿を読めなかった: {}" state.reason)))
    (if ok
        (:= reached True)
        (do (<- now int (now-epoch-ms))
            (when (>= now deadline)
              (raise (UpgradeStalled step limit-seconds last)))
            (val remaining (/ (- deadline now) 1000.0))
            (<- change (AwaitRunnersChange revision :timeout-seconds (min remaining WATCH-SECONDS)))
            (if (isinstance change RunnersChange)
                (:= revision change.revision)
                (<- (Delay (min remaining UNREACHABLE-RETRY-SECONDS)))))))
  None)


(defk confirm-clean-boot [launch target]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: target str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "入れ替え先の値で空の機体の起動が通る事を、宣言を書く前に確かめるため。断られたら UpgradeRefused で target を名指して止まる
   (宣言を書かず公開もしない — cluster は変わらない)。"
  (<- verdict (ConfirmCleanBoot launch))
  (match verdict
    (CleanBootPassed) None
    (CleanBootRefused) (raise (UpgradeRefused target verdict))))


(defk prepare-boot-root [launch target]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: target str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "入れ替え先の版の自己起動の root を、宣言を書く前に、その物の今の置き場に準備しておくため(作り直した process が起動の中で root を
   準備して初回の import をする間 — 実測 15〜25 秒 — service に届かなくなるのを避ける・条 V5・#3725)。準備済みでも組んでも先へ進み、
   断られたら UpgradeRefused で target と断りの答え(訳は閉じた語)を名指して止まる(宣言を書かず公開もしない — cluster は変わらない)。
   待ちは答え手が持つ — ここは答えを 1 回受けるだけ。答えは 3 つの型のどれか(それ以外の値を返す答え手は、ここで型の名指しで落ちる —
   知らない答えを「準備済み」と読んで先へ進まない)。"
  (<- answer (| BootRootAlreadyPrepared BootRootBuilt BootRootRefused) (PrepareBootRoot launch))
  (match answer
    (BootRootAlreadyPrepared) None
    (BootRootBuilt) None
    (BootRootRefused) (raise (UpgradeRefused target answer))))


(defk upgrade-workers [workers limits]
  {:pre [(: workers (get tuple #(WorkerLaunch ...))) (: limits UpgradeLimits)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker を 1 台ずつ新しい値へ入れ替えるため(条 V2・V3 の待ち — 頭の註)。coordinator は入れ替えない — worker だけを上げる回
   (coordinator が今の版のまま新しい worker を受ける版の組)と、upgrade-cluster の前半の両方がこれを通る(#3366)。どの入れ替えも、
   宣言を書く前に空の機体の起動を確かめ(confirm-clean-boot)、入れ替え先の版の root を置き場に準備する(prepare-boot-root)。"
  (for [w workers]
    (<- (await-until (.format "worker {} に置かれた task が終わる" w.name) (partial no-task-on w.name) (partial tasks-on-line w.name)
                     limits.drain-seconds))
    (<- (confirm-clean-boot w w.name))
    (<- (prepare-boot-root w w.name))
    (<- (DesireWorker w))
    (<- (PublishDeclarations))
    (<- (ApplyDeclarations))
    (<- (await-until (.format "worker {} が版 {} で live に戻る" w.name w.doeff-commit) (partial back-on w.name w.doeff-commit)
                     (partial worker-line w.name) limits.return-seconds)))
  None)


(defk upgrade-cluster [workers coordinator limits]
  {:pre [(: workers (get tuple #(WorkerLaunch ...))) (: coordinator CoordinatorLaunch) (: limits UpgradeLimits)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker を 1 台ずつ新しい値へ入れ替え(upgrade-workers)、最後に coordinator を入れ替えるため(条 V1〜V5 を守る順と待ち — 頭の註)。
   worker の値の doeff-commit と coordinator の doeff-commit は同じ版を言う(V1 の「同じ版で live」)。V1 は名簿の worker の全部で
   判じる — 版の読めない worker(RosterEntry の doeff-commit が None)は新しい版と数えないので、その待ちで名指しで止まる。"
  (<- (upgrade-workers workers limits))
  (<- (await-until (.format "worker が全部 版 {} で live" coordinator.doeff-commit) (partial all-back-on coordinator.doeff-commit)
                   (partial not-back-line coordinator.doeff-commit) limits.return-seconds))
  (<- (await-until "待ち行列が空" queue-empty queued-line limits.queue-seconds))
  (<- (confirm-clean-boot coordinator "coordinator"))
  (<- (prepare-boot-root coordinator "coordinator"))
  (<- (DesireCoordinator coordinator))
  (<- (PublishDeclarations))
  (<- (ApplyDeclarations))
  (<- (await-until "coordinator が戻り worker が全部 live" all-live not-live-line limits.return-seconds))
  None)
