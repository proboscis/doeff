;;; 筋書きの prompt と、その prompt への返事の規則(純関数)。
;;;
;;; 同じ prompt を 3 つの相手に渡す: 本物の claude(prompt の言葉どおりに振る舞う)・替え玉の CLI(stub_cli/claude.hy がこの規則を
;;; import する)・fake(interpreters.hy がこの規則を FakeReply に写す)。規則は「本物ならこう振る舞う」の写しで、本物の
;;; 筋書きが緑になる形と同じ言葉を読む。
(require doeff-hy.macros [val])
(import json)
(import re)

(setv CODEWORD "OKAPI-77")
(setv EXTRA-WORD "EXTRA-9")
;; 最後の本文を何片の差分(stream_event の text_delta)に分けて出させるかの言い方。本物の claude は片の数を選べない(言葉どおりの
;; 本文を返すだけ)ので、片の数を当てにする検は替え玉の CLI と fake だけに置く(#3628)。
(setv STREAM-PHRASE "Stream the reply in {} pieces.")
;; 答え始めの前にモデルが考える秒を替え玉の CLI に作らせる言い方(stream の message_start を先に出し、その秒だけ待ってから本文の差分を
;; 流す — #3696)。本物の claude は考える秒を選べないので、秒を当てにする検は替え玉の CLI だけに置く。
(setv THINK-PHRASE "Think for {} seconds before replying.")
;; init の後に hook の知らせ(stream でない system の行)を出し、その秒だけ待ってから答え始める言い方(実物の CLI は init の直後に入力ごとの
;; hook を走らせ、その後で API へ出す — #3696 の直し)。秒を当てにする検は替え玉の CLI だけに置く。
(setv HOOK-PHRASE "Run the hooks for {} seconds first.")
;; 答えの前に考えている間の差分(stream_event の thinking_delta)を何片出させるか・道具の呼びの命令を何片の差分(input_json_delta)で
;; 書かせるかの言い方(#3746 (a))。本物の claude は片の数を選べないので、片の数を当てにする検は替え玉の CLI と fake だけに置く。
(setv THINKING-PIECES-PHRASE "Stream {} thinking pieces.")
(setv TOOL-INPUT-PIECES-PHRASE "Stream the tool input in {} pieces.")
;; Stop hook が最初の答えを理由つきで 1 度差し戻す言い方と、差し戻される答えの本文(#4020)。本物の claude で差し戻しを起こすには
;; Stop hook を置く必要があるので、差し戻しを当てにする検は替え玉の CLI と fake だけに置く。
(setv HOOK-FEEDBACK-PHRASE "Have the Stop hook send back a first answer with the reason: {}.")
(setv REJECTED-ANSWER "DRAFT-1")
;; 答えの前に CLI が会話を自動で 1 度圧縮する言い方と、圧縮の行の compact_metadata の値(#4189)。値は本物の CLI 2.1.289 の
;; auto の圧縮が会話の記録に残した compactMetadata(2026-10-05 13:20 — trigger auto・preTokens 969482・postTokens 252050・
;; cumulativeDroppedTokens 11155244・durationMs 2730)を、stream-json の綴り(CLI 2.1.294 の書き手が snake の名へ写す)にした物。本物の
;; claude で圧縮を起こすには閾値を越えるまで会話を育てる必要があるので、圧縮を当てにする検は替え玉の CLI と fake だけに置く。
(val COMPACT-PHRASE "Compact the context before replying.")
(val COMPACT-METADATA {"trigger" "auto" "pre_tokens" 969482 "post_tokens" 252050 "cumulative_dropped_tokens" 11155244
                       "duration_ms" 2730})

;; 答えの前に UserPromptSubmit の hook が条件つきルールの助言を返す言い方と、その hook の stdout(hook が書く JSON — 助言は
;; hookSpecificOutput.additionalContext・ki-d8b473480303)。本物の claude で助言を出すには hook を置く必要があるので、
;; 助言を当てにする検は替え玉の CLI と fake だけに置く。
(setv ADVICE-PHRASE "Have the hook advise: {}.")
;; 入力に添付した画像(stream-json の user の content の image の block)を数えて答えさせる言い方と、替え玉の CLI が image の block 1 つを
;; 本文へ写す印(card acp:kanban-issue:ki-0faa366b76c0 — 画像が CLI の入力に入ったかを見るため)。本物の claude も画像を数えて答えられるが、
;; 印の綴りは替え玉の CLI だけの物なので、数を当てにする検は替え玉の CLI に置く。言い方の無い入力でも、文字が空で画像だけの入力は数えて答える
;; (利用者が画像だけを貼って送る形)。
(val IMAGES-PHRASE "Count the attached images.")
(val IMAGE-MARK "[stub:image]")

(defn #^ str advice-stdout [#^ str advice]
  "助言 advice を返す UserPromptSubmit の hook の stdout(替え玉の CLI と fake の同じ 1 か所)。"
  (json.dumps {"hookSpecificOutput" {"hookEventName" "UserPromptSubmit" "additionalContext" advice}} :ensure-ascii False))

(defn #^ str remember-prompt [#^ str word]
  (.format "Remember the codeword {}. Reply with exactly: {}" CODEWORD word))

(defn #^ str recall-prompt []
  "What was the codeword I asked you to remember? Reply with only the codeword.")

(defn #^ str reply-prompt [#^ str word]
  (.format "Reply with exactly: {}" word))

(defn #^ str sleep-prompt [#^ int seconds #^ str word]
  (.format "Use the Bash tool to run exactly this command: sleep {} . When it finishes, reply with exactly: {}" seconds word))

(defn #^ str touch-prompt [#^ str path #^ str word]
  (.format "Use the Bash tool to run exactly this command: touch {} . Then reply with exactly: {}" path word))

(defn #^ str extra-prompt []
  (.format "Also include the word {} in your final reply." EXTRA-WORD))


(defn #^ dict reply-for [#^ str text #^ tuple memory]
  "prompt → {\"text\" 返事の本文 \"tool_seconds\" 道具の秒数 \"permission\" 道具の前に許可を問うか \"touch\" 触る path
   \"deltas\" 最後の本文を分ける差分の片の数(STREAM-PHRASE・無ければ 0 = 差分を出さない)
   \"think_seconds\" 本文の差分の前に考える秒(THINK-PHRASE・無ければ 0.0 — 替え玉の CLI だけが読む)
   \"hook_seconds\" init の後に hook の知らせを出して待つ秒(HOOK-PHRASE・無ければ 0.0 — 替え玉の CLI だけが読む)
   \"tool_command\" 道具に走らせる命令の文(「run exactly this command: <命令> .」の命令・無ければ None — 道具の呼びの input の command)
   \"tool_output\" 道具の出力(echo <語> の語・ほかの命令は空 — 道具の結果の content。#3744)
   \"thinking_deltas\" 手番の始めに出す考えている間の差分の片の数(THINKING-PIECES-PHRASE・無ければ 0)
   \"tool_input_deltas\" 道具の呼びの命令を書く差分の片の数(TOOL-INPUT-PIECES-PHRASE・無ければ 0 — #3746 (a))
   \"hook_feedback\" Stop hook が最初の答え(REJECTED-ANSWER)を差し戻す理由(HOOK-FEEDBACK-PHRASE・無ければ None — #4020)
   \"compact\" 答えの前に CLI が会話を 1 度自動で圧縮するか(COMPACT-PHRASE — 圧縮の行の compact_metadata は COMPACT-METADATA・#4189)
   \"advice\" 答えの前に UserPromptSubmit の hook が返す助言(ADVICE-PHRASE・無ければ None — ki-d8b473480303)}。
   memory = それまでの入力の本文(会話の記憶)。"
  (setv sleep (re.search r"sleep (\d+(?:\.\d+)?)" text))
  (setv touch (re.search r"touch (\S+)" text))
  (setv command (re.search r"run exactly this command: (.+?) \." text))
  (setv echoed (if command (re.fullmatch r"echo (\S+)" (.group command 1)) None))
  (setv exact (re.search r"[Rr]eply with exactly: (\S+)" text))
  (setv extra (re.search r"include the word (\S+)" text))
  (setv streamed (re.search r"Stream the reply in (\d+) pieces" text))
  (setv think (re.search r"Think for (\d+(?:\.\d+)?) seconds before replying" text))
  (setv hooks (re.search r"Run the hooks for (\d+(?:\.\d+)?) seconds first" text))
  (setv thinking-pieces (re.search r"Stream (\d+) thinking pieces" text))
  (setv tool-input-pieces (re.search r"Stream the tool input in (\d+) pieces" text))
  (setv sent-back (re.search r"Have the Stop hook send back a first answer with the reason: (\S+?)\." text))
  (setv advised (re.search r"Have the hook advise: (\S+?)\." text))
  (setv images (.count text IMAGE-MARK))
  (setv word
        (cond
          (and (> images 0) (or (in IMAGES-PHRASE text) (= (.strip (.replace text IMAGE-MARK "")) "")))
            (.format "IMAGES-{}" images)
          (in "What was the codeword" text)
            (next (gfor earlier memory
                        :setv found (re.search r"codeword (\S+?)\." earlier)
                        :if found (.group found 1))
                  "UNKNOWN")
          exact (.group exact 1)
          extra (.group extra 1)
          True "OK"))
  {"text" word
   "tool_seconds" (cond sleep (float (.group sleep 1)) touch 0.2 echoed 0.2 True 0.0)
   "permission" (is-not touch None)
   "touch" (if touch (.group touch 1) None)
   "tool_command" (if command (.group command 1) None)
   "tool_output" (if echoed (.group echoed 1) "")
   "thinking_deltas" (if thinking-pieces (int (.group thinking-pieces 1)) 0)
   "tool_input_deltas" (if tool-input-pieces (int (.group tool-input-pieces 1)) 0)
   "hook_feedback" (if sent-back (.group sent-back 1) None)
   "compact" (in COMPACT-PHRASE text)
   "advice" (if advised (.group advised 1) None)
   "deltas" (if streamed (int (.group streamed 1)) 0)
   "think_seconds" (if think (float (.group think 1)) 0.0)
   "hook_seconds" (if hooks (float (.group hooks 1)) 0.0)})
