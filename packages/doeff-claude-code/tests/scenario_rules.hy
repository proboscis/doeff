;;; 筋書きの prompt と、その prompt への返事の規則(純関数)。
;;;
;;; 同じ prompt を 3 つの相手に渡す: 本物の claude(prompt の言葉どおりに振る舞う)・替え玉の CLI(stub_cli/claude.hy がこの規則を
;;; import する)・fake(interpreters.hy がこの規則を FakeReply に写す)。規則は「本物ならこう振る舞う」の写しで、本物の
;;; 筋書きが緑になる形と同じ言葉を読む。
(import re)

(setv CODEWORD "OKAPI-77")
(setv EXTRA-WORD "EXTRA-9")

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
  "prompt → {\"text\" 返事の本文 \"tool_seconds\" 道具の秒数 \"permission\" 道具の前に許可を問うか \"touch\" 触る path}。
   memory = それまでの入力の本文(会話の記憶)。"
  (setv sleep (re.search r"sleep (\d+(?:\.\d+)?)" text))
  (setv touch (re.search r"touch (\S+)" text))
  (setv exact (re.search r"[Rr]eply with exactly: (\S+)" text))
  (setv extra (re.search r"include the word (\S+)" text))
  (setv word
        (cond
          (in "What was the codeword" text)
            (next (gfor earlier memory
                        :setv found (re.search r"codeword (\S+?)\." earlier)
                        :if found (.group found 1))
                  "UNKNOWN")
          exact (.group exact 1)
          extra (.group extra 1)
          True "OK"))
  {"text" word
   "tool_seconds" (cond sleep (float (.group sleep 1)) touch 0.2 True 0.0)
   "permission" (is-not touch None)
   "touch" (if touch (.group touch 1) None)})
