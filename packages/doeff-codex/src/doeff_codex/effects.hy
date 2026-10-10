;;; doeff-codex の公開 effect と、その答えの型。
;;;
;;; CodexStartTurn / CodexSteerTurn / CodexInterruptTurn / CodexReadTurnEvents / CodexAnswerRequest / CodexCloseSession と、検の口
;;; CodexLaunchCount。
;;; 単位は「codex の会話(thread)と、その上のターン」。process の単位の操作(起こす・stdin に書く・降ろす・pid)は公開しない —
;;; handler の内側の語彙。失敗は例外ではなく答えの型で返す(成功の型と失敗の型の判別可能な union)。handler の実装の誤り(I/O の
;;; 予期しない例外)だけが例外として上がる。手本 = doeff-claude-code の effects.hy(同じ単位・同じ答えの形)。
(require doeff-hy.macros [defeffect val])
(val MODULE-TAGS {:context "codex" :role "intent"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_codex.values [CodexSessionSpec CodexTurn CodexEvent CodexInput FreshThread ResumeThread])
(import doeff_codex.lines [TurnEnded])


;; --- ターンの終わり(lines.TurnEnded か、終わりの行を読む前に process が消えた)----------------------------------

(defrecord BackendLost
  "終わりの行(turn/completed)を読む前に process が消えた(OOM・kill・host の再起動)。次のターンは同じ ResumeThread で頼めばよい
   (新しい process が thread/resume する)。exit-code = 降りた process の終了の code(信号なら負)/ stderr-tail = stderr の末尾。"
  {:tags {:context "codex" :role "type"}}
  (#^ str detail)
  (setv #^ (| int None) exit-code None)
  (setv #^ (| str None) stderr-tail None))

(val CodexTurnEnd (| TurnEnded BackendLost))


;; --- 成功の答え ---------------------------------------------------------------------------------

(defrecord TurnStarted
  "ターンが始まった: turn = ターンの参照(thread-id は新しい会話なら codex が決めた id)。"
  {:tags {:context "codex" :role "type"}}
  (#^ CodexTurn turn))

(defrecord Steered
  "走っているターンに入力を足した(codex が受けた — ターンの次の区切りで読む)。"
  {:tags {:context "codex" :role "type"}})

(defrecord InterruptRequested
  "止めを頼んだ(終わりは CodexReadTurnEvents の TurnEnded — 状態 INTERRUPTED — で届く)。"
  {:tags {:context "codex" :role "type"}})

(defrecord TurnEventPage
  "events = after-seq より後の出来事(重複も欠落もなく・seq の順)/ next-seq = 次に渡す after-seq / end = ターンの終わり(まだなら None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (get tuple #(CodexEvent ...)) events)
  (#^ int next-seq)
  (#^ (| TurnEnded BackendLost None) end))

(defrecord Answered
  "codex からの要求(ServerRequest)に答えた。"
  {:tags {:context "codex" :role "type"}})

(defrecord SessionClosed
  "会話を閉じた: was-running = 走っているターンが在ったか(在ればそのターンは BackendLost で終わる)。"
  {:tags {:context "codex" :role "type"}}
  (#^ bool was-running))


;; --- 失敗の答え ---------------------------------------------------------------------------------

(defrecord ThreadUnknown
  "続きを頼んだ thread を codex が知らない(thread/resume が誤りで答えた)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str detail))

(defrecord TurnInFlight
  "同じ会話に走っているターンが在る。"
  {:tags {:context "codex" :role "type"}}
  (#^ CodexTurn turn))

(defrecord LaunchFailed
  "process が要求に答える前に降りたか、答えを待つ上限の秒を過ぎた: detail = どの段で(initialize・thread/start・turn/start など)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str detail)
  (setv #^ (| int None) exit-code None)
  (setv #^ str stderr-tail ""))

(defrecord RequestRefused
  "codex が要求を誤りで答えた(JSON-RPC の error): method = 要求の名 / code・message = 誤りの code と文。"
  {:tags {:context "codex" :role "type"}}
  (#^ str method)
  (#^ int code)
  (#^ str message))

(defrecord NoTurnInFlight
  "名指したターンは走っていない(終わった・知らない)。"
  {:tags {:context "codex" :role "type"}}
  (#^ CodexTurn turn))

(defrecord UnknownTurn
  "名指したターンを handler が知らない。"
  {:tags {:context "codex" :role "type"}}
  (#^ CodexTurn turn))

(defrecord NoSuchRequest
  "名指した要求の id は、走っているターンの答えを待つ要求に無い。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) request-id))

(defrecord ProcessStillAlive
  "降ろす手順(EOF → SIGTERM → SIGKILL)を全部踏んでも降りない。"
  {:tags {:context "codex" :role "type"}}
  (#^ str detail))

(val StartTurnOutcome (| TurnStarted ThreadUnknown TurnInFlight LaunchFailed RequestRefused))


;; --- effect ----------------------------------------------------------------------------------

(defeffect CodexStartTurn
  "ターンを始める。process を起こすか使い回すかの判断はこの effect の handler の中の 1 か所だけ(同じ会話・同じ宣言で生きた process が
   在れば使い回し、無ければ起こして thread/start か thread/resume する)。input = 利用者の入力(文字と画像)。
   答え = TurnStarted | ThreadUnknown | TurnInFlight | LaunchFailed | RequestRefused。"
  {:fields [(: origin (| FreshThread ResumeThread)) (: spec CodexSessionSpec) (: input CodexInput)]
   :pre [(: origin (| FreshThread ResumeThread)) (: spec CodexSessionSpec) (: input CodexInput)]
   :answer StartTurnOutcome
   :tags {:context "codex" :role "intent"}})

(defeffect CodexSteerTurn
  "走っているターンに入力を足す(turn/steer — codex はターンの次の区切りで読み、同じターンが続く)。名指したターンが走っていなければ
   NoTurnInFlight、codex が断れば RequestRefused(ターンが終わりかけていた・足せない種類のターン)、答えの前に process が降りれば
   LaunchFailed。答え = Steered | NoTurnInFlight | RequestRefused | LaunchFailed。"
  {:fields [(: turn CodexTurn) (: input CodexInput)]
   :pre [(: turn CodexTurn) (: input CodexInput)]
   :answer (| Steered NoTurnInFlight RequestRefused LaunchFailed)
   :tags {:context "codex" :role "intent"}})

(defeffect CodexInterruptTurn
  "走っているターンを途中で止める(turn/interrupt)。答え = InterruptRequested | NoTurnInFlight。"
  {:fields [(: turn CodexTurn)]
   :pre [(: turn CodexTurn)]
   :answer (| InterruptRequested NoTurnInFlight)
   :tags {:context "codex" :role "intent"}})

(defeffect CodexReadTurnEvents
  "ターンの出来事を読む: after-seq より後の出来事を、新しい出来事か終わりが来るか wait-up-to 秒が過ぎるまで待って返す。
   答え = TurnEventPage | UnknownTurn。"
  {:fields [(: turn CodexTurn) (: after-seq int) (: wait-up-to float)]
   :pre [(: turn CodexTurn) (: after-seq int) (: wait-up-to float)]
   :answer (| TurnEventPage UnknownTurn)
   :tags {:context "codex" :role "intent"}})

(defeffect CodexAnswerRequest
  "codex からの要求(出来事の ServerRequest — 道具の許可の問いなど)に答える: result = 答えの中身(method ごとに形が違うので、上の層が
   method で選んだ型を dump した JSON を中を読まずに渡す)。答え = Answered | NoSuchRequest。"
  {:fields [(: turn CodexTurn) (: request-id (| int str)) (: result OpaqueJson)]
   :pre [(: turn CodexTurn) (: request-id (| int str)) (: result OpaqueJson)]
   :answer (| Answered NoSuchRequest)
   :tags {:context "codex" :role "intent"}})

(defeffect CodexCloseSession
  "会話を閉じる(冪等 — process を降ろす)。走っているターンは BackendLost で終わる。答え = SessionClosed | ProcessStillAlive。"
  {:fields [(: thread-id str) (: reason str)]
   :pre [(: thread-id str) (: reason str)]
   :answer (| SessionClosed ProcessStillAlive)
   :tags {:context "codex" :role "intent"}})

(defeffect CodexLaunchCount
  "検の口: 会話のために起こした process の数(使い回しを確かめるため — 同じ process が次のターンを受けたなら増えない)。答え = 数。"
  {:fields [(: thread-id str)]
   :pre [(: thread-id str)]
   :answer int
   :tags {:context "codex" :role "intent"}})
