;;; fake の handler — 本番の handler と同じ公開 effect に、memory の上で答える(設計 8 節)。
;;;
;;; 時刻は doeff-time(GetMonotonic で手番の筋書きを進め、GetTime で行の at を刻む)。仮想の時計(sim-time-handler)の下では
;;; 道具の秒数も待ちも一瞬で進む。筋書き = 入力の本文と会話の記憶(それまでの入力)→ FakeReply(返事の本文・道具の秒数・
;;; 許可の問いの要否・終わり方〔完了・失敗・process が消える〕・usage・途中の本文の行の数・最後の本文を分ける差分の片の数)。
;;; 行は本番と同じ ClaudeStreamLine / ClaudeLineKind の型で出す(型を 2 つ作らない)。
;;;
;;; 世界 = 家の中身(transcripts・activity — disk の上の物)と process の中の会話(sessions)。restarted は同じ家の上で process だけを
;;; 作り直した世界(前の process の会話は前の世界で走り続け、新しい世界からは見えない — 上の層の process の作り直しの模擬)。
;;;
;;; 本番と共通の不変条件(1 つの会話に走る手番は多くとも 1 つ・StartTurn 1 回に終わりちょうど 1 つ・seq の単調増加・
;;; FreshSession の id の重複と ResumeSession の不在の断り・手番の外の足す / 止めるの断り・止めた後は Interrupted・閉じるは冪等・
;;; process の死の注入で BackendLost・次の ResumeSession は通る)を同じ筋書きの検で確かめる。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass field replace])
(import uuid)
(import doeff_time [GetMonotonic GetTime WaitWithin])
(import doeff_core_effects [CreateExternalPromise ExternalPromise])
(import doeff_hy._frozen [FrozenMap FrozenJson frozen-json-object])
(import doeff_claude_code._values [ClaudeTurn ClaudeSessionSpec FreshSession ResumeSession ForkSession Rebuilt LinkFromHome
                                  IMAGE-MIMES LentCredential])
(import doeff_claude_code._lines [ClaudeStreamLine Init AssistantMessage PartialMessage ToolCall ToolAnswer ToolResult InputFate PermissionRequested tap-lines
                                 TaskEvent TurnResult Completed Failed Interrupted BackendLost ClaudeLineKind ClaudeTurnEnd Usage
                                 ModelWindow ModelUsage model-cost-gap merged-windows DeltaKind RateLimit AccountLimitHit RATE-LIMIT-REJECTED
                                 ASSISTANT-ERROR-RATE-LIMIT StopHookFeedback AccountRefusalHit CompactStarted CompactEnded CompactOutcome CompactBoundary
                                 HookNotice HookPhase SubagentTranscript SUBAGENT-TASK-TYPE
                                 ModelRefusalHit STOP-REASON-REFUSAL stderr-tail-within CallClosed])
(import doeff_hy._json_lines [jsonl-lines])
(import doeff [run])
(import doeff_claude_code._dialogue [limit-hit-after refusal-hit-after model-refusal-after])
(import doeff_claude_code._effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InputQueued InterruptRequested InputAlreadyRead TurnEventPage Answered SessionClosed
                                   SessionStatus SessionExported Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   SessionNotFound SessionIdInUse TurnInFlight AttachmentRefused NoTurnInFlight
                                   UnknownTurn NoSuchRequest ClaudeWarmSession SessionWarmed ClaudeLiveLimitExceeded
                                   ClaudeReplaceSessionCredential CredentialReplaced NoSuchSession SessionProcessDown
                                   HomeCredentialSession ClaudePrintOnce PrintAnswered PrintFailed PrintLimited PrintTimedOut])
(import doeff_claude_code._faults [ClaudeDropProcess ClaudeForgetSession ClaudeLiveProcess ClaudeEmitOutsideTurn LiveProcess
                                  NoLiveProcess StopReason])
(import doeff_claude_code._argv [launch-key])

(setv QUICK-TURN-SECONDS 0.1)
;; 仮想の時計は datetime(マイクロ秒の刻み)なので、秒の小数の足し算の端数で「期限の直前」に留まらないように
;; 期限の比べに刻み 1 つ分の余裕を置き、眠りは刻み以上にする。
(setv CLOCK-TICK 1e-6)
(setv MIN-SLEEP 1e-3)
;; 最後の本文の差分(PartialMessage の text_delta)どうしの間隔の秒。本物の CLI(--include-partial-messages)は生成の途中の本文を
;; 数十ミリ秒ごとの片で出し、確定の本文(assistant の行)はその後に出す。
(val DELTA-SECONDS 0.05)
;; 偽の手番の考えている間の差分の片 1 つの考えの文字列(替え玉の CLI tests/stub_cli/claude.hy の thinking_delta と同じ — #3789)。
(val FAKE-THINKING-PIECE "...")
;; 道具の命令を書いている間の差分の片の中身(本物の CLI の input_json_delta の partial_json — 替え玉の CLI と同じ綴り・#3974 の 3)。
(val FAKE-TOOL-INPUT-PIECE "{\"c")
(setv FAKE-CAPABILITIES #("msg_lifecycle_v1" "interrupt_receipt_v1"))
;; 止めるの受理(interrupt_receipt_v1)を名乗らない CLI の process の能力(FakeReply の interrupt-receipt が偽の手番)。
(val NO-RECEIPT-CAPABILITIES #("msg_lifecycle_v1"))
;; 偽の手番が呼ぶ道具の 1 つの呼び(tool_use の id と道具の名)。道具の結果の行(ToolResult)の答えは同じ id を名指す。呼びの命令と
;; 結果の中身は手番の返事(FakeReply の tool-input・tool-output・tool-error)が決める。
(val FAKE-TOOL-USE-ID "fake-tool")
(val FAKE-TOOL-NAME "Bash")
;; 偽の手番の本体の呼びの行が名乗る request_id の頭(本番の CLI の stream-json の assistant の行の request_id と同じ役 — 呼びの id の前に
;; 付ける・card ki-b99ace070bf0)。
(val FAKE-REQUEST-PREFIX "req_")
;; CLI が model の答えの代わりに自分で作って出す assistant の行(API の誤りの答え — 口座の側の断りの行など)が名乗る model の名(本番
;; 2026-10-08 22:32 の口座 cryptic-2 の断りの行)と、その行の usage(2026-10-07 の限度の答えの行が名乗った 0 — model を呼んでいない。
;; 断りの行も同じと推定)。
(val SYNTHETIC-MODEL "<synthetic>")
(val SYNTHETIC-USAGE (Usage :input-tokens 0 :output-tokens 0))
;; 口座の側の断りの手番を閉じる result の行の subtype(CLI は API の誤りの答えで終えた手番を subtype success・is_error true で閉じる —
;; 限度の答えの手番と同じ形と推定)。本物の状態機械は result の本文が空の時にこの subtype を手番の失敗の文にする。
(val REFUSAL-RESULT-SUBTYPE "success")
;; model の安全装置の断りの行の最上位の error(CLI の code — 断りの行を error invalid_request の API の誤りの行として作る)。
(val MODEL-REFUSAL-ERROR "invalid_request")
;; 500 以上の status の API の誤りの行の最上位の error(CLI の code — 混雑 529 ほかの誤りの行を error server_error の行として作る)。
(val API-ERROR-SERVER "server_error")


(defrecord StopHookRejection
  "Stop hook が 1 度差し戻す答え(#4020 — FakeReply の stop-hook-rejections の 1 つ): answer = 差し戻される答えの本文(空でない)・
   reason = 差し戻しの理由(CLI が手番へ注入する Stop hook feedback の本文の頭の後)。"
  (#^ str answer)
  (#^ str reason))


(defn #^ bool scripted-end? [reply]  ; defk にできない: FakeReply の __post-init__(defn)が値として呼ぶ
  "純粋: 筋書きの返事 reply(FakeReply)が本文で終わらない手番(失敗・process の消失・口座の側の断り・model の安全装置の断り)かを
   決める 1 か所。"
  (any (gfor end #(reply.fail reply.lose reply.account-refusal reply.model-refusal) (is-not end None))))


;; 偽の手番の道具の task の id(subagent を走らせない手番 — 走らせる手番は subagent の transcript の agent-id)。
(val FAKE-TASK-ID "fake-task")

(defclass [(dataclass :frozen True)] FakeReply []
  "筋書きの 1 手番の返事: text = 最後の本文・tool-seconds = 道具が走る秒数(0 = 道具なし)・
   tool-repeats = 同じ道具を続けて呼ぶ回数(1 以上・既定 1)。偽の CLI は道具の結果の直後に同じ道具(同じ名と命令 tool-input)をまた
   呼び、呼びと結果の対を tool-repeats 組出す — どの呼びも tool-seconds 秒走る(道具の秒の和は tool-repeats × tool-seconds)。呼びの id は
   1 番目が FAKE-TOOL-USE-ID、2 番目からは FAKE-TOOL-USE-ID-<n>(n = 2, 3, …)で、結果の行は同じ id を名指す(道具の task の id も同じ
   付け方)。最後の結果の後は 1 回の手番と同じく本文か筋書きの終わりへ進む。上の層が模擬で、同じ命令を何度も走らせる長い手番(sleep 110 を
   10 回 — card ki-85761baedba4)を数える事を確かめるため。2 以上は道具を走らせる手番(tool-seconds が正)だけで、許可の問い
   (needs-permission)と subagent(subagent-transcript)とは組まない(どちらも最初の呼びだけの筋書き)・
   needs-permission = 道具の前に許可の問いを出す・fail = 期限で Failed(detail = この文)で終わる・fail-api-status = fail の手番を
   API の誤りの答えの形で終える HTTP status(本物の CLI と同じ誤りの行と api_error_status つきの result の行を出し、Failed の
   api-error-status に載せる — 混雑 529 で終わる手番を上の層が見分ける事を模擬で確かめるため・api-error-turn。None = 今の形)・lose = 期限で process が消えて
   BackendLost(detail = この文)で終わる(fail・lose・account-refusal・model-refusal は多くとも 1 つ)・usage = Completed / Failed に載せる usage・
   cost-usd = Completed / Failed に載せる手番の額(USD — 本番の handler が累積の額の差から数える値の代わり。None = 名乗らない)・
   lines = 始めてから期限までの前半に、本文の行(AssistantMessage)を lines 行ほど等間隔に出す(出来事の量の多い手番 — 道具を続けて
   呼ぶ手番の期限は最初の呼びの期限)・
   think-seconds = 道具を使わずに考える秒(道具の行を出さずに長く走る手番)・
   interrupt-receipt = この手番の CLI の process が init で interrupt_receipt_v1 を名乗るか(本物の handler は手番ごとに process を
   起こす)。偽 = 止めるは SIGINT の形で、読まれていない注入を捨てた入力(dropped-refs)として終える — 本物の対話の解釈
   (dialogue.hy の interrupt と on-result)が受理を名乗らない CLI を止める道(#3467)・
   deltas = 最後の本文を何片の差分(PartialMessage の text_delta)に分けて、確定の本文(AssistantMessage)の前に DELTA-SECONDS ごとに
   出すか(0 = 差分を出さない。本物の CLI の --include-partial-messages の行の順 — 差分の列 → 確定の本文 → result)。片の連結は
   確定の本文と同じで、どの片も空でない(deltas は本文の字数以下)。失敗と消失(fail・lose)の手番は本文を出さないので組まない・
   tool-input = 道具の呼び(と許可の問い)の命令 — tool_use の block の input と同じ JSON の object(深く凍らせる)・
   tool-output = 道具の結果の中身の本文・tool-error = 道具が誤りで終えたか(tool-input から tool-error は、道具を呼ぶ手番 —
   tool-seconds > 0 か needs-permission — の呼びと結果の行に載る。上の層が道具の命令と出力を運ぶ事を確かめるため・#3744)・
   last-call-usage・last-call-model = この手番の CLI が本体の会話の assistant の行で名乗る呼びの usage と model・model-windows = result の
   行で名乗る model ごとの窓(本物の CLI が全部の行で名乗るのと同じに、この手番の全部の assistant の行・result の行に載せる。手番の
   終わりの 3 欄は、本番の状態機械と同じ規則で出した行から数える — 上の層が会話の context の大きさを運ぶ事を確かめるため・#3744)・
   thinking-deltas = 手番の始め(init の後)に出す考えている間の差分の行(PartialMessage・DeltaKind THINKING)の数・tool-input-deltas =
   道具の呼びの行の直前に出す道具の命令の差分の行(DeltaKind TOOL-INPUT)の数(道具を呼ぶ手番だけ — 本物の CLI の
   --include-partial-messages と同じ種類の行・#3746 (a))・
   account-limit = この手番が口座の限度に当たる筋書き(#3983)。偽の CLI は本物と同じ行 — 拒まれた rate_limit_event の行(window と
   resets-at が在る時)と、本体の assistant の行の error rate_limit と限度の文(text が在る時)— を終わりの前に出し、終わり(Completed /
   Failed)の account-limit は出した行から本物の状態機械と同じ規則(dialogue.hy の limit-hit-after)で数える。上の層(doeff-agents の
   adapter とその上の手番を起こす層)が模擬で「この口座の枠が尽きた」を受け取るため。行に出せない値(窓も文も無い・窓の無い戻る刻)と、process の
   消える手番(lose — 終わりに欄が無い)は断る・
   account-refusal = この手番の要求を口座の側が断る筋書き(AccountRefusalHit — 本番 2026-10-08 22:32 の口座 cryptic-2 の
   oauth_org_not_allowed)。偽の CLI は本物と同じ行 — 本体の assistant の行(model SYNTHETIC-MODEL・error の語・断りの文)と、その後の
   result の行(subtype success・is_error true・本文 = 断りの文)— を出して Failed で終え、終わりの account-refusal は出した行から本物の
   状態機械と同じ規則(dialogue.hy の refusal-hit-after)で数える。上の層が模擬で「この口座は使えない」を受け取るため。断りは手番の
   終わり方の 1 つなので fail・lose と組まない(本文を出さないので deltas・stop-hook-rejections とも組まない)。道具を呼ぶ手番
   (tool-seconds)なら、道具の結果の後に断る・
   model-refusal = この手番の要求を model の安全装置が断る筋書き(ModelRefusalHit — 本番 2026-10-20 の Fable 5.1 の
   reasoning_extraction の誤フラグ)。偽の CLI は本物と同じ行 — 本体の assistant の行(model = 断りの model・error invalid_request・
   stop_reason refusal・stop_details の category・断りの文)と、その後の result の行(subtype success・is_error true・本文 = 断りの文)—
   を出して Failed で終え、終わりの model-refusal は出した行から本物の状態機械と同じ規則(dialogue.hy の model-refusal-after)で数える。
   上の層が模擬で「この transcript はこの model で続けられない」を受け取るため。組み方の制約は account-refusal と同じ・
   stop-hook-rejections = Stop hook が答えを差し戻す筋書き(StopHookRejection の列・#4020)。偽の CLI は本物と同じ順 — 差し戻される
   答えの本文の行(AssistantMessage)→ 差し戻しの行(StopHookFeedback)を 1 つずつ — を最後の本文を書き始める前に出し、手番の終わりの
   本文は最後の本文だけ(本物の result の行と同じ)。上の層が差し戻された答えを見分けて画面から落とす事を模擬で確かめるため。本文で
   終わる手番だけ(fail・lose の手番は本文を出さない)・
   compactions = CLI が会話を自動で圧縮する筋書き(CompactBoundary か CompactEnded の列 — 圧縮 1 回に 1 つ・#4189)。偽の CLI は本物と
   同じく、要求の前(init の後・考えている間の差分の前)に圧縮の始まりの行(CompactStarted)と終わりの行を 1 組ずつ出し、手番はそのまま続く。
   CompactBoundary = 成功した圧縮(終わりの status null の行 CompactEnded SUCCEEDED と境目の行の 2 行)・CompactEnded = その終わり方で
   終わった圧縮(失敗・中断・圧縮しなかった — 境目の行は出ない・card ki-8239be27d5f6)。上の層が無人のターンの圧縮を
   記録する事を模擬で確かめるため・
   subagent-transcript = 道具が subagent を走らせる筋書き(SubagentTranscript — 本番の handler が subagent の task の報せで transcript
   から読む物そのもの・親の道具の呼びの id は FAKE-TOOL-USE-ID・道具を走らせる手番だけ)。偽の CLI は本番の CLI(背景の task を止めた
   2.1.292)と同じく subagent の呼びの行を出さず、道具の始まりに subagent の task の始まり(TaskEvent・SUBAGENT-TASK-TYPE)を、道具の終わりに
   その報せを出し、報せの直後に本番の handler と同じくこの行を出す(道具の途中で止めた手番は出さない)。上の層が subagent の呼びの使用量を
   記録する事を模擬で確かめるため(card ki-8239be27d5f6・ki-e798888dce4e)・
   hooks = 要求の前に CLI が回す hook の行の筋書き(HookNotice の列 — 条件つきルールの助言を返す UserPromptSubmit の hook の応答ほか・
   ki-d8b473480303)。偽の CLI は本物(--include-hook-events)と同じく init の後に 1 つずつ出し、手番はそのまま
   続く。Stop hook の差し戻し(stop-hook-rejections)の Stop の hook の応答の行は、差し戻しの筋書きが出す(stop-hook-notice-of)。上の層が
   hook の結果を記録して画面に出す事を模擬で確かめるため・
   lose-exit-code・lose-stderr = lose の手番で消える process の終了 code と stderr(#4207)。BackendLost の欄 exit-code・stderr-tail に
   載せる — stderr は本物の状態機械と同じ上限で切る(lines.hy の stderr-tail-within)。上の層が模擬で「CLI がなぜ降りたか」を受け取る
   ため。None = 名乗らない(欄も None — 今の形)。lose の無い手番は断る・
   subagent-tool-output = 道具(本体の会話の呼び)が subagent を走らせ、その subagent の中の道具が答えた出力(card
   ki-b25d579bf48b)。偽の CLI は本体の呼びごとに、その結果の行の直前に subagent の中の道具の答えの行(ToolResult — 親 = その本体の
   呼びの id・答えた呼びの id = <本体の呼びの id>-sub)を出す — 本物の CLI の stream-json が subagent の user の行を
   parent_tool_use_id つきで出すのと同じ。上の層が subagent の中の道具の答えを本体の会話の答えと見分ける事を確かめるため。道具を
   走らせる手番(tool-seconds が正)だけ。None = 出さない・
   boundary-injections = 道具の結果の後、続けて呼ぶ次の道具の呼び(tool-repeats)の前に、読まれていない注入を読む(注入の started の
   行を出す — 本物の CLI が道具の境界で足された入力を読み、同じ手番のまま続けるのと同じ。card ki-b25d579bf48b)。読んだ注入の
   返事の本文は最後の本文に続ける。偽 = 今の形(本文を書き始める時と手番の終わりにだけ読む)。"
  (#^ str text)
  (setv #^ float tool-seconds 0.0)
  ;; 同じ道具を続けて呼ぶ回数(どの呼びも tool-seconds 秒走る)。
  (setv #^ int tool-repeats 1)
  (setv #^ bool needs-permission False)
  (setv #^ (| str None) fail None)
  (setv #^ (| str None) lose None)
  ;; fail の手番を API の誤りの答えの形(誤りの行と api_error_status つきの result の行)で終える時の HTTP status(None = 今の形)。
  (setv #^ (| int None) fail-api-status None)
  ;; lose の手番で消える process の終了 code と stderr(名乗らない = None)。
  (setv #^ (| int None) lose-exit-code None)
  (setv #^ (| str None) lose-stderr None)
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  ;; Completed / Failed に載せる手番の model ごとの使用量(本番の handler が modelUsage の累積の差から数える増分の代わり — None = 名乗らない)。
  ;; 照合の訳は本番と同じ規則(model-cost-gap)で cost-usd と比べて決める(card ki-1d986d4f0007)。
  (setv #^ (| (get tuple #(ModelUsage ...)) None) model-usage None)
  (setv #^ int lines 0)
  ;; 道具なしで考える秒(0 = 既定の短い手番)。本文の行だけで、道具の行を出さずにこの秒まで走る。
  (setv #^ float think-seconds 0.0)
  ;; この手番の CLI が止めるの受理を名乗るか(偽 = 止めるは SIGINT の形で、読まれていない注入を捨てる)。
  (setv #^ bool interrupt-receipt True)
  ;; 最後の本文を分ける差分の片の数(0 = 差分を出さない)。
  (setv #^ int deltas 0)
  ;; 道具の呼びの命令(tool_use の input と同じ JSON の object)と、結果の中身の本文・誤りの印。
  (setv #^ (get FrozenMap FrozenJson) tool-input (field :default-factory (fn [] (FrozenMap {"command" "fake"}))))
  (setv #^ str tool-output "")
  (setv #^ bool tool-error False)
  ;; 本体の会話の呼びの usage と model、model ごとの窓(名乗らない = None・空)。
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  ;; 考えている間の差分の行の数(手番の始め — init の後)と、道具の命令の差分の行の数(道具の呼びの行の直前・道具を呼ぶ手番だけ)。
  (setv #^ int thinking-deltas 0)
  (setv #^ int tool-input-deltas 0)
  ;; 口座の限度に当たる筋書き(当たらない = None)。
  (setv #^ (| AccountLimitHit None) account-limit None)
  ;; 口座の側が要求を断る筋書き(断らない = None)。
  (setv #^ (| AccountRefusalHit None) account-refusal None)
  ;; model の安全装置が要求を断る筋書き(断らない = None)。
  (setv #^ (| ModelRefusalHit None) model-refusal None)
  ;; Stop hook が答えを差し戻す筋書き(差し戻さない = 空)。
  (setv #^ (get tuple #(StopHookRejection ...)) stop-hook-rejections #())
  ;; 要求の前に CLI が会話を自動で圧縮する筋書き(圧縮しない = 空)。
  (setv #^ (get tuple #((| CompactBoundary CompactEnded) ...)) compactions #())
  ;; 道具が走らせる subagent の transcript の読みの筋書き(走らせない = None・道具を走らせる手番だけ)。
  (setv #^ (| SubagentTranscript None) subagent-transcript None)
  ;; 要求の前に CLI が回す hook の行の筋書き(回さない = 空)。
  (setv #^ (get tuple #(HookNotice ...)) hooks #())
  ;; 本体の道具の呼びが走らせた subagent の中の道具の答えの出力(出さない = None・道具を走らせる手番だけ)。
  (setv #^ (| str None) subagent-tool-output None)
  ;; 道具の境界(続けて呼ぶ次の呼びの前)で読まれていない注入を読むか。
  (setv #^ bool boundary-injections False)
  (defn #^ None __post-init__ [self]
    (object.__setattr__ self "tool_input" (frozen-json-object self.tool-input "FakeReply.tool_input"))
    (when (or (< self.thinking-deltas 0) (< self.tool-input-deltas 0))
      (raise (ValueError (.format "FakeReply の thinking-deltas・tool-input-deltas は 0 以上: {} / {}"
                                  self.thinking-deltas self.tool-input-deltas))))
    (when (> (len (lfor end #(self.fail self.lose self.account-refusal self.model-refusal) :if (is-not end None) end)) 1)
      (raise (ValueError "FakeReply の fail・lose・account-refusal・model-refusal は多くとも 1 つ")))
    (when (and (is self.fail None) (is-not self.fail-api-status None))
      (raise (ValueError "FakeReply の fail-api-status は fail の手番だけ(API の誤りで終わる手番の status)")))
    (when (and (is self.lose None) (or (is-not self.lose-exit-code None) (is-not self.lose-stderr None)))
      (raise (ValueError "FakeReply の lose-exit-code・lose-stderr は lose の手番だけ(消える process の終わりの欄)")))
    (when (and (is-not self.subagent-transcript None) (<= self.tool-seconds 0))
      (raise (ValueError "FakeReply の subagent-transcript は道具を走らせる手番だけ(tool-seconds が正 — subagent は道具の途中に走る)")))
    (when (and (is-not self.subagent-transcript None) (!= self.subagent-transcript.parent-tool-use-id FAKE-TOOL-USE-ID))
      (raise (ValueError (.format "FakeReply の subagent-transcript の親は偽の手番の道具の呼び {}: {!r}" FAKE-TOOL-USE-ID
                                  self.subagent-transcript.parent-tool-use-id))))
    (when (and (is-not self.subagent-tool-output None) (<= self.tool-seconds 0))
      (raise (ValueError "FakeReply の subagent-tool-output は道具を走らせる手番だけ(tool-seconds が正 — subagent は道具の途中に走る)")))
    (when (< self.tool-repeats 1)
      (raise (ValueError (+ "FakeReply の tool-repeats は 1 以上(道具を続けて呼ぶ回数): " (str self.tool-repeats)))))
    (when (and (> self.tool-repeats 1) (<= self.tool-seconds 0))
      (raise (ValueError (.format "FakeReply の tool-repeats が 2 以上は道具を走らせる手番だけ(tool-seconds が正): tool-repeats {} / tool-seconds {}"
                                  self.tool-repeats self.tool-seconds))))
    (when (and (> self.tool-repeats 1) (or self.needs-permission (is-not self.subagent-transcript None)))
      (raise (ValueError (+ "FakeReply の tool-repeats が 2 以上の手番は needs-permission・subagent-transcript と組まない"
                            "(許可の問いと subagent は最初の呼びだけの筋書き): tool-repeats " (str self.tool-repeats)))))
    (when (< self.lines 0)
      (raise (ValueError (+ "FakeReply の lines は 0 以上: " (str self.lines)))))
    (when (< self.deltas 0)
      (raise (ValueError (+ "FakeReply の deltas は 0 以上: " (str self.deltas)))))
    (when (> self.deltas (len self.text))
      (raise (ValueError (.format "FakeReply の deltas は本文の字数以下(どの片も空でない): deltas {} / 本文 {} 字"
                                  self.deltas (len self.text)))))
    (when (and (> self.deltas 0) (scripted-end? self))
      (raise (ValueError "FakeReply の deltas は本文で終わる手番だけ(fail・lose・account-refusal・model-refusal の手番は本文を出さない)")))
    (when (is-not self.account-limit None)
      (when (and (is self.account-limit.window None) (not self.account-limit.text))
        (raise (ValueError "FakeReply の account-limit は window か text を名乗る(行に出せない値は数えられない)")))
      (when (and (is self.account-limit.window None) (is-not self.account-limit.resets-at None))
        (raise (ValueError "FakeReply の account-limit の resets-at は window と一緒に(拒まれた限度の行が運ぶ)")))
      (when (is-not self.lose None)
        (raise (ValueError "FakeReply の account-limit は Completed / Failed で終わる手番だけ(lose の終わりに欄が無い)"))))
    (when (and self.stop-hook-rejections (scripted-end? self))
      (raise (ValueError "FakeReply の stop-hook-rejections は本文で終わる手番だけ(fail・lose・account-refusal・model-refusal の手番は本文を出さない)")))
    (when (any (gfor rejection self.stop-hook-rejections (not rejection.answer)))
      (raise (ValueError "FakeReply の stop-hook-rejections の answer は空でない(差し戻される答えの本文の行を出す)")))))


(defclass [(dataclass :frozen True)] FakeInjection []
  "手番に足した入力 1 つ: ref = 入力の行の名 / text = 本文 / fate = 運命(queued → started → completed)。
   運命が進む時は replace で作り直して差し替える。"
  (#^ str ref)
  (#^ str text)
  (setv #^ str fate "queued"))


(defclass FakeTurn []
  "fake の手番 1 つ: phase = quick / tool / permission / text(最後の本文を差分で書いている — 期限 due-at は確定の本文を出す時刻)/ done・
   permission = 答え待ちの許可の問いの id(無ければ None)・tools-run = この手番で呼んだ道具の数(呼びの行を出した数 — 返事の
   tool-repeats に届くまで、結果の直後に同じ道具を続けて呼ぶ。今の呼びは tools-run 番目)・text = 最後の本文(text の相に入る時に決まる)・
   deltas-emitted = 出した差分の片の数・
   end = 手番の終わり(まだなら None)・bells = 出来事の読み(ClaudeReadTurnEvents)の待ち手が掛けた呼び鈴(新しい行か終わりで鳴らして外す)・
   last-call-usage・last-call-model・model-windows = 出した行から数えた本体の最後の呼びと窓(終わりに載せる — 本番の状態機械と同じ規則)。"
  (defn __init__ [self #^ int seq #^ float started-at #^ FakeReply reply #^ (get tuple #(str ...)) refs]
    (setv #^ int self.seq seq)
    (setv #^ float self.started-at started-at)
    (setv #^ FakeReply self.reply reply)
    (setv #^ (get list str) self.refs (list refs))
    (setv #^ str self.phase "quick")
    (setv #^ float self.due-at (+ started-at (max QUICK-TURN-SECONDS reply.think-seconds)))
    (setv #^ int self.lines-emitted 0)
    (setv #^ str self.text "")
    (setv #^ int self.deltas-emitted 0)
    (setv #^ (get list FakeInjection) self.injections [])
    (setv #^ (| str None) self.permission None)
    (setv #^ int self.tools-run 0)
    (setv #^ (get list ClaudeStreamLine) self.lines [])
    (setv #^ (| Completed Failed Interrupted BackendLost None) self.end None)
    (setv #^ (get tuple #((get ExternalPromise None) ...)) self.bells #())
    (setv #^ (| Usage None) self.last-call-usage None)
    (setv #^ (| str None) self.last-call-model None)
    (setv #^ (get tuple #(ModelWindow ...)) self.model-windows #())
    (setv #^ (| AccountLimitHit None) self.limit-hit None)
    (setv #^ (| AccountRefusalHit None) self.refusal-hit None)
    (setv #^ (| ModelRefusalHit None) self.model-refusal-hit None)
    ;; 報せを出して、まだ出していない subagent の transcript の読み(本番の handler と同じく次の result の行の直前に出す)。
    (setv #^ (| SubagentTranscript None) self.subagent-due None)
    ;; 道具の境界で読んだ注入の返事の本文(返事の boundary-injections — 最後の本文に続ける)。
    (setv #^ (get tuple #(str ...)) self.boundary-extra #())
    ;; fake の手番の本体の API の呼び(返事 1 つ = 呼び 1 つ): call-id = 本体の assistant の行が名乗る呼びの id / call-usage = その呼びの
    ;; 行が名乗った usage で、まだ閉じていない物(閉じた・名乗っていなければ None)— 本番の handler と同じく、呼びの最終の usage
    ;; (CallClosed)を手番の終わりの行の前に出すため(card ki-290ec664737d)。
    (setv #^ str self.call-id (.format "msg_fake_{}" seq))
    (setv #^ (| Usage None) self.call-usage None)
    ;; 手番の行の写し手(ClaudeStartTurn.tap — 本番の handler の読み手の thread と同じく、行を出した所で呼ぶ・card ki-31b41eac5071)。
    (setv #^ (| (get Callable #([ClaudeStreamLine] None)) None) self.tap None)))


(defclass FakeSession []
  "launches = この会話で起こした process の数(本番の handler と同じ規則 — 同じ起こした時の条件の鍵の続きは生きた process を使い回し、
   生き残った入力の手番も同じ process)・alive = process が生きている(手番をまたいで生きて待つ — #3672)・launch-key = 今の process の
   起動条件のキー・stopped-because = 最後の process を停止した理由・warmed-fresh = 新しい会話として入力なしで事前起動し
   (ClaudeWarmSession)、まだターンを始めていない(最初のターンは同じ id の FreshSession — 本番の handler と同じ)・credential = 今の
   process の資格の家の資格(本番の .credentials.json の中身にあたる — 家の資格で起こした process は None。起こした時・使い回した
   手番の宣言の資格・ClaudeReplaceSessionCredential で替わる)。"
  (defn __init__ [self #^ str session-id home #^ str cwd]
    (setv self.session-id session-id self.home home self.cwd cwd
          self.current-seq 0 self.next-line-seq 0 self.closed False)
    (setv #^ int self.launches 0)
    (setv #^ bool self.alive False)
    (setv #^ (| str None) self.launch-key None)
    (setv #^ (| StopReason None) self.stopped-because None)
    (setv #^ bool self.warmed-fresh False)
    (setv #^ (| LentCredential None) self.credential None)
    (setv #^ (get dict #(int FakeTurn)) self.turns {}))

  (defn running [self]
    (setv turn (.get self.turns self.current-seq))
    (if (and (is-not turn None) (is turn.end None)) turn None)))


(defclass FakeClaudeWorld []
  "fake の世界: 家ごとの transcript(入力の列)と会話の状態。返事の作り方はちょうど 1 つ:
   responder = (本文 記憶) → FakeReply の同期の関数(効果を出さない筋書き)・
   respond = (本文 記憶) → FakeReply の Program の kleisli(defk — 返事を作る時に効果を出してよい。効果は fake の handler の外側が
   答える。上の層の相手役が、いま始めている手番を自分の handler の状態から効果で読むため)。
   live-limit = 同時に生かす process の本数の上限(本番の ClaudeCodeHost の live-limit と同じ意味 — 上の層の模擬は本番と同じ値を
   渡す。None = 上限を宣言しない世界)。本番の handler と同じに、上限を越える起動でも process を止めず、知らせ
   ClaudeLiveLimitExceeded をホストへ出す(#4072 の E1b。fake は log の行は出さない — slog の答え手を要しない)。"
  (defn __init__ [self #^ (| (get Callable #(... object)) None) [responder None]
                  * #^ (| (get Callable #(... object)) None) [respond None] #^ (| int None) [live-limit None]]
    (when (= (is responder None) (is respond None))
      (raise (ValueError "FakeClaudeWorld は responder(同期)と respond(kleisli)のちょうど 1 つを受ける")))
    ;; 数の型は注記が持つ。bool は int の子なので名指しで断り、値の範囲を断る(本番の ClaudeCodeHost と同じ)。
    (when (and (is-not live-limit None) (or (isinstance live-limit bool) (< live-limit 1)))
      (raise (ValueError (.format "FakeClaudeWorld.live_limit は 1 以上の整数か None: {!r}" live-limit))))
    (setv #^ (| int None) self.live-limit live-limit)
    (setv self.responder responder
          self.respond respond
          self.transcripts {}
          self.activity {})
    (setv #^ (get dict #(str FakeSession)) self.sessions {}))

  (defn #^ "FakeClaudeWorld" restarted [self]
    "同じ家の上で process を作り直した世界: transcript と activity(家の中身)は同じ物を共有し、会話(process の中の状態)は空。
     前の世界の走っている手番は前の世界で走り続ける(子 process は上の層の process の作り直しで止まらない)。"
    (setv world (FakeClaudeWorld self.responder :respond self.respond :live-limit self.live-limit))
    (setv world.transcripts self.transcripts world.activity self.activity)
    world)

  (defn transcript-key [self home #^ str cwd #^ str session-id]
    #(home.config-dir cwd session-id)))


(defk reply-of [#^ FakeClaudeWorld world #^ str text #^ tuple memory]
  {:pre [(: world FakeClaudeWorld) (: text str) (: memory tuple)] :post [(: % FakeReply)]}
  "筋書きの返事を 1 つ作る(返事を作る所はここ 1 つ): respond が在れば、その Program を走らせた答え・無ければ同期の responder の答え。"
  (if (is world.respond None)
      (world.responder text memory)
      (do (<- reply FakeReply (world.respond text memory))
          reply)))


;; --- 行を出す ------------------------------------------------------------------------------------

;; 読みの待ち手の呼び鈴: 本番の CLI の行の流れは、行が出た時と process が終わった時(消えた・止めた・閉じた)にその場で読み手へ届く。
;; fake の読みも、期限まで眠らずに、行を出した時と手番を終えた時に鳴らす呼び鈴で起きる(待ちの外から手番を終える故障の注入・止める・
;; 閉じるでも、読み手は次の筋書きの刻まで眠り続けない — #3130)。

(defk ring-turn [#^ FakeTurn turn]
  {:pre [(: turn FakeTurn)] :post [(: % (type None))]}
  "手番 turn に掛かった読みの呼び鈴を全部鳴らして外す(新しい行か終わりが出た)。"
  (setv bells turn.bells)
  (setv turn.bells #())
  (for [bell bells]
    (.complete bell None))
  None)

(defk said-by-reply [#^ FakeReply reply kind #^ str call-id]
  {:pre [(: reply FakeReply) (: kind ClaudeLineKind) (: call-id str)] :post [(: % ClaudeLineKind)]
   :tags {:context "claude-code" :role "foundation"}}
  "fake の CLI がこの手番の行で名乗る呼びの値を行に載せるため(本物の CLI が全部の assistant の行で usage と model を、result の行で
   modelUsage を名乗るのと同じ — #3744): assistant の行には返事の last-call-usage と last-call-model、result の行には返事の
   model-windows。CLI が答えの代わりに自分で作る assistant の行(model を自分で名乗る行 — 口座の断りの行の SYNTHETIC-MODEL)と
   ほかの行はそのまま。usage を名乗る本体の会話の assistant の行には手番の呼びの id(call-id — 本番の CLI の message.id と同じ役)も載せる(card
   ki-290ec664737d)。"
  (cond
    (and (isinstance kind AssistantMessage) (is kind.model None))
      (do
        (val names-call (and (is kind.parent-tool-use-id None) (is-not reply.last-call-usage None)))
        (replace kind :usage reply.last-call-usage :model reply.last-call-model
                      :message-id (if names-call call-id kind.message-id)
                      :request-id (if names-call (+ FAKE-REQUEST-PREFIX call-id) kind.request-id)))
    (isinstance kind TurnResult) (replace kind :model-windows reply.model-windows)
    True kind))

(defk emit [#^ FakeSession session #^ FakeTurn turn kind]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kind ClaudeLineKind)] :post [(: % (type None))]}
  "手番の行を 1 つ出すため。出した行から、本番の状態機械(dialogue.hy の on-assistant・on-result)と同じ規則で本体の最後の呼びと
   model ごとの窓を手番に覚える(終わりに載せるのは finish)。"
  ;; 報せを受けた subagent の transcript の読みは、本番の handler と同じく CLI の手番の終わり(result の行)の直前に足す。
  (when (and (isinstance kind TurnResult) (is-not turn.subagent-due None))
    (val due turn.subagent-due)
    (setv turn.subagent-due None)
    (<- (emit session turn due)))
  ;; 本体の呼びの最終の usage は、本番の CLI の message_delta と同じく手番の終わりの行より前に出す。止めた手番の終わり(terminal-reason
  ;; aborted_*)は、本番の CLI が終わりを受けずに止めるので出さない(card ki-290ec664737d)。
  (when (and (isinstance kind TurnResult) (is-not turn.call-usage None) (not (.startswith kind.terminal-reason "aborted")))
    (val closed (CallClosed :message-id turn.call-id :model turn.last-call-model :parent-tool-use-id None :usage turn.call-usage
                            :stopped True))
    (setv turn.call-usage None)
    (<- (emit session turn closed)))
  (<- at (GetTime))
  (<- said (said-by-reply turn.reply kind turn.call-id))
  (val line (ClaudeStreamLine :seq session.next-line-seq :at at :kind said :raw (repr said)))
  (.append turn.lines line)
  (+= session.next-line-seq 1)
  ;; 出した行を手番の写し手へ(本番の handler の tap-lines と同じく、写し手の例外は捨てる)。
  (when (is-not turn.tap None)
    (tap-lines turn.tap [line]))
  (cond
    (and (isinstance said AssistantMessage) (is said.parent-tool-use-id None))
      (setv turn.last-call-usage said.usage turn.last-call-model said.model
            turn.call-usage (if (= said.message-id turn.call-id) said.usage turn.call-usage))
    (isinstance said TurnResult)
      (setv turn.model-windows (! (merged-windows turn.model-windows said.model-windows))))
  (setv turn.limit-hit (limit-hit-after turn.limit-hit said)
        turn.refusal-hit (refusal-hit-after turn.refusal-hit said)
        turn.model-refusal-hit (model-refusal-after turn.model-refusal-hit said))
  (<- (ring-turn turn))
  None)

(defk emit-all [#^ FakeSession session #^ FakeTurn turn #^ list kinds]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kinds list)] :post [(: % (type None))]}
  (for [kind kinds] (<- (emit session turn kind)))
  None)

(defk finish [#^ FakeSession session #^ FakeTurn turn end]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: end ClaudeTurnEnd)] :post [(: % (type None))]}
  "手番を end で閉じるため。process は手番の終わりで降ろさない(次の手番まで生きて待つ)— 消えた process の終わり(BackendLost)だけ
   process が無くなる(訳は付けない — 降ろしたのでなく自分で消えた)。終わりには手番に覚えた本体の最後の呼びと窓を載せる(本番の
   状態機械の ended と同じ — どの終わり方でも同じ 3 欄)。CLI が終えた手番(Completed・Failed)には、出した行から数えた口座の限度と
   口座の側の断りの事実も載せる(本番の状態機械の with-last-call と同じ)。"
  (val called (replace end :last-call-usage turn.last-call-usage :last-call-model turn.last-call-model
                           :model-windows turn.model-windows))
  (setv turn.end (match called
                   (| (Completed) (Failed)) (replace called :account-limit turn.limit-hit :account-refusal turn.refusal-hit
                                                     :model-refusal turn.model-refusal-hit)
                   _ called)
        turn.phase "done")
  (when (isinstance end BackendLost) (setv session.alive False))
  (<- (ring-turn turn))
  None)

(defk read-injections [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % tuple)]
   :tags {:context "claude-code" :role "foundation"}}
  "読まれていない注入(queued)を読む — 本物の CLI が道具の境界と本文の後に足された入力を読むのと同じに、注入の started の行を出し、
   それぞれの筋書きの返事の本文を足された順に返す(最後の本文へ続けるため)。"
  (val memory (tuple (.get world.transcripts (.transcript-key world session.home session.cwd session.session-id) [])))
  (var extra #())
  (for [#(index injection) (enumerate turn.injections)]
    (when (= injection.fate "queued")
      (setv (get turn.injections index) (replace injection :fate "started"))
      (<- (emit session turn (InputFate injection.ref "started")))
      (<- extra-reply FakeReply (reply-of world injection.text memory))
      (:= extra (+ extra #(extra-reply.text)))))
  extra)

(defk stop-hook-notice-of [rejection]
  {:pre [(: rejection StopHookRejection)] :post [(: % HookNotice)] :tags {:context "claude-code" :role "foundation"}}
  "Stop hook が答えを差し戻した時に、本物の CLI(--include-hook-events)が差し戻しの行の前に出す Stop の hook の応答の行を、偽の CLI でも
   出すため(ki-d8b473480303)。形は exit code 2 で差し戻す hook の応答 — 理由は stderr・終わり方 error(本物の
   差し戻しは同じ時に system/notification の stop-hook-error「Stop hook error occurred」を出す — 替え玉の CLI と同じ。応答の行そのものは
   計っていない — 欄は CLI の書き手 hook_response の形)。"
  (HookNotice :event "Stop" :phase HookPhase.RESPONSE :name "Stop" :hook-id (.format "fake-stop-hook-{}" rejection.answer)
              :stderr rejection.reason :output rejection.reason :exit-code 2 :outcome "error"))

(defk begin-text [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "道具の境界(か手番の終わり)で最後の本文を決め、本文を書く相(text)に入る — 本物の CLI が最後の本文を差分で流してから確定するのを
   模すため。本文 = 返事の本文 + 読まれていない注入の返事。確定の本文は deltas 片の差分を DELTA-SECONDS ごとに出し終えた刻
   (deltas = 0 なら今)に出す。Stop hook が差し戻す筋書き(返事の stop-hook-rejections)は、本物と同じく差し戻される答えの本文の行と
   差し戻しの行を 1 つずつ、最後の本文より前(最後の道具の結果より後)に出す(#4020)。"
  (for [rejection turn.reply.stop-hook-rejections]
    (<- notice HookNotice (stop-hook-notice-of rejection))
    (<- (emit-all session turn [(AssistantMessage :text rejection.answer) notice (StopHookFeedback :reason rejection.reason)])))
  (<- extra (read-injections world session turn))
  (setv turn.text (.join " " (+ #(turn.reply.text) turn.boundary-extra extra))
        turn.phase "text"
        turn.due-at (+ now (* DELTA-SECONDS turn.reply.deltas)))
  None)

(defk next-delta-at [#^ FakeTurn turn]
  {:pre [(: turn FakeTurn)] :post [(: % (| float None))] :tags {:context "claude-code" :role "foundation"}}
  "まだ出していない次の差分の時刻(本文を書く相の外か、出し終えていれば None)— 差分を出す刻と、読みの待ち手が起きる刻を 1 か所で決めるため。
   差分 k(0 から)は確定の本文の時刻 due-at の (deltas − k) × DELTA-SECONDS 前(最初の片は相に入った刻)。"
  (if (and (= turn.phase "text") (< turn.deltas-emitted turn.reply.deltas))
      (- turn.due-at (* DELTA-SECONDS (- turn.reply.deltas turn.deltas-emitted)))
      None))

(defk emit-due-deltas [#^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "now までに来た本文の差分を PartialMessage の text_delta で出す — 上の層が確定の本文より先に書きかけの本文を読めるように。本文を
   deltas 片に字数でほぼ等分する(片 k = 本文の [k × 字数 / deltas, (k + 1) × 字数 / deltas) — 片の連結は本文と同じ・どの片も空でない)。"
  (val pieces turn.reply.deltas)
  (val size (len turn.text))
  (var due (! (next-delta-at turn)))
  (while (and (is-not due None) (>= (+ now CLOCK-TICK) due))
    (<- (emit session turn (PartialMessage :text-delta (cut turn.text
                                                            (// (* turn.deltas-emitted size) pieces)
                                                            (// (* (+ turn.deltas-emitted 1) size) pieces))
                                           :delta DeltaKind.TEXT)))
    (setv turn.deltas-emitted (+ turn.deltas-emitted 1))
    (<- following (next-delta-at turn))
    (:= due following))
  None)

(defk emit-limit [#^ FakeSession session #^ FakeTurn turn]
  {:pre [(: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))] :tags {:context "claude-code" :role "foundation"}}
  "口座の限度に当たる筋書きの手番で、本物の CLI と同じ限度の行を終わりの前に出すため(#3983 — 返事の account-limit): 窓が在れば拒まれた
   rate_limit_event の行、文が在れば本体の assistant の行の error rate_limit。終わりの account-limit は emit が出した行から数える。"
  (val limit turn.reply.account-limit)
  (when (is-not limit None)
    (when (is-not limit.window None)
      (<- (emit session turn (RateLimit limit.window :resets-at limit.resets-at :status RATE-LIMIT-REJECTED))))
    (when limit.text
      (<- (emit session turn (AssistantMessage :text limit.text :error ASSISTANT-ERROR-RATE-LIMIT)))))
  None)

(defk complete-turn [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "本文を書き終えた刻に、確定の本文の返事で手番を終える。本文を書く間に足された注入もここで読み、その返事を本文の後に続ける。"
  (<- late (read-injections world session turn))
  (val text (.join " " (+ #(turn.text) late)))
  (<- (emit-limit session turn))
  (<- (emit-all session turn [(AssistantMessage :text text)
                              (TurnResult "success" False :terminal-reason "completed" :usage turn.reply.usage)]))
  (for [injection turn.injections]
    (<- (emit session turn (InputFate injection.ref "completed"))))
  (<- (finish session turn (Completed :result-text text :usage turn.reply.usage :cost-usd turn.reply.cost-usd
                                      :model-usage turn.reply.model-usage
                                      :model-usage-gap (! (model-cost-gap turn.reply.model-usage turn.reply.cost-usd))
                                      :input-refs (tuple turn.refs))))
  None)

(defn #^ float line-due-at [#^ FakeTurn turn #^ int index]
  "本文の行 index(0 から)を出す時刻: 始めてから期限までの前半に等間隔。"
  (+ turn.started-at (* (/ (- turn.due-at turn.started-at) 2) (/ index (max turn.reply.lines 1)))))

(defn #^ (| float None) next-line-at [#^ FakeTurn turn]
  "まだ出していない本文の行の次の時刻(無ければ None)。"
  (if (and (in turn.phase #("quick" "tool")) (< turn.lines-emitted turn.reply.lines))
      (line-due-at turn turn.lines-emitted)
      None))

(defk emit-due-lines [#^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]}
  "now までに来た本文の行を出す。"
  (while (and (< turn.lines-emitted turn.reply.lines) (>= (+ now CLOCK-TICK) (line-due-at turn turn.lines-emitted)))
    (<- (emit session turn (AssistantMessage :text (+ "line " (str turn.lines-emitted)))))
    (+= turn.lines-emitted 1))
  None)

(defk refuse-turn [#^ FakeSession session #^ FakeTurn turn #^ AccountRefusalHit refusal]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: refusal AccountRefusalHit)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "口座の側が要求を断る筋書きの手番(返事の account-refusal)を、本物の CLI と同じ断りの行を出して Failed で終えるため: 本体の
   assistant の行(model SYNTHETIC-MODEL・error の語・断りの文)→ result の行(subtype REFUSAL-RESULT-SUBTYPE・is_error true・本文 =
   断りの文)。終わりの account-refusal は emit が出した行から数え(finish が載せる)、失敗の文は本物の状態機械と同じく result の本文
   (空なら subtype)。"
  (val reply turn.reply)
  (<- (emit-limit session turn))
  (<- (emit-all session turn [(AssistantMessage :text refusal.text :error refusal.error :model SYNTHETIC-MODEL :usage SYNTHETIC-USAGE)
                              (TurnResult REFUSAL-RESULT-SUBTYPE True :result-text refusal.text :usage reply.usage)]))
  (<- (finish session turn (Failed (or (.strip refusal.text) REFUSAL-RESULT-SUBTYPE) :usage reply.usage :cost-usd reply.cost-usd
                                   :model-usage reply.model-usage :model-usage-gap (! (model-cost-gap reply.model-usage reply.cost-usd))
                                   :input-refs (tuple turn.refs))))
  None)

(defk model-refuse-turn [#^ FakeSession session #^ FakeTurn turn #^ ModelRefusalHit refusal]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: refusal ModelRefusalHit)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "model の安全装置が要求を断る筋書きの手番(返事の model-refusal)を、本物の CLI と同じ断りの行を出して Failed で終えるため: 本体の
   assistant の行(model = 断りの model・error MODEL-REFUSAL-ERROR・stop_reason STOP-REASON-REFUSAL・断りの種類・断りの文)→ result の行
   (subtype REFUSAL-RESULT-SUBTYPE・is_error true・本文 = 断りの文)。終わりの model-refusal は emit が出した行から数える(finish が載せる)。"
  (val reply turn.reply)
  (<- (emit-limit session turn))
  (<- (emit-all session turn [(AssistantMessage :text refusal.text :error MODEL-REFUSAL-ERROR :model refusal.model :usage SYNTHETIC-USAGE
                                                :stop-reason STOP-REASON-REFUSAL :refusal-category refusal.category)
                              (TurnResult REFUSAL-RESULT-SUBTYPE True :result-text refusal.text :usage reply.usage)]))
  (<- (finish session turn (Failed (or (.strip refusal.text) REFUSAL-RESULT-SUBTYPE) :usage reply.usage :cost-usd reply.cost-usd
                                   :model-usage reply.model-usage :model-usage-gap (! (model-cost-gap reply.model-usage reply.cost-usd))
                                   :input-refs (tuple turn.refs))))
  None)

(defk api-error-turn [#^ FakeSession session #^ FakeTurn turn #^ str text #^ int status]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: text str) (: status int)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "API の誤りで終わる筋書きの手番(返事の fail と fail-api-status)を、本物の CLI と同じ行を出して Failed で終えるため: 本体の assistant の
   行(model SYNTHETIC-MODEL・error API-ERROR-SERVER・誤りの文)→ result の行(subtype REFUSAL-RESULT-SUBTYPE・is_error true・本文 = 誤りの
   文・api_error_status = status)。本物の CLI は 500 以上の status の誤りを「API Error: <status> <訳>. This is a server-side issue, …」の
   文と error server_error の行にする(実測 CLI 2.1.292 の code — 本番 2026-10-24 09:26 JST の 529 Overloaded)。失敗の文は result の本文。"
  (val reply turn.reply)
  (<- (emit-limit session turn))
  (<- (emit-all session turn [(AssistantMessage :text text :error API-ERROR-SERVER :model SYNTHETIC-MODEL :usage SYNTHETIC-USAGE)
                              (TurnResult REFUSAL-RESULT-SUBTYPE True :result-text text :api-error-status status :usage reply.usage)]))
  (<- (finish session turn (Failed (or (.strip text) REFUSAL-RESULT-SUBTYPE) :api-error-status status :usage reply.usage
                                   :cost-usd reply.cost-usd :model-usage reply.model-usage
                                   :model-usage-gap (! (model-cost-gap reply.model-usage reply.cost-usd))
                                   :input-refs (tuple turn.refs))))
  None)

(defk end-scripted [#^ FakeSession session #^ FakeTurn turn]
  {:pre [(: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "筋書きが失敗・口座の側の断り・process の消失で終わる手番の終わり(注入は読まない)。process の消失の終わりには、台本が名乗った
   終了 code と stderr の末尾(本物の状態機械と同じ上限 — stderr-tail-within)を欄で載せる(#4207)。"
  (setv reply turn.reply)
  (cond
    (is-not reply.account-refusal None)
      (<- (refuse-turn session turn reply.account-refusal))
    (is-not reply.model-refusal None)
      (<- (model-refuse-turn session turn reply.model-refusal))
    (and (is-not reply.fail None) (is-not reply.fail-api-status None))
      (<- (api-error-turn session turn reply.fail reply.fail-api-status))
    (is-not reply.fail None)
      (do
        (<- (emit-limit session turn))
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "failed" :usage reply.usage)))
        (<- (finish session turn (Failed reply.fail :terminal-reason "failed" :usage reply.usage :cost-usd reply.cost-usd
                                         :model-usage reply.model-usage
                                         :model-usage-gap (! (model-cost-gap reply.model-usage reply.cost-usd))
                                         :input-refs (tuple turn.refs)))))
    True
      (<- (finish session turn (BackendLost reply.lose :exit-code reply.lose-exit-code
                                            :stderr-tail (if (is reply.lose-stderr None)
                                                             None
                                                             (! (stderr-tail-within reply.lose-stderr)))))))
  None)

(defk repeated-id [#^ str base #^ int call]
  {:pre [(: base str) (: call int) (>= call 1)] :post [(: % str)] :tags {:context "claude-code" :role "foundation"}}
  "偽の手番の道具の呼び call 番目(1 から)の id(tool_use の id と道具の task の id)を決める 1 か所 — 同じ道具を続けて呼ぶ手番
   (返事の tool-repeats)の呼びと結果の対を見分けるため。1 番目は base のまま(呼びが 1 回の手番の行は今までと同じ)、2 番目からは
   base-<call>。"
  (if (= call 1) base (.format "{}-{}" base call)))

(defk tool-call-lines-of [#^ FakeReply reply #^ int call]
  {:pre [(: reply FakeReply) (: call int)] :post [(: % list)] :tags {:context "claude-code" :role "foundation"}}
  "偽の手番の道具の呼び call 番目(1 から)の行を、本物の CLI と同じ順で出すため: 命令の差分(tool-input-deltas)を出す返事は、道具の
   block の始まり(content_block_start の tool_use — 呼びの id と道具の名・#3974 の 3)→ 命令を書いている間の差分(input_json_delta の行)、
   最後に呼びの行。命令は返事の tool-input(許可の問いも同じ命令を問う — 本物の CLI の can_use_tool の input は tool_use の input)。"
  (<- use-id (repeated-id FAKE-TOOL-USE-ID call))
  (+ (if (> reply.tool-input-deltas 0)
         [(PartialMessage :tool-start (ToolCall use-id FAKE-TOOL-NAME))]
         [])
     (lfor _ (range reply.tool-input-deltas)
           (PartialMessage :delta DeltaKind.TOOL-INPUT :tool-input-delta FAKE-TOOL-INPUT-PIECE))
     [(AssistantMessage :tool-calls #((ToolCall use-id FAKE-TOOL-NAME reply.tool-input)))]))

(defk task-lines-of [#^ FakeReply reply #^ int call #^ bool started]
  {:pre [(: reply FakeReply) (: call int) (: started bool)] :post [(: % list)] :tags {:context "claude-code" :role "foundation"}}
  "道具を走らせる偽の手番の、道具の呼び call 番目(1 から)の task の始まり(started)か終わりの行を、本番と同じ形で出すため。subagent を
   走らせる手番(subagent-transcript が在る — 呼びは 1 回だけ)は本物の CLI 2.1.292 と同じく subagent の task(id = transcript の
   agent-id・親 = 道具の呼び・始まりは SUBAGENT-TASK-TYPE)の行(本番の handler が足す transcript の読みの行は、emit が次の result の行の
   直前に出す — FakeTurn の subagent-due)。走らせない手番は FAKE-TASK-ID(2 番目からの呼びは repeated-id の付け方)の行だけ。"
  (val transcript reply.subagent-transcript)
  (cond
    (is transcript None)
      [(TaskEvent (! (repeated-id FAKE-TASK-ID call)) (if started "started" "completed"))]
    started
      [(TaskEvent transcript.agent-id "started" :tool-use-id FAKE-TOOL-USE-ID :task-type SUBAGENT-TASK-TYPE)]
    True
      [(TaskEvent transcript.agent-id "completed" :tool-use-id FAKE-TOOL-USE-ID)]))

(defk call-tool [#^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "道具の呼びを 1 つ始めて道具を走らせる相(tool)に入るため: 呼びの数(tools-run)を 1 つ進め、期限を now から返事の tool-seconds 後に
   置き、その呼びの行と道具の task の始まりの行を出す(結果の行は期限に advance が出す)。手番の最初の呼びも、返事の tool-repeats で
   続けて呼ぶ 2 番目からの呼びも同じ形。"
  (val call (+ turn.tools-run 1))
  (setv turn.tools-run call turn.phase "tool" turn.due-at (+ now turn.reply.tool-seconds))
  (<- called (tool-call-lines-of turn.reply call))
  (<- started (task-lines-of turn.reply call True))
  (<- (emit-all session turn (+ called started)))
  None)

(defk advance [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "今の時刻まで筋書きを進める。"
  (<- now (GetMonotonic))
  (when (in turn.phase #("quick" "tool"))
    (<- (emit-due-lines session turn now)))
  (when (and (in turn.phase #("quick" "tool")) (>= (+ now CLOCK-TICK) turn.due-at))
    (when (= turn.phase "tool")
      ;; 本物の CLI 2.1.292 の順: task の報せ → 道具の結果(今の呼び — tools-run 番目 — の id を名指す)。subagent の transcript の
      ;; 読みは、本番の handler と同じく次の result の行の直前に出す(emit)。
      (<- ended (task-lines-of turn.reply turn.tools-run False))
      (<- answered (repeated-id FAKE-TOOL-USE-ID turn.tools-run))
      (setv turn.subagent-due turn.reply.subagent-transcript)
      ;; subagent の中の道具の答え(返事の subagent-tool-output)は、本体の呼びの結果の前に、親 = 本体の呼びの行で出す。
      (val inner (if (is turn.reply.subagent-tool-output None)
                     []
                     [(ToolResult :answers #((ToolAnswer :id (.format "{}-sub" answered) :text turn.reply.subagent-tool-output
                                                         :is-error False))
                                  :parent-tool-use-id answered)]))
      (<- (emit-all session turn (+ ended inner [(ToolResult :answers #((ToolAnswer :id answered :text turn.reply.tool-output
                                                                                     :is-error turn.reply.tool-error)))]))))
    (cond
      ;; 返事の tool-repeats に届くまで、結果の直後に同じ道具を続けて呼ぶ(相は tool のまま・期限は今から tool-seconds 後)。返事の
      ;; boundary-injections なら、次の呼びの前に読まれていない注入を読む(本物の CLI の道具の境界)。
      (and (= turn.phase "tool") (< turn.tools-run turn.reply.tool-repeats))
        (do (when turn.reply.boundary-injections
              (<- extra (read-injections world session turn))
              (setv turn.boundary-extra (+ turn.boundary-extra extra)))
            (<- (call-tool session turn now)))
      (scripted-end? turn.reply)
        (<- (end-scripted session turn))
      True
        (<- (begin-text world session turn now))))
  (when (= turn.phase "text")
    (<- (emit-due-deltas session turn now))
    (when (>= (+ now CLOCK-TICK) turn.due-at)
      (<- (complete-turn world session turn))))
  None)

(defk begin-fake-turn [#^ FakeClaudeWorld world #^ FakeSession session reply #^ tuple refs #^ bool announce #^ bool launched tap]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: reply FakeReply) (: refs tuple) (: announce bool) (: launched bool)
         (: tap (| Callable None))]
   :post [(: % FakeTurn)]}
  "手番を開いて最初の行を出す。announce = 入力の行の運命と init を出す(頼まれた手番 — 使い回した process も入力ごとに init を出す。
   生き残った入力の手番は started から)/ launched = この手番のために process を起こした(使い回し・生き残った入力の手番は起こさない)/
   tap = 手番の行の写し手(ClaudeStartTurn.tap — 生き残った入力の手番は止めた手番の写し手を続ける)。"
  (<- now (GetMonotonic))
  (+= session.current-seq 1)
  (when launched (+= session.launches 1))
  (setv session.alive True)
  (setv turn (FakeTurn session.current-seq now reply refs))
  (setv turn.tap tap)
  (setv (get session.turns turn.seq) turn)
  (for [ref refs]
    (when announce (<- (emit session turn (InputFate ref "queued"))))
    (<- (emit session turn (InputFate ref "started"))))
  (<- (emit session turn (Init :session-id session.session-id
                               :capabilities (if reply.interrupt-receipt FAKE-CAPABILITIES NO-RECEIPT-CAPABILITIES)
                               :model "fake")))
  ;; 要求の前に CLI が回す hook の行(本物の CLI の --include-hook-events の system/hook_response ほか・ki-d8b473480303)。
  (<- (emit-all session turn (list reply.hooks)))
  ;; 要求の前の会話の自動の圧縮(本物の CLI 2.1.292 の行 — 始まりの system/status compacting(card ki-aeae6f9e643d)→ 終わりの
  ;; system/status null(compact_result — card ki-8239be27d5f6)→ 成功なら system/compact_boundary(#4189)— 圧縮 1 回に 1 組)。
  ;; 筋書きの CompactBoundary は成功した圧縮、CompactEnded はその終わり方で終わった圧縮(境目の行は出ない)。
  (<- (emit-all session turn (lfor compaction reply.compactions
                                   line (if (isinstance compaction CompactBoundary)
                                            #((CompactStarted) (CompactEnded :outcome CompactOutcome.SUCCEEDED) compaction)
                                            #((CompactStarted) compaction))
                                   line)))
  ;; 答えの前に考えている間の差分(本物の CLI の thinking_delta の行 — 片ごとの考えの文字列は FAKE-THINKING-PIECE・替え玉の CLI と同じ)。
  ;; 差分の前に、考えの block の始まりの行を 1 つ(本物の CLI の content_block_start の thinking — #4186)。考えの無い返事では出さない。
  (<- (emit-all session turn (+ (if (> reply.thinking-deltas 0) [(PartialMessage :thinking-start True)] [])
                                (lfor _ (range reply.thinking-deltas)
                                      (PartialMessage :delta DeltaKind.THINKING :thinking-delta FAKE-THINKING-PIECE)))))
  ;; 道具の呼び(tool-call-lines-of — 最初の呼び): 許可の問いを出す手番は呼びの行の後に同じ命令の許可の問いを出して答えを待ち、
  ;; ほかの道具の手番はそのまま道具を走らせる(call-tool — 続けて呼ぶ 2 番目からの呼びは advance が始める)。
  (cond
    reply.needs-permission
      (do
        (setv request-id (str (uuid.uuid4)))
        (setv turn.phase "permission" turn.permission request-id turn.tools-run 1)
        (<- call-lines (tool-call-lines-of reply 1))
        (<- (emit-all session turn (+ call-lines [(PermissionRequested request-id FAKE-TOOL-NAME reply.tool-input)]))))
    (> reply.tool-seconds 0)
      (<- (call-tool session turn now)))
  turn)


;; --- 節の中身 -----------------------------------------------------------------------------------

(defk note-over-limit [#^ FakeClaudeWorld world #^ FakeSession session #^ str session-id #^ bool warm]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: session-id str) (: warm bool)] :post [(: % None)]
   :tags {:context "claude-code" :role "foundation"}}
  "会話 session の process を起こす直前に、起こした後の生きた process の本数が世界の上限(world.live-limit)を越えるなら、知らせ
   ClaudeLiveLimitExceeded をホストへ出すため(本番の handler の note-over-limit と同じ数え — 止めない・待たない・失敗にしない)。
   起こす会話の前の process は数えない(使い回さない時は起こす前に降ろしている)。session-id = 知らせに載せる会話の id(本番と同じに、
   枝分かれは元の会話の id)・warm = 入力の前の事前起動か。"
  (when (is world.live-limit None)
    (return None))
  (val live (+ 1 (len (lfor other (.values world.sessions) :if (and other.alive (is-not other session)) other))))
  (when (<= live world.live-limit)
    (return None))
  (<- (ClaudeLiveLimitExceeded :session-id session-id :live live :limit world.live-limit :warm warm))
  None)

(defn transcript-of [#^ FakeClaudeWorld world home #^ str cwd #^ str session-id]
  (.get world.transcripts (.transcript-key world home cwd session-id)))

(defn carry-into [#^ FakeClaudeWorld world home #^ str cwd #^ str session-id carry]
  "持ち込み: 在れば上書きしない。Rebuilt は本文の行を入力として読む・LinkFromHome は元の家の transcript を写す。"
  (setv key (.transcript-key world home cwd session-id))
  (when (or (is carry None) (in key world.transcripts)) (return None))
  (cond
    (isinstance carry Rebuilt)
      (setv (get world.transcripts key) (list (run (jsonl-lines carry.jsonl-text))))
    (isinstance carry LinkFromHome)
      (do
        (setv source (transcript-of world carry.source-home cwd session-id))
        (when (is-not source None) (setv (get world.transcripts key) source)))))

(defk fake-start-turn [#^ FakeClaudeWorld world #^ ClaudeStartTurn request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeStartTurn)] :post [(: % "StartTurnOutcome")]}
  (setv origin request.origin spec request.spec input request.input)
  (setv refused (lfor item input.attachments :if (not-in item.mime IMAGE-MIMES) item.mime))
  (when refused (return (AttachmentRefused (get refused 0))))
  (setv target (if (isinstance origin ForkSession) origin.parent-session-id origin.session-id))
  (when (not (isinstance origin FreshSession)) (carry-into world spec.home spec.cwd target origin.carry))
  (setv existing (if (isinstance origin ForkSession) None (.get world.sessions target)))
  (setv present (is-not (transcript-of world spec.home spec.cwd target) None))
  (cond
    ;; 入力なしで事前起動しただけの新しい会話(warmed-fresh)は、同じ id の FreshSession の最初のターンを拒否しない。
    (and (isinstance origin FreshSession) (or (and existing (not existing.warmed-fresh)) present)) (return (SessionIdInUse target))
    (and (isinstance origin ResumeSession) existing (.running existing)) (return (TurnInFlight (ClaudeTurn target existing.current-seq)))
    (and (not (isinstance origin FreshSession)) (not present)) (return (SessionNotFound target)))
  (setv session-id (if (isinstance origin ForkSession) (str (uuid.uuid4)) target))
  (setv key (.transcript-key world spec.home spec.cwd session-id))
  (when (isinstance origin ForkSession)
    (setv parent (transcript-of world spec.home spec.cwd target))
    (when (is parent None)
      (raise (RuntimeError (.format "枝分かれの元の会話 {} の transcript が無い(在ることは上で確かめた)" target))))
    (setv (get world.transcripts key) (list parent)))
  (when (isinstance origin FreshSession) (setv (get world.transcripts key) []))
  (setv session (or existing (FakeSession session-id spec.home spec.cwd)))
  (setv session.closed False)
  (setv (get world.sessions session-id) session)
  ;; 本番の handler と同じ規則(decision.start-decision): 続き(ResumeSession)と、入力なしで事前起動しただけの新しい会話の最初の
  ;; ターンで、生きて待つ process の起動条件のキーが同じなら使い回す。違えば停止してから起動する(理由 LAUNCH-CHANGED)。fake の
  ;; process に実行ファイルは無いので command は空。
  (<- wanted (launch-key #() spec))
  (setv reuse (and (or (isinstance origin ResumeSession) (and (isinstance origin FreshSession) session.warmed-fresh))
                   session.alive (= session.launch-key wanted)))
  (when (and session.alive (not reuse))
    (setv session.alive False session.stopped-because StopReason.LAUNCH-CHANGED))
  (setv session.launch-key wanted session.warmed-fresh False)
  ;; 本番の handler は起こした process の資格の家に宣言の資格を置き、使い回す手番では宣言の資格を家の file に書く — どちらも宣言の資格。
  (setv session.credential spec.credential)
  (setv memory (tuple (get world.transcripts key)))
  (.append (get world.transcripts key) input.text)
  (<- now-time (GetTime))
  (setv (get world.activity key) (.timestamp now-time))
  (<- reply FakeReply (reply-of world input.text memory))
  (when (not reuse)
    (<- (note-over-limit world session target False)))
  (<- turn (begin-fake-turn world session reply #(input.ref) True (not reuse) request.tap))
  (TurnStarted (ClaudeTurn session-id turn.seq) session-id))

(defk fake-warm-refusal [#^ FakeClaudeWorld world origin spec]
  {:pre [(: world FakeClaudeWorld) (: origin (| FreshSession ResumeSession)) (: spec ClaudeSessionSpec)]
   :post [(: % (| SessionIdInUse TurnInFlight SessionNotFound None))] :tags {:context "claude-code" :role "judgment"}}
  "事前起動を拒否するかを、ターンを始める時(fake-start-turn)と同じ拒否の型で決めるため(本番の handler の start-decision と同じ規則)。
   拒否しなければ None。新しい会話は、事前起動しただけの会話(warmed-fresh)を除いて、既知の id・記録の在る id を拒否する。続きは
   ターンの走っている会話と記録の無い会話を拒否する。"
  (val target origin.session-id)
  (val existing (.get world.sessions target))
  (val present (is-not (transcript-of world spec.home spec.cwd target) None))
  (match origin
    (FreshSession) (if (or (and (is-not existing None) (not existing.warmed-fresh)) present) (SessionIdInUse target) None)
    (ResumeSession) (cond
                      (and (is-not existing None) (is-not (.running existing) None))
                        (TurnInFlight (ClaudeTurn target existing.current-seq))
                      (not present) (SessionNotFound target)
                      True None)))

(defk fake-warm-session [#^ FakeClaudeWorld world #^ ClaudeWarmSession request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeWarmSession)] :post [(: % "WarmSessionOutcome")]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の process を最初の入力の前に起動して待たせるため(本番の handler と同じ規則 — 拒否はターンと同じ型・同じ起動条件のキーの
   生きた process が在れば起動しない・違えば停止して(理由 LAUNCH-CHANGED)起動する)。fake の CLI は入力の前に行を出さず、新しい
   会話の記録(transcript)は最初のターンで作る(本物の CLI も入力の前に記録を作らない)。"
  (val origin request.origin)
  (val spec request.spec)
  (val target origin.session-id)
  (match origin
    (ResumeSession :carry carry) (carry-into world spec.home spec.cwd target carry)
    _ None)
  (<- refused (fake-warm-refusal world origin spec))
  (when (is-not refused None) (return refused))
  (<- wanted (launch-key #() spec))
  (val session (or (.get world.sessions target) (FakeSession target spec.home spec.cwd)))
  (setv (get world.sessions target) session)
  (setv session.closed False)
  (when (and session.alive (= session.launch-key wanted))
    (return (SessionWarmed :session-id target)))
  (when session.alive
    (setv session.alive False session.stopped-because StopReason.LAUNCH-CHANGED))
  (<- (note-over-limit world session target True))
  (setv session.alive True
        session.launches (+ session.launches 1)
        session.launch-key wanted
        session.warmed-fresh (isinstance origin FreshSession)
        session.credential spec.credential)
  (SessionWarmed :session-id target))

(defn running-turn-of [#^ FakeClaudeWorld world #^ ClaudeTurn turn]
  (setv session (.get world.sessions turn.session-id))
  (setv running (if (is session None) None (.running session)))
  (if (and (is-not running None) (= running.seq turn.turn-seq)) running None))

(defk fake-inject [#^ FakeClaudeWorld world #^ ClaudeInjectInput request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeInjectInput)] :post [(: % (| InputQueued NoTurnInFlight))]}
  (setv turn (running-turn-of world request.turn))
  (when (is turn None) (return (NoTurnInFlight request.turn.session-id)))
  (.append turn.injections (FakeInjection request.input.ref request.input.text))
  (.append turn.refs request.input.ref)
  (<- (emit (get world.sessions request.turn.session-id) turn (InputFate request.input.ref "queued")))
  (InputQueued request.input.ref))

(defk fake-interrupt [#^ FakeClaudeWorld world #^ ClaudeInterruptTurn request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeInterruptTurn)] :post [(: % (| InterruptRequested InputAlreadyRead NoTurnInFlight))]}
  "止める(本物の対話の解釈 dialogue.hy の interrupt と同じ分け方): 入力を名指した止め(for-input)で、その入力がこの手番に入れた入力で、まだ読まれていない注入で
   なければ止めずに InputAlreadyRead(dialogue.hy の input-already-read と同じ — 偽の CLI はいつも入力の運命を名乗る)。読まれていない
   注入が在り、この手番の CLI が止めるの受理(interrupt_receipt_v1)を名乗っていれば control_request の形 — 注入を生き残った入力として
   次の手番で走らせる。それ以外は SIGINT の形(result は error_during_execution・aborted_streaming)で Interrupted — 読まれていない注入は
   捨てた入力(dropped-refs・on-result の StopSignal の道)。捨てた注入の行方の行は出さない(SIGINT の時に CLI が名乗る行は実測に無く、
   本物の handler も終わりだけを使う)。"
  (setv turn (running-turn-of world request.turn))
  (when (is turn None) (return (NoTurnInFlight request.turn.session-id)))
  (setv session (get world.sessions request.turn.session-id))
  (setv queued (lfor injection turn.injections :if (= injection.fate "queued") injection))
  (when (and (is-not request.for-input None) (in request.for-input turn.refs)
             (not-in request.for-input (lfor injection queued injection.ref)))
    (return (InputAlreadyRead request.for-input)))
  (if (and queued turn.reply.interrupt-receipt)
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_tools")))
        (setv memory (tuple (get world.transcripts (.transcript-key world session.home session.cwd session.session-id))))
        (<- reply FakeReply (reply-of world (.join "\n" (lfor injection queued injection.text)) memory))
        (<- next-turn (begin-fake-turn world session (FakeReply reply.text) (tuple (lfor injection queued injection.ref)) False False
                                       turn.tap))
        (<- (finish session turn (Interrupted :process-kept True :surviving-refs (tuple next-turn.refs)
                                              :continued-by (ClaudeTurn session.session-id next-turn.seq)))))
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_streaming")))
        ;; result の行で閉じた止めは、止めるまでの消費を運ぶ(本物の interrupted-by-result と同じ — 偽は筋書きの額を名乗る・card
        ;; ki-47e64b7b642f)。
        (<- (finish session turn (Interrupted :process-kept False :dropped-refs (tuple (lfor injection queued injection.ref))
                                              :usage turn.reply.usage :cost-usd turn.reply.cost-usd
                                              :model-usage turn.reply.model-usage
                                              :model-usage-gap (! (model-cost-gap turn.reply.model-usage turn.reply.cost-usd)))))
        ;; SIGINT の形の CLI は result の後に自分で降りる(本番の handler も降ろす — 訳 INTERRUPT-SIGNAL)。
        (setv session.alive False session.stopped-because StopReason.INTERRUPT-SIGNAL)))
  (InterruptRequested))

(defk fake-read-events [#^ FakeClaudeWorld world #^ ClaudeReadTurnEvents request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]}
  (setv session (.get world.sessions request.turn.session-id))
  (when (is session None) (return (UnknownTurn request.turn)))
  (setv turn (.get session.turns request.turn.turn-seq))
  (when (is turn None) (return (UnknownTurn request.turn)))
  (<- started (GetMonotonic))
  (setv deadline (+ started (float request.wait-up-to)))
  (while True
    (<- (advance world session turn))
    (setv lines (tuple (gfor line turn.lines :if (> line.seq request.after-seq) line)))
    (<- now (GetMonotonic))
    (when (or lines (is-not turn.end None) (>= now deadline))
      (return (TurnEventPage lines (if lines (. (get lines -1) seq) request.after-seq) turn.end)))
    ;; 次の刻 = 本文の行(quick・tool の相)か差分(text の相)の次の時刻(無ければ期限)と、相の期限 due-at の早い方。
    (setv line-at (next-line-at turn))
    (<- delta-at (next-delta-at turn))
    (setv next-at (if (is line-at None) delta-at line-at))
    (setv wake (if (in turn.phase #("quick" "tool" "text"))
                   (min turn.due-at deadline (if (is next-at None) deadline next-at))
                   deadline))
    ;; 筋書きの次の刻(行・期限)か、待ちの外で行か終わりが出て呼び鈴が鳴るまで眠る(呼び鈴は読み直す前に掛け、鳴らずに起きたら外す)。
    (<- bell (CreateExternalPromise))
    (setv turn.bells (+ turn.bells #(bell)))
    (<- _woke (WaitWithin bell.future (max MIN-SLEEP (- wake now)) :park True))
    (setv turn.bells (tuple (gfor other turn.bells :if (is-not other bell) other)))))

(defk fake-answer [#^ FakeClaudeWorld world #^ ClaudeAnswerPermission request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeAnswerPermission)] :post [(: % (| Answered NoSuchRequest))]}
  (setv turn (running-turn-of world request.turn))
  (when (or (is turn None) (!= turn.permission request.request-id) (!= turn.phase "permission"))
    (return (NoSuchRequest request.request-id)))
  (<- now (GetMonotonic))
  (setv turn.phase "tool" turn.due-at (+ now (max QUICK-TURN-SECONDS turn.reply.tool-seconds)) turn.permission None)
  (Answered))

(defk fake-close [#^ FakeClaudeWorld world #^ ClaudeCloseSession request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeCloseSession)] :post [(: % SessionClosed)]}
  (setv session (.get world.sessions request.session-id))
  (when (is session None) (return (SessionClosed False)))
  (setv running (.running session))
  (when (is-not running None)
    (<- (finish session running (Interrupted :process-kept False :dropped-refs (tuple (gfor injection running.injections
                                                                       :if (= injection.fate "queued")
                                                                       injection.ref))))))
  (setv session.closed True)
  (when session.alive
    (setv session.alive False session.stopped-because StopReason.SESSION-CLOSED))
  (SessionClosed (is-not running None)))

(defn fake-status [#^ FakeClaudeWorld world #^ ClaudeSessionStatus request]
  (setv key (.transcript-key world request.home request.cwd request.session-id))
  (setv transcript (if (in key world.transcripts)
                       (TranscriptPresent (.get world.activity key 0.0))
                       (TranscriptAbsent)))
  (setv session (.get world.sessions request.session-id))
  (setv running (if (is session None) None (.running session)))
  (SessionStatus (cond
                   (is-not running None) (TurnRunning (ClaudeTurn request.session-id running.seq))
                   (and (is-not session None) session.closed) (Closed)
                   True (Idle))
                 transcript))

(defk fake-export [#^ FakeClaudeWorld world #^ ClaudeExportSession request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeExportSession)] :post [(: % (| SessionExported SessionNotFound))]}
  "transcript の写し: 行を改行で結んだ本文(carry-into の Rebuilt が同じ行の列へ読み戻す形)。tail-bytes が在れば、本物と同じく
   末尾の tail-bytes byte に丸ごと収まる行だけ(行の頭から)。from-byte が在れば、本物と同じく本文の from-byte byte 目から後ろ
   (本文より短い・字の途中なら SessionNotFound)。無い・空 = SessionNotFound。"
  (val lines (transcript-of world request.home request.cwd request.session-id))
  (val ended (if (is lines None) [] (lfor line lines (+ line "\n"))))
  (when (is-not request.from-byte None)
    (val whole (.encode (.join "" ended) "utf-8"))
    (val after (if (> request.from-byte (len whole))
                   None
                   (try
                     (.decode (cut whole request.from-byte None) "utf-8")
                     (except [UnicodeDecodeError] None))))
    (return (if (and after (.strip after)) (SessionExported after) (SessionNotFound request.session-id))))
  (var kept ended)
  (when (is-not request.tail-bytes None)
    (var room request.tail-bytes)
    (var start (len ended))
    (while (and (> start 0) (<= (len (.encode (get ended (- start 1)) "utf-8")) room))
      (:= room (- room (len (.encode (get ended (- start 1)) "utf-8"))))
      (:= start (- start 1)))
    (:= kept (cut ended start None)))
  (val text (.join "" kept))
  (if (.strip text) (SessionExported text) (SessionNotFound request.session-id)))

(defk fake-drop [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)]}
  "会話の process を消すため(検の口 — 本番の答え手と同じく、手番を走らせていない生きた process も消す。走っている手番は
   BackendLost で終わる)。自分で消えた扱いで、降ろした訳は付けない。答え = 消す process が在ったか。"
  (setv session (.get world.sessions session-id))
  (when (is session None) (return False))
  (setv running (.running session))
  (when (is-not running None)
    (<- (finish session running (BackendLost "process killed (fake)")))
    (return True))
  (when (not session.alive) (return False))
  (setv session.alive False)
  True)

(defk fake-forget [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)]}
  "家から会話を消す(家を空にした形): 走っている手番は BackendLost で終わり(終わりは読める)、その会話の transcript を忘れる。
   以後の ResumeSession は SessionNotFound(写しを持ち込めば続く)。答え = 忘れた transcript が在ったか。"
  (setv session (.get world.sessions session-id))
  (when (is-not session None)
    (setv running (.running session))
    (when (is-not running None)
      (<- (finish session running (BackendLost "home emptied (fake)")))))
  (setv keys (lfor key world.transcripts :if (= (get key 2) session-id) key))
  (for [key keys]
    (del (get world.transcripts key))
    (.pop world.activity key None))
  (bool keys))

(defk fake-live-process [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % (| LiveProcess NoLiveProcess))]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の process の見え方を答えるため(本番の handler と同じ筋書きで、使い回しと守りを確かめる口 — #3672)。"
  (val session (.get world.sessions session-id))
  (cond
    (is session None) (NoLiveProcess :launches 0 :stopped-because None)
    session.alive (LiveProcess :launches session.launches)
    True (NoLiveProcess :launches session.launches :stopped-because session.stopped-because)))

(defk fake-emit-outside [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)] :tags {:context "claude-code" :role "foundation"}}
  "生きていて手番を走らせていない process に手番の外の出力をさせるため(守りの筋書きの口)。本番の handler と同じく、手番の外で
   出力した process は降ろす(訳 OUTSIDE-TURN-OUTPUT)。答え = 出させる process が在ったか。"
  (val session (.get world.sessions session-id))
  (when (or (is session None) (not session.alive) (is-not (.running session) None))
    (return False))
  (setv session.alive False session.stopped-because StopReason.OUTSIDE-TURN-OUTPUT)
  True)


(defk fake-replace-credential [#^ FakeClaudeWorld world #^ ClaudeReplaceSessionCredential request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeReplaceSessionCredential)]
   :post [(: % (| CredentialReplaced NoSuchSession SessionProcessDown HomeCredentialSession))]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の生きた process の資格を替えるため(本番の handler と同じ答え: 知らない会話 = NoSuchSession・生きた process が無い =
   SessionProcessDown・家の資格で起こした process = HomeCredentialSession・ほか = 資格を替えて CredentialReplaced)。"
  (val session-id request.session-id)
  (val session (.get world.sessions session-id))
  (cond
    (is session None) (NoSuchSession :session-id session-id)
    (not session.alive) (SessionProcessDown :session-id session-id)
    (is session.credential None) (HomeCredentialSession :session-id session-id)
    True (do (setv session.credential request.credential)
             (CredentialReplaced :session-id session-id))))


;; --- handler -----------------------------------------------------------------------------------

(defhandler fake-claude-code-handler [world]
  (ClaudeStartTurn [origin spec input]
    (<- outcome (fake-start-turn world effect))
    (resume outcome))
  (ClaudeInjectInput [turn input]
    (<- outcome (fake-inject world effect))
    (resume outcome))
  (ClaudeInterruptTurn [turn]
    (<- outcome (fake-interrupt world effect))
    (resume outcome))
  (ClaudeReadTurnEvents [turn after-seq wait-up-to]
    (<- page (fake-read-events world effect))
    (resume page))
  (ClaudeAnswerPermission [turn request-id answer]
    (<- outcome (fake-answer world effect))
    (resume outcome))
  (ClaudeCloseSession [session-id reason]
    (<- closed (fake-close world effect))
    (resume closed))
  (ClaudeSessionStatus [home cwd session-id]
    (resume (fake-status world effect)))
  (ClaudeExportSession [home cwd session-id]
    (<- exported (fake-export world effect))
    (resume exported))
  (ClaudeWarmSession [origin spec]
    (<- warmed (fake-warm-session world effect))
    (resume warmed))
  (ClaudeReplaceSessionCredential [session-id credential]
    (<- replaced (fake-replace-credential world effect))
    (resume replaced))
  (ClaudeDropProcess [session-id]
    (<- dropped (fake-drop world session-id))
    (resume dropped))
  (ClaudeLiveProcess [session-id]
    (<- view (fake-live-process world session-id))
    (resume view))
  (ClaudeEmitOutsideTurn [session-id]
    (<- emitted (fake-emit-outside world session-id))
    (resume emitted))
  (ClaudeForgetSession [session-id]
    (<- forgotten (fake-forget world session-id))
    (resume forgotten)))


;; --- 1 回きりの print の fake -------------------------------------------------------------------------

(defk fake-print [#^ Callable respond #^ ClaudePrintOnce request]
  {:pre [(: respond Callable) (: request ClaudePrintOnce)] :post [(: % (| PrintAnswered PrintFailed PrintLimited PrintTimedOut))]
   :tags {:context "claude-code" :role "foundation"}}
  "1 回きりの print に筋書き(respond — 宣言 → 答えの Program)の答えで答えるため(CLI を起こさない・資格の置き場も作らない)。筋書きの
   答えが答えの和の外なら TypeError(筋書きの誤りを本番に無い値で隠さない)。"
  (<- outcome (respond request))
  (when (not (isinstance outcome #(PrintAnswered PrintFailed PrintLimited PrintTimedOut)))
    (raise (TypeError (.format "fake の 1 回きりの print の筋書きの答えは PrintAnswered / PrintFailed / PrintLimited / PrintTimedOut: {}"
                               (. (type outcome) __name__)))))
  outcome)

;; 引数に残す理由: 筋書き(respond)は検ごとに違い、同じ組に本番の handler の代わりに並べる。
(defhandler fake-claude-print-handler [respond]
  "1 回きりの print(ClaudePrintOnce)の fake。respond = 宣言 → 答え(PrintAnswered | PrintFailed | PrintLimited | PrintTimedOut)の Program を作る
   関数(効果は fake の handler の外側が答える)。"
  (ClaudePrintOnce [home model]
    (<- outcome (fake-print respond effect))
    (resume outcome)))
