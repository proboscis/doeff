;;; worker と coordinator を新しい版へ入れ替える版上げの型と effect(#3366 の単位 3)。版上げの Program(shared/core/upgrade_program.hy)が
;;; 使い、条 V1〜V5 の判定(coordinator/core/upgrade_invariants.hy)が同じ記録を読む。
;;;
;;;   (<- state (ReadUpgradeState))       ; 名簿(worker ごとの live と動いている版)と、終わっていない task の写し
;;;   (<- (PublishDeclarations))          ; DesireWorker / DesireCoordinator で書いた宣言を公開する(本番 = commit と merge の列・sim = 何もしない)
;;;   (<- (ApplyDeclarations))            ; 公開した宣言を当てる(本番 = Flux の「すぐ読み直せ」の印・sim = 模擬の Flux の 1 回の当て)
;;;   (<- (ConfirmCleanBoot launch))      ; 入れ替え先の値で、コピーも状態も無い空の機体の起動が通るか(Desire の前に・#3366 の単位 5a)
;;;   (<- answer (PrepareBootRoot launch)) ; 入れ替え先の版の自己起動の root を、今の置き場に先に準備する(確かめの後・Desire の前に・#3725)
;;;
;;; 答え手は本番と sim で分かれる(本番の答え手は配備する側の repo — 単位 5 の前に形を決める)。待ちは時間で読み直さず、coordinator の
;;; 版の変化(AwaitRunnersChange)で起きる。どの待ちも上限(UpgradeLimits — 宣言の値)を持ち、越えたら UpgradeStalled で名指しで落ちる。
;;; 空の起動か root の準備が断られたら UpgradeRefused で名指しで止まる(宣言を書かず公開もしない・自動で戻さない)。
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


(defrecord RosterEntry
  "名簿の 1 台の写し: worker = 名・live = 生きていたか(作り直した直後の新しい世代が名乗り終える前は数えない)・doeff-commit = その
   worker の今の世代が動いている doeff の版。None = 読み手がその worker の版を読めない(配備する側が宣言を書けない worker)— 名簿には
   coordinator の知る worker を全部 載せ、版の読めない worker は条 V1 で新しい版と数えない(#3366)。unread-reason = 版を読めない訳
   (読み手が書く汎用の文 — 入れ替えの途中で新しい世代が準備完了でない・宣言を書けない worker など。版を読めた時は None)。待ちが上限で
   止まった時の文に載り、何が戻らないのかを名指す。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str worker)
  (#^ bool live)
  (#^ (| str None) doeff-commit)
  (setv #^ (| str None) unread-reason None))


(defrecord PendingTask
  "まだ終わっていない task の写し: phase = queued か assigned・worker = assigned の時に置かれた worker の名(queued は None)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str task)
  (#^ PendingPhase phase)
  (#^ (| str None) worker))


(defrecord UpgradeState
  "版上げの Program が次へ進むかを決める読み: roster = 名簿の写し・tasks = 終わっていない task の写し。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ (get tuple #(RosterEntry ...)) roster)
  (#^ (get tuple #(PendingTask ...)) tasks))


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
  "入れ替えを始めた瞬間(古い process が止まる瞬間)の、入れ替える物の置き場の写し 1 つ: start = その入れ替えの記録・prepared = その瞬間に
   置き場に準備済み(完成の印つき)で在った自己起動の root の版。条 V5 の入力。UpgradeStart の欄にしない訳 = 名簿と task は coordinator の
   読み・置き場は準備の答え手(PrepareBootRoot)の読みで、宣言を当てるだけの記録の作り手は置き場を知らない(#3725)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ UpgradeStart start)
  (#^ (get tuple #(str ...)) prepared))


(defrecord UpgradeLimits
  "版上げの待ちの上限(秒 — 宣言の値): drain = 入れ替える worker に置かれた task が終わるまで・return = 当てた worker / coordinator が
   新しい版で戻るまで・queue = coordinator を入れ替える前に待ち行列が空になるまで。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ float drain-seconds)
  (#^ float return-seconds)
  (#^ float queue-seconds))


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


;; 自己起動の root の準備が断られた訳(閉じた語 — 答え手は自由な文でなく、このどれかで答える)。
;;   PREPARE-ROLE-UNKNOWN  = 入れ替え先の版の起動の script が準備の役を知らない(準備を始めていない)
;;   PLACE-UNAVAILABLE     = 置き場が準備を受けられない(今の版の process に届かない・置き場に書けない・準備の分の余りが無い など —
;;                           準備を始めていない)
;;   PREPARE-STOPPED       = 準備の間、同じ置き場で動いている今の版の process を守る線(答え手が持つ — 余りの memory・拍の遅れ など)に
;;                           当たったので、答え手が準備を途中で止めた(版が悪いのではなく、置き場が込んでいた — 後で撃ち直せる)
;;   PREPARE-FAILED        = 準備が自分で 0 でない終了で終わった(その版の準備が通らない)
;;   READY-MARK-INCOMPLETE = 準備は 0 で終わったが、root の完成の印が無いか、印の中身が完成の形でない(終了の値だけを信じない)
(defenum BootRootRefusal PREPARE-ROLE-UNKNOWN PLACE-UNAVAILABLE PREPARE-STOPPED PREPARE-FAILED READY-MARK-INCOMPLETE)


(defrecord BootRootAlreadyPrepared
  "入れ替え先の版の自己起動の root は、置き場に準備済みだった(何もしなかった): target = worker の名か \"coordinator\"・
   previous-root-present = 上げる前の版(今 動いている版)の root が置き場に在るか — 戻し先が残っているかを、回のまとめ役が別に読まずに
   済むように載せる(上げる前の版が無い時は偽)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ bool previous-root-present))


(defrecord BootRootBuilt
  "入れ替え先の版の自己起動の root を、置き場に組んだ: target = worker の名か \"coordinator\"・seconds = 組むのにかかった秒・
   previous-root-present = 上げる前の版の root が置き場に在るか(BootRootAlreadyPrepared と同じ — 準備は足すだけで、今の版の root を消さない)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ float seconds)
  (#^ bool previous-root-present))


(defrecord BootRootRefused
  "入れ替え先の版の自己起動の root を、置き場に準備できなかった: target = worker の名か \"coordinator\"・reason = 断りの訳(閉じた語)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ BootRootRefusal reason))


(defeffect PrepareBootRoot
  "入れ替え先の値(worker か coordinator)の版の自己起動の root(bytecode 込み)を、その物の今の置き場に先に準備する — 作り直した後の起動が
   完成の印を読んで進み、root の準備と初回の import(実測 15〜25 秒)が止まりに乗らないようにするため(#3725)。答え手は準備が終わるまで
   受け持ってから答える(呼び手は時間で読み直さない)。準備は足すだけで、今の版の root を消さない。上げる前の版は答え手が自分で読む。
   本番 = 今の版の process の置き場で、入れ替え先の版の起動の script の準備の役を 1 回通す・sim = 模擬の置き場に足す(筋書きの答え)。
   答え = BootRootAlreadyPrepared か BootRootBuilt か BootRootRefused。"
  {:fields [(: launch (| WorkerLaunch CoordinatorLaunch))]
   :answer (| BootRootAlreadyPrepared BootRootBuilt BootRootRefused)
   :tags {:context "doeff-cluster" :role "intent"}})


(defclass UpgradeRefused [RuntimeError]
  "入れ替えの前の手(空の機体の起動の確かめ・自己起動の root の準備)が断られたので、宣言を書かず公開もせずに止まった。target = 何の
   入れ替えを止めたか・refusal = 断りの答えそのもの(CleanBootRefused か BootRootRefused — 準備の断りの訳は閉じた語)。自動で戻さない。"
  (defn #^ None __init__ [self #^ str target #^ (| CleanBootRefused BootRootRefused) refusal]  ; defk にできない: 例外の構成子
    (.__init__ (super)
               (match refusal
                 (CleanBootRefused :reason reason)
                   (.format "版上げを止めた: {} の入れ替え先の版で、空の機体の起動が通らない({})— 宣言は書いていない" target reason)
                 (BootRootRefused :reason reason)
                   (.format "版上げを止めた: {} の置き場に、入れ替え先の版の自己起動の root を準備できない({})— 宣言は書いていない"
                            target reason)))
    (setv self.target target self.refusal refusal)))
