;;; worker と coordinator を新しい版へ入れ替える版上げの型と effect(#3366 の単位 3)。版上げの Program(shared/core/upgrade_program.hy)が
;;; 使い、条 V1〜V4 の判定(coordinator/core/upgrade_invariants.hy)が同じ記録を読む。
;;;
;;;   (<- state (ReadUpgradeState))       ; 名簿(worker ごとの live と動いている版)と、終わっていない task の写し
;;;   (<- (PublishDeclarations))          ; DesireWorker / DesireCoordinator で書いた宣言を公開する(本番 = commit と merge の列・sim = 何もしない)
;;;   (<- (ApplyDeclarations))            ; 公開した宣言を当てる(本番 = Flux の「すぐ読み直せ」の印・sim = 模擬の Flux の 1 回の当て)
;;;
;;; 答え手は本番と sim で分かれる(本番の答え手は配備する側の repo — 単位 5 の前に形を決める)。待ちは時間で読み直さず、coordinator の
;;; 版の変化(AwaitRunnersChange)で起きる。どの待ちも上限(UpgradeLimits — 宣言の値)を持ち、越えたら UpgradeStalled で名指しで落ちる。
(require doeff-hy.macros [val defeffect])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import enum [StrEnum])


;; 入れ替える物の種類。
(defenum UpgradeKind WORKER COORDINATOR)

;; 写しに載せる task の phase(coordinator の /resources/Task の queued と assigned — assigned は置かれた・走り中)。
(defenum PendingPhase QUEUED ASSIGNED)


(defrecord RosterEntry
  "名簿の 1 台の写し: worker = 名・live = 生きていたか(作り直した直後の新しい世代が名乗り終える前は数えない)・doeff-commit = その
   worker の今の世代が動いている doeff の版。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str worker)
  (#^ bool live)
  (#^ str doeff-commit))


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


(defrecord UpgradeLimits
  "版上げの待ちの上限(秒 — 宣言の値): drain = 入れ替える worker に置かれた task が終わるまで・return = 当てた worker / coordinator が
   新しい版で戻るまで・queue = coordinator を入れ替える前に待ち行列が空になるまで。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ float drain-seconds)
  (#^ float return-seconds)
  (#^ float queue-seconds))


(defclass UpgradeStalled [RuntimeError]
  "版上げの待ちが上限を越えた(置かれた task が終わらない・worker が戻らない・待ち行列が空にならない)。step = どの待ちで止まったか・
   limit-seconds = その上限。黙って待ち続けない。"
  (defn #^ None __init__ [self #^ str step #^ float limit-seconds]  ; defk にできない: 例外の構成子
    (.__init__ (super) (.format "版上げが止まった: {}(上限 {} 秒を越えた)" step limit-seconds))
    (setv self.step step self.limit-seconds limit-seconds)))


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
