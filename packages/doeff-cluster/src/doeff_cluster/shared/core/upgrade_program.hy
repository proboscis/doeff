;;; 版上げの Program(#3366 の単位 3)— worker を 1 台ずつ新しい版へ入れ替え、最後に coordinator を入れ替える。2026-10-05 の版上げ
;;; 12 回(#3156)で手でした順と待ちを、条 V1〜V4(coordinator/core/upgrade_invariants.hy)を自分で守る形にした:
;;;
;;;   worker ごとに: その worker に置かれた task が終わるのを待つ(V2)→ DesireWorker → PublishDeclarations → ApplyDeclarations →
;;;                  新しい版で live に戻るのを待つ(V3 — 戻りが来なければ次へ進まない)
;;;   最後に:       worker が全部新しい版で live(V1)・待ち行列が空(V4)を待つ → DesireCoordinator → 公開 → 当てる → 戻りを待つ
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
(import doeff_cluster.shared.intent.upgrade_model [PendingPhase UpgradeState UpgradeLimits UpgradeStalled ReadUpgradeState
                                                   PublishDeclarations ApplyDeclarations])


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


(defk await-until [step done limit-seconds]
  {:pre [(: step str) (: done Callable) (: limit-seconds float)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "版上げの次の手の前の条件 done(UpgradeState → bool の Program)が真になるまで待つため。coordinator の版の変化で起きて読み直し、
   届かない間は UNREACHABLE-RETRY-SECONDS だけ待って問い直す。limit-seconds を越えたら UpgradeStalled(step を名指す)。"
  (<- started int (now-epoch-ms))
  (val deadline (+ started (int (* limit-seconds 1000))))
  (var revision 0)
  (var reached False)
  (while (not reached)
    (<- state (ReadUpgradeState))
    (var ok False)
    (when (isinstance state UpgradeState)
      (<- judged bool (done state))
      (:= ok judged))
    (if ok
        (:= reached True)
        (do (<- now int (now-epoch-ms))
            (when (>= now deadline)
              (raise (UpgradeStalled step limit-seconds)))
            (val remaining (/ (- deadline now) 1000.0))
            (<- change (AwaitRunnersChange revision :timeout-seconds (min remaining WATCH-SECONDS)))
            (if (isinstance change RunnersChange)
                (:= revision change.revision)
                (<- (Delay (min remaining UNREACHABLE-RETRY-SECONDS)))))))
  None)


(defk upgrade-cluster [workers coordinator limits]
  {:pre [(: workers (get tuple #(WorkerLaunch ...))) (: coordinator CoordinatorLaunch) (: limits UpgradeLimits)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker を 1 台ずつ新しい値へ入れ替え、最後に coordinator を入れ替えるため(条 V1〜V4 を守る順と待ち — 頭の註)。worker の値の
   doeff-commit と coordinator の doeff-commit は同じ版を言う(V1 の「同じ版で live」)。"
  (for [w workers]
    (<- (await-until (.format "worker {} に置かれた task が終わる" w.name) (partial no-task-on w.name) limits.drain-seconds))
    (<- (DesireWorker w))
    (<- (PublishDeclarations))
    (<- (ApplyDeclarations))
    (<- (await-until (.format "worker {} が版 {} で live に戻る" w.name w.doeff-commit) (partial back-on w.name w.doeff-commit)
                     limits.return-seconds)))
  (<- (await-until (.format "worker が全部 版 {} で live" coordinator.doeff-commit) (partial all-back-on coordinator.doeff-commit)
                   limits.return-seconds))
  (<- (await-until "待ち行列が空" queue-empty limits.queue-seconds))
  (<- (DesireCoordinator coordinator))
  (<- (PublishDeclarations))
  (<- (ApplyDeclarations))
  (<- (await-until "coordinator が戻り worker が全部 live" all-live limits.return-seconds))
  None)
