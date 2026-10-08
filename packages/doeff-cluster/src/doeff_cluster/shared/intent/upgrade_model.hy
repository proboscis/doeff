;;; worker と coordinator を新しい版へ入れ替える版上げの型と effect(#3366 の単位 3)。版上げの Program(shared/core/upgrade_program.hy)が
;;; 使い、条 V1〜V5 の判定(coordinator/core/upgrade_invariants.hy)が同じ記録を読む。
;;;
;;;   (<- state (ReadUpgradeState))       ; 名簿(worker ごとの live・動いている版・宣言の内か外か)・終わっていない task・coordinator の版・
;;;                                       ; coordinator が知る task の id の一覧
;;;   (<- (PublishDeclarations))          ; DesireWorker / DesireCoordinator で書いた宣言を公開する(本番 = commit と merge の列・sim = 何もしない)
;;;   (<- (ApplyDeclarations))            ; 公開した宣言を当てる(本番 = Flux の「すぐ読み直せ」の印・sim = 模擬の Flux の 1 回の当て)
;;;   (<- (ConfirmCleanBoot launch))      ; 入れ替え先の値で、コピーも状態も無い空の機体の起動が通るか(Desire の前に・#3366 の単位 5a)
;;;   (<- answer (PrepareBootRoot launch)) ; 入れ替え先の版の自己起動の root を、今の保存先に先に準備する(起動の確認の後・Desire の前に・#3725)
;;;   (<- answer (AwaitQuietWindow target seconds)) ; 静かな時間帯を待つ(coordinator の宣言を公開した後・当てる直前に・#3772)
;;;   (<- answer (AwaitWorkerDrained launch seconds)) ; worker を drain し、中の仕事が 0 になるのを待つ(worker の宣言を公開した後・
;;;                                       ; 当てる直前に・#3968)
;;;   (<- (ReleaseWorkerDrain launch))    ; AwaitWorkerDrained で worker に置いた drain を外す(当てた worker が新しい版で live に戻った後と、
;;;                                       ; drain の後に止まる時に・#4177)
;;;
;;; 答え手は本番と sim で分かれる(本番の答え手は配備する側の repo — 単位 5 の前に形を決める)。待ちは時間で読み直さず、coordinator の
;;; 版の変化(AwaitRunnersChange)で起きる。どの待ちも上限(UpgradeLimits — 宣言の値)を持ち、越えたら UpgradeStalled で名指しで落ちる。
;;; 入れ替えの前の手順が断った時は、UpgradeRefused で対象と断った所(RefusalPoint)を明示して止まる(自動で戻さない): 空の起動か root の
;;; 準備が断られた時・確かめた版の組み合わせに無い版の worker が居る時・戻し先の root が無い時は宣言を書く前(宣言を書かず公開もしない)。
;;; coordinator の当てる直前の確かめ(版の組み合わせの照らし・待ち行列が空・静かな時間帯)と、worker の当てる直前の drain(中の仕事が
;;; 上限の内に 0 にならない)が断った時は当てる前(宣言は書いて公開した・当てていない)。
;;;
;;; worker と coordinator のどちらを先に上げるかは変更ごとに決まり、確かめた版の組み合わせ(VerifiedVersions — coordinator の版 X と、X と組めると
;;; 手元で確かめた worker の版の集合)で表す(#3772)。
(require doeff-hy.macros [val defeffect])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])


;; 入れ替える物の種類。
(defenum UpgradeKind WORKER COORDINATOR)

;; 写しに載せる task の phase(coordinator の /resources/Task の queued と assigned — assigned は置かれた・走り中)。
(defenum PendingPhase QUEUED ASSIGNED)

;; 名簿の worker が、配備する側の宣言の内に居るか(#3772)。
;;   DECLARED   = 配備する側が宣言を書ける worker。版上げの待ちと条 V1 の照らしに入る — 版を読めない間(入れ替えの途中など)は待つ。
;;   UNDECLARED = 宣言の外の worker(配備する側の外の systemd が起動する worker など)。待ちからも条 V1 の照らしからも外し、外した事は
;;                coordinator を入れ替えた結果に名と版で必ず出す(黙って外さない)。
(defenum WorkerDeclaration DECLARED UNDECLARED)


(defrecord RosterEntry
  "名簿の worker 1 つのコピー: worker = 名・live = 生きていたか(作り直した直後の新しい世代が登録を終える前は数えない)・doeff-commit = その
   worker の今の世代が動いている doeff の版(None = 読み手がその worker の版を読めない)・declaration = 宣言の内か外か(閉じた区別 —
   「宣言の内で版を読めない」は待ち、「宣言の外」は待たない・#3772)。名簿には coordinator の知る worker を全部 載せる。
   unread-reason = 版を読めない理由(読み手が書く汎用の文 — 入れ替えの途中で新しい世代が準備完了でない・宣言の外 など。版を読めた時は
   None)。待ちが上限で止まった時の文に載り、何が戻らないのかを明示する。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str worker)
  (#^ bool live)
  (#^ (| str None) doeff-commit)
  (#^ WorkerDeclaration declaration)
  (setv #^ (| str None) unread-reason None))


(defrecord PendingTask
  "まだ終わっていない task の写し: phase = queued か assigned・worker = assigned の時に置かれた worker の名(queued は None)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str task)
  (#^ PendingPhase phase)
  (#^ (| str None) worker))


(defrecord UpgradeState
  "版上げの Program が次へ進むかを決めるための coordinator の状態: roster = 名簿のコピー・tasks = 終わっていない task のコピー・coordinator-commit = 答えた
   coordinator が動いている doeff の版(GET /state の欄 coordinatorCommit を shared/protocol/coordinator_reads の coordinator-commit-of-state で
   読んだ物 — 宣言した版ではなく答えた process の版。None = 申告していない。当てた直後に古い coordinator が答えても「新しい版で戻った」と
   読まないため・#3772)・known-tasks = coordinator が知る task の id の全部(終わった物も — coordinator の作り直しの前に待っていた task が、
   作り直しの後も coordinator に在るかを読むため)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ (get tuple #(RosterEntry ...)) roster)
  (#^ (get tuple #(PendingTask ...)) tasks)
  (#^ (| str None) coordinator-commit)
  (#^ (get tuple #(str ...)) known-tasks))


(defrecord UpgradeStateUnreachable
  "coordinator に届かず名簿を読めなかった(coordinator の作り直しの間など — 止まったとはみなさない)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str reason))


(defrecord UpgradeStart
  "入れ替えを始めた瞬間(古い process が止まる瞬間)の記録 1 つ: kind と target(worker の名・coordinator なら \"coordinator\")・
   doeff-commit = 入れ替え先の版・roster と tasks = その瞬間の写し。条 V1〜V4 の入力。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ int at-ms)
  (#^ UpgradeKind kind)
  (#^ str target)
  (#^ str doeff-commit)
  (#^ (get tuple #(RosterEntry ...)) roster)
  (#^ (get tuple #(PendingTask ...)) tasks))


(defrecord BootRootsAtStart
  "入れ替えを始めた瞬間(古い process が止まる瞬間)の、入れ替える対象の保存先のスナップショット 1 つ: start = その入れ替えの記録・prepared = その瞬間に
   保存先に準備済み(完成のマークつき)で在った自己起動の root の版。条 V5 の入力。UpgradeStart の欄にしない理由 = 名簿と task は coordinator から
   読む物・保存先は準備の handler(PrepareBootRoot)だけが読む物で、宣言を当てるだけの記録の作り手は保存先を知らない(#3725)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ UpgradeStart start)
  (#^ (get tuple #(str ...)) prepared))


(defrecord UpgradeLimits
  "版上げの待ちの上限(秒 — 宣言の値): drain = 入れ替える worker に置かれた task が終わるまで・return = 当てた worker / coordinator が
   新しい版で戻るまで(coordinator の前の、宣言の内の worker が動いて版を読めるまでの待ちも同じ上限)・queue = coordinator を入れ替える前に
   待ち行列が空になるまで・quiet = coordinator の宣言を書く前に静かな時間帯を待つ上限(AwaitQuietWindow に渡す・#3772)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ float drain-seconds)
  (#^ float return-seconds)
  (#^ float queue-seconds)
  (#^ float quiet-seconds))


(defrecord VerifiedVersions
  "確かめた版の組み合わせ(#3772): coordinator = coordinator の版 X・workers = X と組めると手元で確かめた worker の版の集合。worker と coordinator の
   どちらを先に上げるかは変更ごとに決まり、この組み合わせで表す — coordinator を版 X へ入れ替えてよいのは、宣言の内の worker が全部動いていて、
   その版がこの集合に入っている時だけ(条 V1)。集合に無い版の worker が 1 つでも居れば、宣言を書く前に、その worker を明示して断る。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str coordinator)
  (#^ (get frozenset str) workers))


(defclass UpgradeStalled [RuntimeError]
  "版上げの待ちが上限を越えた(置かれた task が終わらない・worker が戻らない・待ち行列が空にならない)。step = どの待ちで止まったか・
   limit-seconds = その上限・observed = 最後に読んだ物のうちその待ちに効く所(戻らない worker の live・版・版を読めない訳 など —
   待ちの名だけでは何が戻らないのか分からないため・#3366)。黙って待ち続けない。"
  (defn #^ None __init__ [self #^ str step #^ float limit-seconds #^ str observed]  ; defk にできない: 例外の構成子
    (.__init__ (super) (.format "版上げが止まった: {}(上限 {} 秒を越えた)— 最後の読み: {}" step limit-seconds observed))
    (setv self.step step self.limit-seconds limit-seconds self.observed observed)))


(defeffect ReadUpgradeState
  "版上げの Program が次へ進むかを決める読み(名簿と、終わっていない task)。答え = UpgradeState か UpgradeStateUnreachable。"
  {:answer (| UpgradeState UpgradeStateUnreachable)
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PublishDeclarations
  "DesireWorker / DesireCoordinator で書いた宣言を公開する(本番 = commit と merge の列への登録と main 入り・sim = 記憶の中の置き場が
   そのまま main なので何もしない)。答え = None。"
  {:answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ApplyDeclarations
  "公開した宣言を当てる(本番 = Flux の「すぐ読み直せ」の印・sim = 模擬の Flux の 1 回の当て)。答え = None(当たったかは
   ReadUpgradeState で読む)。"
  {:answer None
   :tags {:context "doeff-cluster" :role "intent"}})


(defrecord CleanBootPassed
  "入れ替え先の値で、コピーも状態も無い空の機体の起動が通った: target = worker の名か \"coordinator\"。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target))


(defrecord CleanBootRefused
  "入れ替え先の値で、空の機体の起動が通らなかった: target = worker の名か \"coordinator\"・reason = どこで落ちたか。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ str reason))


(defeffect ConfirmCleanBoot
  "入れ替え先の値(worker か coordinator)で、コピーも状態も無い空の機体の起動が通るかを確かめる — 起動の時に読む物(版の root・
   dotfiles の master など)が壊れた版を入れ替えてから、空の Pod が起動で落ちる形(2026-10-05 07:1x に 15 本の job が約 20 分止まった)を
   入れ替えの前に拾うため。本番 = 起動の道を空の環境で 1 回通す・sim = 筋書きの答え。答え = CleanBootPassed か CleanBootRefused。"
  {:fields [(: launch (| WorkerLaunch CoordinatorLaunch))]
   :answer (| CleanBootPassed CleanBootRefused)
   :tags {:context "doeff-cluster" :role "intent"}})


;; 自己起動の root の準備が断られた理由(閉じた語 — handler は自由な文でなく、このどれかで答える)。
;;   PREPARE-ROLE-UNKNOWN  = 入れ替え先の版の起動の script が準備の役を知らない(準備を始めていない)
;;   PLACE-UNAVAILABLE     = 保存先が準備を受けられない(今の版の process に届かない・保存先に書けない・準備に要る空きが無い など —
;;                           準備を始めていない)
;;   PREPARE-STOPPED       = 準備の間、同じ保存先で動いている今の版の process を守るためのしきい値(handler が持つ — 空き memory・周期の遅れ
;;                           など)を越えたので、handler が準備を途中で止めた。版が悪いのではなく保存先が混んでいた — 次の手は、保存先が
;;                           空いた時に同じ版で実行し直す。
;;   PREPARE-FAILED        = 準備が自分で 0 でない終了で終わった。その版の準備が通らない — 次の手は、実行し直さずに失敗の原因を直す
;;                           (PREPARE-STOPPED と分ける理由 = 次の手が違う)。
;;   READY-MARK-INCOMPLETE = 準備は 0 で終わったが、root の完成のマークが無いか、マークの中身が完成の形でない(終了の値だけを信じない)
(defenum BootRootRefusal PREPARE-ROLE-UNKNOWN PLACE-UNAVAILABLE PREPARE-STOPPED PREPARE-FAILED READY-MARK-INCOMPLETE)


(defrecord BootRootAlreadyPrepared
  "入れ替え先の版の自己起動の root は、保存先に準備済みだった(何もしなかった): target = worker の名か \"coordinator\"・
   previous-root-present = 上げる前の版(今 動いている版)の root が保存先に在るか — 戻し先が残っているかを、版上げ全体をまとめる側が別に
   読まずに済むように載せる(上げる前の版が無い時は偽)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ bool previous-root-present))


(defrecord BootRootBuilt
  "入れ替え先の版の自己起動の root を、保存先に組んだ: target = worker の名か \"coordinator\"・seconds = 組むのにかかった秒・
   previous-root-present = 上げる前の版の root が保存先に在るか(BootRootAlreadyPrepared と同じ — 準備は足すだけで、今の版の root を消さない)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ float seconds)
  (#^ bool previous-root-present))


(defrecord BootRootRefused
  "入れ替え先の版の自己起動の root を、保存先に準備できなかった: target = worker の名か \"coordinator\"・reason = 拒否の理由(閉じた語)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ BootRootRefusal reason))


(defeffect PrepareBootRoot
  "入れ替え先の値(worker か coordinator)の版の自己起動の root(bytecode 込み)を、入れ替える対象の今の保存先に先に準備する — 作り直した後の起動が
   完成のマークを読んで進み、root の準備と初回の import(実測 15〜25 秒)が停止時間に乗らないようにするため(#3725)。handler は準備が終わるまで
   受け持ってから答える(呼び手は時間で読み直さない)。準備は足すだけで、今の版の root を消さない。上げる前の版は handler が自分で読む。
   本番 = 今の版の process の保存先で、入れ替え先の版の起動の script の準備の役を 1 回通す・sim = 模擬の保存先に足す(筋書きの答え)。
   答え = BootRootAlreadyPrepared か BootRootBuilt か BootRootRefused。"
  {:fields [(: launch (| WorkerLaunch CoordinatorLaunch))]
   :answer (| BootRootAlreadyPrepared BootRootBuilt BootRootRefused)
   :tags {:context "doeff-cluster" :role "intent"}})


(defrecord QuietWindowOpened
  "静かな時間帯に入った(答え手が確かめた — 入れ替えで切れて困る仕事が今は走っていない): target = 何を入れ替える前の待ちか。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target))


(defrecord QuietWindowMissed
  "上限の内に静かな時間帯に入らなかった: target = 何を入れ替える前の待ちか・reason = 何が静かにならなかったか(答え手が書く文)。
   AwaitQuietWindow の答えで、そのまま UpgradeRefused の断りの理由になる(当てる前に断った — 宣言は書いて公開した)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ str reason))


(defeffect AwaitQuietWindow
  "入れ替えで切れて困る仕事が走っていない時間帯(静かな時間帯)を待つ — 汎用の効果で、何を「切れて困る仕事」と読むかは答え手(配備する
   側の業務)が決める(#3772)。版上げの Program は coordinator の宣言を公開した後、当てる直前に 1 回出す(公開は数分かかり、その間に
   切れて困る仕事が始まりうるので、当てる直前に読む)。答え手は静かになるか timeout-seconds を
   越えるまで受け持ってから答える(呼び手は時間で読み直さない)。sim = すぐ QuietWindowOpened。答え = QuietWindowOpened か
   QuietWindowMissed。"
  {:fields [(: target str) (: timeout-seconds float)]
   :answer (| QuietWindowOpened QuietWindowMissed)
   :tags {:context "doeff-cluster" :role "intent"}})


(defrecord WorkerDrained
  "worker を drain し、答え手が数える worker の中の仕事が 0 になった(#3968): target = drain した worker の名。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target))


(defrecord WorkerDrainMissed
  "worker を drain したが、上限の内に worker の中の仕事が 0 にならなかった(#3968): target = drain した worker の名・
   reason = 何が終わらなかったか(答え手が書く文 — 残った仕事を名指す)。AwaitWorkerDrained の答えで、そのまま UpgradeRefused の断りの
   理由になる(当てる前に断った — 宣言は書いて公開した・走っている仕事は止めない)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ str reason))


(defeffect AwaitWorkerDrained
  "入れ替える worker を drain し(新しい仕事を受けない状態にする)、worker の中で走っている仕事が終わって 0 になったのを確かめる —
   汎用の効果で、drain の印の置き方と「worker の中で走っている仕事」の数え方は答え手(配備する側の業務)が決める(#3968 —
   coordinator の task に数えられない仕事、例えば長く生きる 1 つの task の中で回る子の仕事は、task の待ち(条 V2)では見えない)。
   版上げの Program は worker の宣言を公開した後、当てる直前に 1 回出す(公開は数分かかり、その間も worker は仕事を受けるので、drain は
   当てる直前に置く)。答え手は 0 になるか timeout-seconds を越えるまで受け持ってから答える(呼び手は時間で読み直さない)。走っている仕事を
   止めて 0 にしない。sim = すぐ WorkerDrained。答え = WorkerDrained か WorkerDrainMissed。"
  {:fields [(: launch WorkerLaunch) (: timeout-seconds float)]
   :answer (| WorkerDrained WorkerDrainMissed)
   :tags {:context "doeff-cluster" :role "intent"}})


(defeffect ReleaseWorkerDrain
  "AwaitWorkerDrained で worker に置いた drain を外す(worker が新しい仕事を受ける状態に戻す)— 汎用の効果で、外し方は答え手(配備する
   側の業務)が決める(#4177 — drain は worker を作り直しても、頼んだ側が外すまで残る)。版上げの Program は AwaitWorkerDrained を出した
   後に必ず 1 回出す: 当てた worker が新しい版で live に戻った後と、drain の後に止まる時(中の仕事が 0 にならない・当てが落ちた・live に
   戻らない)。もう無い drain を外すのは成功。sim = 何もしない。答え = None。"
  {:fields [(: launch WorkerLaunch)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})


(defrecord UnverifiedWorkers
  "coordinator を入れ替える前の条 V1 の照らしで、確かめた版の組み合わせに無い版で動く宣言の内の worker が居た(#3772): target = 何の入れ替えを
   止めたか(\"coordinator\")・coordinator-commit = 入れ替え先の coordinator の版・workers = 組み合わせに無い版で live な宣言の内の worker
   (名と版 — 宣言の外の worker は入らない)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ str coordinator-commit)
  (#^ (get tuple #(RosterEntry ...)) workers))


(defrecord RollbackRootMissing
  "入れ替える前の版(今 動いている版)の自己起動の root が保存先に無い — 入れ替えた後に前の版へ戻す先が無いので始めない(#3772):
   target = 何の入れ替えを止めたか・root = 入れ替え先の版の root の準備の答え(previous-root-present が偽の物)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ (| BootRootAlreadyPrepared BootRootBuilt) root))


(defrecord QueuedTasksRemain
  "coordinator を当てる直前に、待ち行列の task が上限の内に空にならなかった(#3772 — 作り直した coordinator が worker の行を読めない間に
   queued の task を落とす形 #2440 を防ぐ条 V4 を、宣言と公開の数分の後にもう 1 度確かめた): target = 何の入れ替えを止めたか・
   tasks = 最後に読んだ時に queued だった task の id。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ (get tuple #(str ...)) tasks))


;; 入れ替えの前の手順が断った所(UpgradeRefused の欄 point — 閉じた語):
;;   BEFORE-DESIRE = 宣言を書く前(宣言を書かず公開もしていない — cluster は変わらない)
;;   BEFORE-APPLY  = 宣言を書いて公開した後、当てる前(当てていない — 配備する側は Flux を止めたまま扱う・自動では戻さない)
(defenum RefusalPoint BEFORE-DESIRE BEFORE-APPLY)


(defclass UpgradeRefused [RuntimeError]
  "入れ替えの前の手順が断ったので止まった。target = 何の入れ替えを止めたか・refusal = 拒否の答えそのもの(閉じた和: CleanBootRefused・
   BootRootRefused(準備の拒否の理由は閉じた語)・UnverifiedWorkers・RollbackRootMissing・QueuedTasksRemain・QuietWindowMissed・
   WorkerDrainMissed)・
   point = 断った所(RefusalPoint — 宣言を書く前か、宣言を書いて公開した後で当てる前か)。文も断った所を明示する。自動で戻さない。"
  (defn #^ None __init__ [self #^ str target
                          #^ (| CleanBootRefused BootRootRefused UnverifiedWorkers RollbackRootMissing QueuedTasksRemain QuietWindowMissed
                                WorkerDrainMissed) refusal
                          #^ RefusalPoint point]
    ;; defk にできない: 例外の構成子
    (setv what (match refusal
                 (CleanBootRefused :reason reason)
                   (.format "{} の入れ替え先の版で、空の機体の起動が通らない({})" target reason)
                 (BootRootRefused :reason reason)
                   (.format "{} の保存先に、入れ替え先の版の自己起動の root を準備できない({})" target reason)
                 (UnverifiedWorkers :coordinator_commit commit :workers workers)
                   (.format "{} を版 {} へ入れ替える前に、確かめた版の組み合わせに無い版で動く宣言の内の worker が居る({})"
                            target (cut commit 0 10)
                            (.join "・" (gfor e workers (.format "{}(版 {})" e.worker (cut (or e.doeff-commit "") 0 10)))))
                 (RollbackRootMissing)
                   (.format "{} の保存先に、上げる前の版の自己起動の root が無い(入れ替えた後に戻す先が無い)" target)
                 (QueuedTasksRemain :tasks tasks)
                   (.format "{} を当てる前に、待ち行列の task が上限の内に空にならない(queued: {})" target (.join "・" tasks))
                 (QuietWindowMissed :reason reason)
                   (.format "{} を当てる前に、静かな時間帯が上限の内に来ない({})" target reason)
                 (WorkerDrainMissed :reason reason)
                   (.format "{} を drain したが、当てる前に中の仕事が上限の内に 0 にならない({})" target reason)))
    (setv where (match point
                  RefusalPoint.BEFORE-DESIRE "宣言は書いていない"
                  RefusalPoint.BEFORE-APPLY "宣言は書いて公開したが、当てていない(自動では戻さない)"))
    (.__init__ (super) (.format "版上げを止めた: {} — {}" what where))
    (setv self.target target self.refusal refusal self.point point)))


(defrecord WaitReached
  "版上げの待ちが条件に届いた: state = 条件が真になった時の名簿(呼び手がその同じ状態で次を判定する)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ UpgradeState state))


(defrecord WaitExpired
  "版上げの待ちが上限を越えた: observed = 最後に読んだ物のうちその待ちに効く所の文・last = 最後に読めた名簿(1 度も読めなければ None)。
   呼び手が UpgradeStalled で落ちるか、最後の名簿から断りの理由を組んで UpgradeRefused で止まるかを決める(#3772)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str observed)
  (#^ (| UpgradeState None) last))


(defrecord CoordinatorUpgraded
  "upgrade-coordinator の答え(#3772): root = coordinator の入れ替え先の版の root の準備の答え(上げる前の版の root が在る物 — 無ければ
   断っている)・undeclared = 宣言の外で、版の組み合わせの照らしと待ちから外した worker(名・live・版 — 黙って外さないため結果に必ず載せる)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ (| BootRootAlreadyPrepared BootRootBuilt) root)
  (#^ (get tuple #(RosterEntry ...)) undeclared))


(defrecord ClusterUpgraded
  "upgrade-cluster の答え: workers = worker ごとの root の準備の答え(入れ替えた順)・coordinator = coordinator の入れ替えの答え。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ (get tuple #((| BootRootAlreadyPrepared BootRootBuilt) ...)) workers)
  (#^ CoordinatorUpgraded coordinator))
