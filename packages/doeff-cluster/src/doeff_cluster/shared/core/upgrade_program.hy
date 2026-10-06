;;; 版上げの Program(#3366 の単位 3)— worker を 1 つずつ新しい版へ入れ替える入口(upgrade-workers)・coordinator だけを入れ替える入口
;;; (upgrade-coordinator — #3772)・その 2 つを順に通す入口(upgrade-cluster)。2026-10-05 の版上げ 12 回(#3156)で手でした順と待ちを、
;;; 条 V1〜V5(coordinator/core/upgrade_invariants.hy)を自分で守る形にした:
;;;
;;;   worker ごとに: その worker に置かれた task が終わるのを待つ(V2)→ 空の機体の起動を確かめる(ConfirmCleanBoot — 断られたら
;;;                  UpgradeRefused で止まる・単位 5a)→ 入れ替え先の版の自己起動の root を今の保存先に先に準備する(PrepareBootRoot —
;;;                  V5・断られたら UpgradeRefused で止まる・#3725)→ DesireWorker → PublishDeclarations → ApplyDeclarations →
;;;                  新しい版で live に戻るのを待つ(V3 — 戻りが来なければ次へ進まない)
;;;   coordinator:   待ち行列が空(V4)を待つ → 宣言の内の worker が全部 live で版を読めるのを待ち、その版を確かめた版の組み合わせで照らす
;;;                  (V1 — 組み合わせに無い版が 1 つでも在れば断る)→ 空の起動を確かめる → root を準備し(V5)、上げる前の版の root
;;;                  (戻し先)が在るかを確かめる → DesireCoordinator → 公開 → 当てる直前の確かめ(状態を読み直す → V1 の照らし →
;;;                  待ち行列が空(V4)→ 静かな時間帯を待つ(AwaitQuietWindow))→ 当てる → coordinator が入れ替え先の版で答えるのを
;;;                  待つ → 宣言の内の worker が全部 live に戻るのを待つ → 入れ替えの前に待っていた task が coordinator に在るのを待つ
;;;
;;; 入れ替えの前の手順(起動の確認・root の準備・版の照らし・戻し先の確かめ)は宣言を書く前に通す — どれが断っても、宣言にも名簿にも何も
;;; 書かずに止まる(UpgradeRefused の断った所 = 宣言の前)。公開(マージの列・main 入り)は数分かかり、その間に別の worker の入れ替え・
;;; 待ち行列の task・入れ替えで切れて困る仕事が入りうるので、coordinator は当てる直前にもう 1 度確かめる — 断ったら当てずに止まる
;;; (断った所 = 当てる前・宣言と公開は済んだまま・自動では戻さない)。root の準備と静かな時間帯の待ちは effect 1 つずつで、handler が
;;; 終わりまで受け持って答える(ここは答えを 1 回受けるだけ — 時間で読み直さない)。
;;;
;;; worker と coordinator のどちらを先に上げるかは変更ごとに決まり、確かめた版の組み合わせ(VerifiedVersions — coordinator の版 X と、X と
;;; 組めると手元で確かめた worker の版の集合)で表す(#3772 — 2026-10-05 は worker が先・2026-10-06 の #3748 は coordinator が先)。
;;; worker だけを上げる時は upgrade-workers、coordinator だけを上げる時は upgrade-coordinator を呼び、組み合わせが 2 つの順を決める。
;;; 名簿の worker のうち宣言の外(RosterEntry の declaration が UNDECLARED)の物は、待ちからも V1 の照らしからも外し、coordinator の
;;; 入れ替えの答えに名と版を必ず載せる。宣言の内で版を読めない worker(doeff-commit が None — 入れ替えの途中など)は待つ。
;;;
;;; 3 つの入口は、どれも最初に doeff-cluster の code の差を判じる(#2671 — 本体を起動し直すのは doeff-cluster の code が変わった時だけ):
;;; 名簿を 1 回読み、上げる対象ごとに動いている版(coordinator は GET /state で名乗る版・worker は名簿の版)と上げる先の版の間の差を
;;; CompareClusterCode で問う。差の無い対象は入れ替えずに答えに名と版で出し(UnchangedTarget)、全部の対象に差が無ければ宣言を書く前に
;;; UpgradeRefused(NoClusterCodeChange)で断る。動いている版を読めない・差を判じられない対象が 1 つでも在れば、宣言を書く前に
;;; UpgradeRefused(ClusterCodeUnjudged)で名指して止まる。upgrade-cluster で入れ替えずに外した worker は、上げる先と同じ doeff-cluster の
;;; code で動いているので、その動いている版を coordinator の条 V1 の照らしで確かめた版の組み合わせに数える。
;;;
;;; 待ちは時間で読み直さない — coordinator の版の変化(AwaitRunnersChange — task の phase と worker の変化で進む)で起きて読み直す。
;;; coordinator に届かない間だけ、上限の内で短く待ってから問い直す(版の変化を待つ API が無いため)。どの待ちも上限(UpgradeLimits —
;;; 宣言の値)を持ち、越えたら UpgradeStalled で、どの待ちで止まったかを明示して落ちる(黙って待ち続けない)。
(require doeff-hy.macros [val var defk <-])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import collections.abc [Callable])
(import functools [partial])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange RunnersChange])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch DesireWorker DesireCoordinator])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.upgrade_model [PendingPhase WorkerDeclaration RosterEntry UpgradeState UpgradeLimits UpgradeStalled
                                                   ReadUpgradeState PublishDeclarations ApplyDeclarations ConfirmCleanBoot
                                                   CleanBootPassed CleanBootRefused UpgradeRefused PrepareBootRoot
                                                   BootRootAlreadyPrepared BootRootBuilt BootRootRefused VerifiedVersions
                                                   AwaitQuietWindow QuietWindowOpened QuietWindowMissed UnverifiedWorkers
                                                   RollbackRootMissing QueuedTasksRemain RefusalPoint WaitReached WaitExpired
                                                   CoordinatorUpgraded ClusterUpgraded WorkersUpgraded UpgradeKind
                                                   CompareClusterCode ClusterCodeDiffers ClusterCodeSame ClusterCodeUnread
                                                   UnchangedTarget NoClusterCodeChange ClusterCodeUnjudged CodeGate])


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


(defk declared-of [state]
  {:pre [(: state UpgradeState)] :post [(: % (get tuple #(RosterEntry ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿のうち宣言の内の worker を返すため — 待ちと条 V1 の照らしの母集団(#3772)。"
  (tuple (gfor e state.roster :if (= e.declaration WorkerDeclaration.DECLARED) e)))


(defk undeclared-of [state]
  {:pre [(: state UpgradeState)] :post [(: % (get tuple #(RosterEntry ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿のうち宣言の外の worker を返すため — 待ちと照らしから外した事を、coordinator の入れ替えの答えに載せる(黙って外さない・#3772)。"
  (tuple (gfor e state.roster :if (= e.declaration WorkerDeclaration.UNDECLARED) e)))


(defk unverified-of [verified state]
  {:pre [(: verified VerifiedVersions) (: state UpgradeState)] :post [(: % (get tuple #(RosterEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "宣言の内で live な worker のうち、版を読めて、その版が確かめた版の組み合わせ(verified.workers)に無い物を返すため — coordinator を
   入れ替える前に、組めると確かめていない版の worker を明示して断る(条 V1・#3772)。版を読めない worker は数えない(待つ側)。"
  (<- declared (get tuple #(RosterEntry ...)) (declared-of state))
  (tuple (gfor e declared
               :if (and e.live (is-not e.doeff-commit None) (not-in e.doeff-commit verified.workers))
               e)))


(defk v1-decidable [verified state]
  {:pre [(: verified VerifiedVersions) (: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "条 V1 の待ちを終えてよいか — 宣言の内の worker が全部 live で版を読めた(組み合わせに入るかはこの後に照らす)か、組み合わせに無い版で
   live な worker が 1 つでも居る(それ以上待たずに断る)時に真。宣言の内の worker が名簿に 1 つも無ければ偽(読めていない)。"
  (<- declared (get tuple #(RosterEntry ...)) (declared-of state))
  (<- unverified (get tuple #(RosterEntry ...)) (unverified-of verified state))
  (or (bool unverified)
      (and (bool declared) (all (gfor e declared (and e.live (is-not e.doeff-commit None)))))))


(defk state-read [state]
  {:pre [(: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿を 1 度読めたか(読めた名簿なら真)— 当てる直前に coordinator の状態を読み直す待ちの条件(届かない間だけ待つ)。"
  True)


(defk state-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "state-read の待ちの最後の読みの文 — 名簿を読めた時は止まらないので、名簿の worker の数だけを載せる。"
  (.format "名簿の worker の数 {}" (len state.roster)))


(defk queue-empty [state]
  {:pre [(: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "待ち行列に task が無いか — 作り直した coordinator が worker の行を読めない間に queued を落とさないため(条 V4)。"
  (not (any (gfor t state.tasks (= t.phase PendingPhase.QUEUED)))))


(defk coordinator-on [commit state]
  {:pre [(: commit str) (: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "答えた coordinator が版 commit で動いているか(GET /state で申告する版 — 宣言した版ではない)— 当てた直後に古い coordinator が答えても、
   入れ替えが済んだとみなさないため(#3772)。"
  (= state.coordinator-commit commit))


(defk declared-live [state]
  {:pre [(: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宣言の内の worker が全部 live か — 作り直した coordinator に worker が登録し直したか。宣言の外の worker は待たない。宣言の内の
   worker が名簿に 1 つも無ければ偽。"
  (<- declared (get tuple #(RosterEntry ...)) (declared-of state))
  (and (bool declared) (all (gfor e declared e.live))))


(defk tasks-known [ids state]
  {:pre [(: ids (get tuple #(str ...))) (: state UpgradeState)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator を入れ替える前に待っていた task(ids)が、入れ替えの後の coordinator に全部在るか(終わった物も数える)— 作り直しで
   coordinator が task の記録を失っていないことを確かめるため(#3772)。"
  (all (gfor i ids (in i state.known-tasks))))


(defk undeclared-across [before after]
  {:pre [(: before UpgradeState) (: after UpgradeState)] :post [(: % (get tuple #(RosterEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の入れ替えで待ちと照らしから外した宣言の外の worker を、入れ替えの答えに載せる形にするため: 入れ替えの後の名簿の物
   (新しい状態)と、入れ替えの前の名簿にだけ居た物。"
  (<- late (get tuple #(RosterEntry ...)) (undeclared-of after))
  (<- early (get tuple #(RosterEntry ...)) (undeclared-of before))
  (val seen (frozenset (gfor e late e.worker)))
  (+ late (tuple (gfor e early :if (not-in e.worker seen) e))))


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


(defk unsettled-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "条 V1 の待ち(v1-decidable)が止まった時に、live でない・版を読めない宣言の内の worker を明示するため(#3772)。"
  (<- declared (get tuple #(RosterEntry ...)) (declared-of state))
  (if (not declared)
      "宣言の内の worker が名簿に居ない"
      (do (<- line str (joined-lines (tuple (gfor e declared :if (not (and e.live (is-not e.doeff-commit None))) e))))
          line)))


(defk coordinator-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator-on の待ちが止まった時に、最後に答えた coordinator の版を明示するため(古い版のまま答え続けているのか、版を読めないのか)。"
  (if (is state.coordinator-commit None)
      "coordinator の版を読めない"
      (.format "coordinator は版 {} で答えている" (cut state.coordinator-commit 0 10))))


(defk missing-tasks-line [ids state]
  {:pre [(: ids (get tuple #(str ...))) (: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "tasks-known の待ちが止まった時に、入れ替えの後の coordinator に無い task を明示するため。"
  (.format "coordinator に無い task {}" (.join "・" (gfor i ids :if (not-in i state.known-tasks) i))))


(defk queued-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "queue-empty の待ちが止まった時に、待ち行列に残る task を名指すため。"
  (.format "queued の task {}" (.join "・" (gfor t state.tasks :if (= t.phase PendingPhase.QUEUED) t.task))))


(defk not-live-line [state]
  {:pre [(: state UpgradeState)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "declared-live の待ちが止まった時に、登録し直していない宣言の内の worker を明示するため。"
  (<- declared (get tuple #(RosterEntry ...)) (declared-of state))
  (if (not declared)
      "宣言の内の worker が名簿に居ない"
      (do (<- line str (joined-lines (tuple (gfor e declared :if (not e.live) e)))) line)))


(defk await-state [done observe limit-seconds]
  {:pre [(: done Callable) (: observe Callable) (: limit-seconds float)] :post [(: % (| WaitReached WaitExpired))]
   :tags {:context "doeff-cluster" :role "program"}}
  "版上げの次の手順の前の条件 done(UpgradeState → bool の Program)が真になるまで待つため。coordinator の版の変化で起きて読み直し、
   届かない間は UNREACHABLE-RETRY-SECONDS だけ待って問い直す。答えは閉じた 2 つ: 届いた(WaitReached — done が真になった時の名簿・
   呼び手がその同じ状態で次を判定する)か、limit-seconds を越えた(WaitExpired — 最後に読んだ名簿を observe(UpgradeState → str の
   Program)で文にした物と、最後に読めた名簿)。上限で落ちるか断るかは呼び手が決める(#3772)。"
  (<- started int (now-epoch-ms))
  (val deadline (+ started (int (* limit-seconds 1000))))
  (var revision 0)
  (var answer None)
  (var last "まだ 1 度も読めていない")
  (var last-state None)
  (while (is answer None)
    (<- state (ReadUpgradeState))
    (var ok False)
    (if (isinstance state UpgradeState)
        (do (<- judged bool (done state))
            (:= ok judged)
            (<- seen str (observe state))
            (:= last seen)
            (:= last-state state))
        (:= last (.format "名簿を読めなかった: {}" state.reason)))
    (if ok
        (:= answer (WaitReached :state state))
        (do (<- now int (now-epoch-ms))
            (if (>= now deadline)
                (:= answer (WaitExpired :observed last :last last-state))
                (do (val remaining (/ (- deadline now) 1000.0))
                    (<- change (AwaitRunnersChange revision :timeout-seconds (min remaining WATCH-SECONDS)))
                    (if (isinstance change RunnersChange)
                        (:= revision change.revision)
                        (<- (Delay (min remaining UNREACHABLE-RETRY-SECONDS)))))))))
  answer)


(defk await-until [step done observe limit-seconds]
  {:pre [(: step str) (: done Callable) (: observe Callable) (: limit-seconds float)] :post [(: % UpgradeState)]
   :tags {:context "doeff-cluster" :role "program"}}
  "版上げの次の手順の前の条件 done が真になるまで待ち(await-state)、limit-seconds を越えたら UpgradeStalled で落ちるため(step を明示し、
   最後に読んだ名簿の文を載せる — 待ちの名だけでは何が戻らないのか分からないため・#3366)。答え = done が真になった時の名簿。"
  (<- waited (| WaitReached WaitExpired) (await-state done observe limit-seconds))
  (match waited
    (WaitReached) waited.state
    (WaitExpired) (raise (UpgradeStalled step limit-seconds waited.observed))))


(defk confirm-clean-boot [launch target]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: target str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "入れ替え先の値で空の機体の起動が通る事を、宣言を書く前に確かめるため。断られたら UpgradeRefused で target を名指して止まる
   (宣言を書かず公開もしない — cluster は変わらない)。"
  (<- verdict (ConfirmCleanBoot launch))
  (match verdict
    (CleanBootPassed) None
    (CleanBootRefused) (raise (UpgradeRefused target verdict RefusalPoint.BEFORE-DESIRE))))


(defk prepare-boot-root [launch target]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: target str)] :post [(: % (| BootRootAlreadyPrepared BootRootBuilt))]
   :tags {:context "doeff-cluster" :role "program"}}
  "入れ替え先の版の自己起動の root を、宣言を書く前に、入れ替える対象の今の保存先に準備しておくため(作り直した process が起動の中で root を
   準備して初回の import をする間 — 実測 15〜25 秒 — service に届かなくなるのを避ける・条 V5・#3725)。準備済みでも組んでも先へ進み、
   断られたら UpgradeRefused で target と拒否の答え(理由は閉じた語)を明示して止まる(宣言を書かず公開もしない — cluster は変わらない)。
   待ちは handler が持つ — ここは答えを 1 回受けるだけ。答えは 3 つの型のどれか(それ以外の値を返す handler は、ここで型の名前を明示して落ちる —
   知らない答えを「準備済み」とみなして先へ進まない)。通った答え(準備済みだった・組んだ)はそのまま返す — 組んだ秒と、上げる前の版の
   root が保存先に残っているか(previous-root-present = 戻し先が在るか)を、Program を実行した側が読めるように。"
  (<- answer (| BootRootAlreadyPrepared BootRootBuilt BootRootRefused) (PrepareBootRoot launch))
  (match answer
    (BootRootAlreadyPrepared) answer
    (BootRootBuilt) answer
    (BootRootRefused) (raise (UpgradeRefused target answer RefusalPoint.BEFORE-DESIRE))))


(defk launch-name [launch]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch))] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "入れ替え先の値が名指す対象の名を、code の差の判じの答えと断りに載せるため: worker なら名・coordinator なら \"coordinator\"。"
  (match launch
    (WorkerLaunch :name name) name
    (CoordinatorLaunch) "coordinator"))


(defk launch-kind [launch]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch))] :post [(: % UpgradeKind)] :tags {:context "doeff-cluster" :role "judgment"}}
  "入れ替え先の値が worker か coordinator かを、差の無い対象の記録(UnchangedTarget)に載せるため。"
  (match launch
    (WorkerLaunch) UpgradeKind.WORKER
    (CoordinatorLaunch) UpgradeKind.COORDINATOR))


(defk running-commit-of [launch state]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: state UpgradeState)] :post [(: % (| str None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "入れ替え先の値が名指す対象が今 動いている doeff の版を、名簿から読むため(#2671): coordinator = GET /state で名乗る版・worker = 名簿の
   その worker の版。読めなければ None(既定の値で埋めない — 呼び手が訳を添えて止まる)。"
  (match launch
    (CoordinatorLaunch) state.coordinator-commit
    (WorkerLaunch :name name) (next (gfor e state.roster :if (= e.worker name) e.doeff-commit) None)))


(defk unread-reason-of [launch state]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: state UpgradeState)] :post [(: % str)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "動いている版を読めない対象の訳を、止まった時の断りに載せるため(何が名乗らないのか・名簿のどの行が版を持たないのかを名指す)。"
  (match launch
    (CoordinatorLaunch) "coordinator が GET /state で動いている版を名乗らない(答えに coordinatorCommit が無い)"
    (WorkerLaunch :name name)
      (do (val found (tuple (gfor e state.roster :if (= e.worker name) e)))
          (if found
              (or (. (get found 0) unread-reason) "名簿の版が無い(訳は読み手が書いていない)")
              (.format "worker {} は名簿に居ない" name)))))


(defk cluster-code-changed [launch state]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: state UpgradeState)] :post [(: % (| (| WorkerLaunch CoordinatorLaunch) UnchangedTarget))]
   :tags {:context "doeff-cluster" :role "program"}}
  "対象 1 つの「動いている版 → 上げる先の版」の間に doeff-cluster の code の差が在るかを判じるため(#2671): 差が在れば launch をそのまま返し
   (入れ替える)、差が無ければ UnchangedTarget(名と版)を返す(入れ替えない)。動いている版を読めない・答え手が判じられない時は、
   宣言を書く前に UpgradeRefused(ClusterCodeUnjudged)で対象と訳を名指して止まる(差が在るとも無いともみなさない)。"
  (<- target str (launch-name launch))
  (<- running (| str None) (running-commit-of launch state))
  (when (is running None)
    (<- reason str (unread-reason-of launch state))
    (raise (UpgradeRefused target (ClusterCodeUnjudged :target target :running None :wanted launch.doeff-commit :reason reason)
                           RefusalPoint.BEFORE-DESIRE)))
  (<- answer (| ClusterCodeDiffers ClusterCodeSame ClusterCodeUnread) (CompareClusterCode running launch.doeff-commit))
  (<- kind UpgradeKind (launch-kind launch))
  (match answer
    (ClusterCodeDiffers) launch
    (ClusterCodeSame) (UnchangedTarget :kind kind :target target :running running :wanted launch.doeff-commit)
    (ClusterCodeUnread :reason reason)
      (raise (UpgradeRefused target (ClusterCodeUnjudged :target target :running running :wanted launch.doeff-commit :reason reason)
                             RefusalPoint.BEFORE-DESIRE))))


(defk cluster-code-gate [launches limits]
  {:pre [(: launches (get tuple #((| WorkerLaunch CoordinatorLaunch) ...))) (: limits UpgradeLimits)] :post [(: % CodeGate)]
   :tags {:context "doeff-cluster" :role "program"}}
  "版上げの入口で、上げる対象の全部について doeff-cluster の code の差を、何も書く前に判じるため(#2671 — 頭の註)。名簿を 1 回読み
   (coordinator に届かない間は上限の内で待つ)、対象ごとに cluster-code-changed で問う。全部の対象に差が無ければ、宣言を書く前に
   UpgradeRefused(NoClusterCodeChange — 対象の名と版の全部)で断る。答え = 差の在る対象(渡した順)と、差が無いので外す対象。"
  (<- state UpgradeState (await-until "動いている版を読む" state-read state-line limits.return-seconds))
  (var changed #())
  (var unchanged #())
  (for [launch launches]
    (<- judged (| (| WorkerLaunch CoordinatorLaunch) UnchangedTarget) (cluster-code-changed launch state))
    (match judged
      (UnchangedTarget) (:= unchanged (+ unchanged #(judged)))
      _ (:= changed (+ changed #(judged)))))
  (when (not changed)
    (raise (UpgradeRefused (.join "・" (gfor u unchanged u.target)) (NoClusterCodeChange :targets unchanged) RefusalPoint.BEFORE-DESIRE)))
  (CodeGate :changed changed :unchanged unchanged))


(defk swap-workers [workers limits]
  {:pre [(: workers (get tuple #(WorkerLaunch ...))) (: limits UpgradeLimits)]
   :post [(: % (get tuple #((| BootRootAlreadyPrepared BootRootBuilt) ...)))]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker を 1 つずつ新しい値へ入れ替えるため(条 V2・V3 の待ち — 頭の註)。coordinator は入れ替えない — worker だけを上げる時
   (今の coordinator がその worker の版と組めると確かめた変更)と、upgrade-cluster の前半の両方がこれを通る(#3366)。どの入れ替えも、
   宣言を書く前に空の機体の起動を確かめ(confirm-clean-boot)、入れ替え先の版の root を保存先に準備する(prepare-boot-root)。
   code の差の判じは呼び手(入口)が先に済ませ、差の在る worker だけを渡す(#2671)。
   答え = worker ごとの root の準備の答えを、入れ替えた順に並べた列(実行した側が、worker ごとの秒と戻し先の有無を終わりに出すため)。"
  (var prepared #())
  (for [w workers]
    (<- (await-until (.format "worker {} に置かれた task が終わる" w.name) (partial no-task-on w.name) (partial tasks-on-line w.name)
                     limits.drain-seconds))
    (<- (confirm-clean-boot w w.name))
    (<- root (| BootRootAlreadyPrepared BootRootBuilt) (prepare-boot-root w w.name))
    (:= prepared (+ prepared #(root)))
    (<- (DesireWorker w))
    (<- (PublishDeclarations))
    (<- (ApplyDeclarations))
    (<- (await-until (.format "worker {} が版 {} で live に戻る" w.name w.doeff-commit) (partial back-on w.name w.doeff-commit)
                     (partial worker-line w.name) limits.return-seconds)))
  prepared)


(defk upgrade-workers [workers limits]
  {:pre [(: workers (get tuple #(WorkerLaunch ...))) (: limits UpgradeLimits)]
   :post [(: % WorkersUpgraded)]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker だけを上げる入口(coordinator は入れ替えない — 今の coordinator がその worker の版と組めると確かめた変更・#3366)。何も書く前に
   doeff-cluster の code の差を判じ(cluster-code-gate — 全部に差が無ければ断る・#2671)、差の在る worker だけを 1 つずつ入れ替える
   (swap-workers)。答え = 入れ替えた worker ごとの root の準備の答え(入れ替えた順)と、差が無いので外した worker(名と版)。"
  (<- gate CodeGate (cluster-code-gate workers limits))
  (<- prepared (get tuple #((| BootRootAlreadyPrepared BootRootBuilt) ...)) (swap-workers gate.changed limits))
  (WorkersUpgraded :prepared prepared :unchanged gate.unchanged))


(defk refuse-unverified [coordinator verified state point]
  {:pre [(: coordinator CoordinatorLaunch) (: verified VerifiedVersions) (: state UpgradeState) (: point RefusalPoint)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "条 V1 の照らし: 宣言の内で live な worker の版が、確かめた版の組み合わせに無い物を 1 つでも見たら、UpgradeRefused でその worker と版と
   断った所 point を明示して断るため(新しい coordinator が組めない版の worker の heartbeat を断り、その worker が job を止める形を、
   当てる前に止める・#3772)。宣言を書く前と、当てる直前(公開の待ちの間に別の worker の入れ替えが入りうる)の 2 か所で通る。"
  (<- unverified (get tuple #(RosterEntry ...)) (unverified-of verified state))
  (when unverified
    (raise (UpgradeRefused "coordinator" (UnverifiedWorkers :target "coordinator" :coordinator-commit coordinator.doeff-commit
                                                            :workers unverified)
                           point)))
  None)


(defk require-rollback-root [root target]
  {:pre [(: root (| BootRootAlreadyPrepared BootRootBuilt)) (: target str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "入れ替える前の版の自己起動の root(戻し先)が保存先に在る事を、宣言を書く前に確かめるため — 無ければ入れ替えた後に前の版へ戻せない
   ので、UpgradeRefused(RollbackRootMissing)で target を明示して始めない(#3772)。"
  (when (not root.previous-root-present)
    (raise (UpgradeRefused target (RollbackRootMissing :target target :root root) RefusalPoint.BEFORE-DESIRE)))
  None)


(defk queued-ids [state]
  {:pre [(: state UpgradeState)] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "名簿の読みの queued の task の id を、当てる前の待ち行列の断りの理由に載せるため。"
  (tuple (gfor t state.tasks :if (= t.phase PendingPhase.QUEUED) t.task)))


(defk refuse-unless-queue-empties [target limit-seconds]
  {:pre [(: target str) (: limit-seconds float)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "当てる直前に、待ち行列が空になるのを待つため(条 V4 をもう 1 度 — 宣言を書いてから公開して着くまでの数分の間に積まれた queued の
   task は、作り直した coordinator が worker の行を読めない間に落ちうる・#2440・#3772)。上限の内に空にならなければ当てずに
   UpgradeRefused(QueuedTasksRemain — 最後に読んだ queued の task の id)で止まる。1 度も名簿を読めなければ UpgradeStalled で落ちる
   (残った task を名指せない)。"
  (<- waited (| WaitReached WaitExpired) (await-state queue-empty queued-line limit-seconds))
  (match waited
    (WaitReached) None
    (WaitExpired :last None) (raise (UpgradeStalled "当てる前に待ち行列が空" limit-seconds waited.observed))
    (WaitExpired) (do (<- ids (get tuple #(str ...)) (queued-ids waited.last))
                      (raise (UpgradeRefused target (QueuedTasksRemain :target target :tasks ids) RefusalPoint.BEFORE-APPLY)))))


(defk await-quiet-window [target limit-seconds]
  {:pre [(: target str) (: limit-seconds float)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "当てる直前に、入れ替えで切れて困る仕事が走っていない時間帯を待つため(AwaitQuietWindow — 何を「切れて困る仕事」と読むかは handler が
   決める・#3772。公開は数分かかり、その間に切れて困る仕事が始まりうるので、宣言の前ではなく当てる直前に読む)。上限の内に来なければ
   当てずに UpgradeRefused(QuietWindowMissed — 静かにならなかった理由)で止まる(宣言は書いて公開した・当てていない)。"
  (<- answer (| QuietWindowOpened QuietWindowMissed) (AwaitQuietWindow :target target :timeout-seconds limit-seconds))
  (match answer
    (QuietWindowOpened) None
    (QuietWindowMissed) (raise (UpgradeRefused target answer RefusalPoint.BEFORE-APPLY))))


(defk check-before-apply [coordinator verified limits]
  {:pre [(: coordinator CoordinatorLaunch) (: verified VerifiedVersions) (: limits UpgradeLimits)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の宣言を公開した後、当てる直前の確かめを 1 か所で通すため(#3772): 状態を読み直す → 条 V1 の照らし(公開の待ちの間に
   別の worker の入れ替えが入りうる)→ 待ち行列が空(条 V4 — 上限で断る)→ 静かな時間帯(上限で断る)。どれが断っても当てずに
   UpgradeRefused(断った所 = 当てる前)で止まる — 宣言と公開は済んだまま・自動では戻さない。"
  (<- fresh UpgradeState (await-until "当てる前に coordinator の状態を読み直す" state-read state-line limits.return-seconds))
  (<- (refuse-unverified coordinator verified fresh RefusalPoint.BEFORE-APPLY))
  (<- (refuse-unless-queue-empties "coordinator" limits.queue-seconds))
  (<- (await-quiet-window "coordinator" limits.quiet-seconds))
  None)


(defk swap-coordinator [coordinator verified limits]
  {:pre [(: coordinator CoordinatorLaunch) (: verified VerifiedVersions) (: limits UpgradeLimits)
         (= verified.coordinator coordinator.doeff-commit)]
   :post [(: % CoordinatorUpgraded)]
   :tags {:context "doeff-cluster" :role "program"}}
  "coordinator を版 X(coordinator.doeff-commit)へ入れ替えるため(#3772 — 頭の註の coordinator の手順。code の差の判じは呼び手の入口が
   先に済ませる — #2671)。verified = 確かめた版の
   組み合わせ(coordinator の版は X と同じでなければならない)— 宣言の内の worker の版が混ざっていても、全部が組み合わせに入っていれば
   当てる。宣言の外の worker は待たず照らさず、答えに載せる。待ち行列が空(V4)→ 条 V1 の待ちと照らし → 空の起動の確認 → root の準備
   (V5)と戻し先の確かめ → Desire → 公開 → 当てる直前の確かめ(状態を読み直す → V1 の照らし → 待ち行列が空 → 静かな時間帯 —
   check-before-apply)→ 当てる → coordinator が X で答える → 宣言の内の worker が live → 待っていた task が coordinator に在る。
   答え = root の準備の答えと、外した宣言の外の worker。"
  (<- (await-until "待ち行列が空" queue-empty queued-line limits.queue-seconds))
  (<- before UpgradeState (await-until "宣言の内の worker が全部 live で版を読める" (partial v1-decidable verified) unsettled-line
                                       limits.return-seconds))
  (<- (refuse-unverified coordinator verified before RefusalPoint.BEFORE-DESIRE))
  (<- (confirm-clean-boot coordinator "coordinator"))
  (<- root (| BootRootAlreadyPrepared BootRootBuilt) (prepare-boot-root coordinator "coordinator"))
  (<- (require-rollback-root root "coordinator"))
  (<- (DesireCoordinator coordinator))
  (<- (PublishDeclarations))
  (<- (check-before-apply coordinator verified limits))
  (<- (ApplyDeclarations))
  (<- (await-until (.format "coordinator が版 {} で答える" coordinator.doeff-commit) (partial coordinator-on coordinator.doeff-commit)
                   coordinator-line limits.return-seconds))
  (<- after UpgradeState (await-until "宣言の内の worker が全部 live に戻る" declared-live not-live-line limits.return-seconds))
  (val waited (tuple (gfor t before.tasks t.task)))
  (<- (await-until "待っていた task が coordinator に在る" (partial tasks-known waited) (partial missing-tasks-line waited)
                   limits.return-seconds))
  (<- outside (get tuple #(RosterEntry ...)) (undeclared-across before after))
  (CoordinatorUpgraded :root root :undeclared outside))


(defk upgrade-coordinator [coordinator verified limits]
  {:pre [(: coordinator CoordinatorLaunch) (: verified VerifiedVersions) (: limits UpgradeLimits)
         (= verified.coordinator coordinator.doeff-commit)]
   :post [(: % CoordinatorUpgraded)]
   :tags {:context "doeff-cluster" :role "program"}}
  "coordinator だけを上げる入口(#3772): 何も書く前に、coordinator が GET /state で名乗る動いている版と上げる先の版 X の間の doeff-cluster の
   code の差を判じ(cluster-code-gate — 差が無ければ NoClusterCodeChange で断る・名乗らなければ ClusterCodeUnjudged で止まる・#2671)、
   差が在れば swap-coordinator で入れ替える。答え = root の準備の答えと、外した宣言の外の worker。"
  (<- (cluster-code-gate #(coordinator) limits))
  (<- swapped CoordinatorUpgraded (swap-coordinator coordinator verified limits))
  swapped)


(defk verified-with-kept [verified kept]
  {:pre [(: verified VerifiedVersions) (: kept (get tuple #(UnchangedTarget ...)))] :post [(: % VerifiedVersions)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "upgrade-cluster で入れ替えずに外した worker(kept)の動いている版を、coordinator の条 V1 の照らしの確かめた版の組み合わせに足すため
   (#2671): 外した worker は上げる先(組み合わせに入る版)と同じ doeff-cluster の code で動いているので、組めると確かめた版と数える —
   足さないと、外した worker の版で coordinator の入れ替えを断る事になる。"
  (replace verified :workers (| verified.workers (frozenset (gfor u kept :if (= u.kind UpgradeKind.WORKER) u.running)))))


(defk upgrade-cluster [workers coordinator verified limits]
  {:pre [(: workers (get tuple #(WorkerLaunch ...))) (: coordinator CoordinatorLaunch) (: verified VerifiedVersions) (: limits UpgradeLimits)
         (= verified.coordinator coordinator.doeff-commit) (all (gfor w workers (in w.doeff-commit verified.workers)))]
   :post [(: % ClusterUpgraded)]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker を 1 つずつ新しい値へ入れ替え(swap-workers)、その後に coordinator を入れ替えるため(swap-coordinator — worker が先の変更の入口)。
   何も書く前に、worker と coordinator の全部について doeff-cluster の code の差を判じ(cluster-code-gate — 全部に差が無ければ断る・#2671)、
   差の在る物だけを入れ替える。入れ替え先の worker の版は確かめた版の組み合わせ(verified.workers)に入っていなければならない — 入らない版へ
   上げると、worker を全部入れ替えた後に coordinator の条 V1 で断る事になるため、呼び手の誤りとして始めに落とす。差が無いので外した worker の
   動いている版は、coordinator の条 V1 の照らしで組み合わせに数える(verified-with-kept)。
   答え = worker の入れ替えの答え(root の準備の答えと外した worker)と、coordinator の入れ替えの答えか外した事。"
  (<- gate CodeGate (cluster-code-gate (+ workers #(coordinator)) limits))
  (val changed-workers (tuple (gfor w gate.changed :if (isinstance w WorkerLaunch) w)))
  (val kept-workers (tuple (gfor u gate.unchanged :if (= u.kind UpgradeKind.WORKER) u)))
  (<- prepared (get tuple #((| BootRootAlreadyPrepared BootRootBuilt) ...)) (swap-workers changed-workers limits))
  (<- swapped (| CoordinatorUpgraded UnchangedTarget) (coordinator-unless-kept coordinator verified gate limits))
  (ClusterUpgraded :workers (WorkersUpgraded :prepared prepared :unchanged kept-workers) :coordinator swapped))


(defk coordinator-unless-kept [coordinator verified gate limits]
  {:pre [(: coordinator CoordinatorLaunch) (: verified VerifiedVersions) (: gate CodeGate) (: limits UpgradeLimits)]
   :post [(: % (| CoordinatorUpgraded UnchangedTarget))] :tags {:context "doeff-cluster" :role "program"}}
  "upgrade-cluster の後半: coordinator に doeff-cluster の code の差が無ければ入れ替えずにその記録(UnchangedTarget)を返し、差が在れば、
   外した worker の版を足した組み合わせ(verified-with-kept)で swap-coordinator を通すため(#2671)。"
  (val kept (tuple (gfor u gate.unchanged :if (= u.kind UpgradeKind.COORDINATOR) u)))
  (match kept
    #(found) found
    _ (do (<- widened VerifiedVersions (verified-with-kept verified gate.unchanged))
          (<- swapped CoordinatorUpgraded (swap-coordinator coordinator widened limits))
          swapped)))
