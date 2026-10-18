;; code mode が使えない警告の検(card ki-86b1443b22b6)— 版を固定した本物の codex(0.162.1)を、code mode だけで道具を呼ぶ設定で、
;; code mode の host の無い置き場(code-mode-unavailable)と、起動できない host を置いた置き場(code-mode-host-broken)から起こして録った
;; 行(tests/recorded/codex-0.162.1・録り方は scripts/record_app_server_lines.py)で確かめる。
;;
;; 守る事: codex が code mode を使えず道具の呼びを閉じたと名乗った process では、model は道具を 1 つも呼べないまま文だけを返し、ターンは
;; completed になる(本番の実例 = 10-15 の会話 c-HFAMV0E6RHQJ773QTWP1HJ58T2)。警告は捨てずに型のある事実 CodeModeUnavailable にし、
;; handler はその process のターンを ToolsUnavailable で終える。警告はターンを名乗らず、ターンの前(turn/start の答えより前)にも、
;; ターンの途中にも届きうるので、どちらの順でもターンは終わる。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <- var])
(import json)
(import doeff_codex.lines [classify-line CodeModeUnavailable CodexWarning Response TurnStarted TextDelta
                           CODE-MODE-UNAVAILABLE-HEAD CODE-MODE-FAIL-CLOSED])
(import doeff_codex.handler [Continuation SessionRuntime absorb])
(import doeff_codex.effects [ToolsUnavailable])
(import doeff_codex.values [CodexSessionSpec CodexHome])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import tests.test_lines [RECORDED recorded-records recorded-line])


(defk fresh-runtime []
  {:pre [] :post [(: % SessionRuntime)] :tags {:context "codex-test" :role "foundation"}}
  "process を持たない会話の状態を作るため(読みの thread の振り分け absorb だけを、行の順を替えて当てる)。"
  (val runtime (SessionRuntime (Continuation (CodexSessionSpec :home (CodexHome :env {}) :cwd "/tmp" :approval-policy ApprovalPolicy.NEVER
                                                 :sandbox SandboxMode.DANGER-FULL-ACCESS))))
  (setv runtime.continuation.thread-id "t")
  runtime)


(deftest test-the-code-mode-warning-is-a-fact-of-its-own-once-and-before-the-turn
  ;; host の無い置き場の codex は、thread の最初のターンを始める時に、code mode が使えず道具の呼びを閉じたと 1 度だけ名乗る。その行は
  ;; turn/start の答え(id 3)より前に届き、2 つ目のターン(id 4)の前には出ない — 走っているターンの無い時に届く事実。
  (<- records (recorded-records "code-mode-unavailable"))
  (val facts (tuple (gfor #(index record) (enumerate records) :if (isinstance record CodeModeUnavailable) #(index record))))
  (assert (= (len facts) 1) facts)
  (val at (get facts 0 0))
  (val fact (get facts 0 1))
  (assert (.startswith fact.message CODE-MODE-UNAVAILABLE-HEAD) fact)
  (assert (in CODE-MODE-FAIL-CLOSED fact.message) fact)
  (assert (in "codex-code-mode-host: host executable was not found" fact.message) fact)
  (val first-answer (next (gfor #(index record) (enumerate records) :if (and (isinstance record Response) (= record.id 3)) index)))
  (assert (< at first-answer) #(at first-answer))
  ;; ほかの警告(試しの機能・model の宣言が無い)は文を持つ CodexWarning で、道具を呼べない事実と分ける。
  (val others (tuple (gfor record records :if (isinstance record CodexWarning) record.message)))
  (assert (any (gfor message others (.startswith message "Model metadata for `gpt-mock` not found"))) others)
  (assert (not (any (gfor message others (.startswith message CODE-MODE-UNAVAILABLE-HEAD)))) others))


(deftest test-a-code-mode-warning-that-falls-back-to-direct-tools-is-only-a-warning
  ;; code mode と直の道具の両方を持つ形では、同じ頭の警告が「直の道具に戻る」と名乗る — 道具は使えるので、ターンを終える事実にしない。
  ;; 元は録った code mode の警告の行(振る舞いの句だけを替える)。
  (<- line (recorded-line "code-mode-unavailable" "warning"))
  (<- warnings (recorded-records "code-mode-unavailable"))
  (val fact (next (gfor record warnings :if (isinstance record CodeModeUnavailable) record)))
  (val fallback {#** line "params" {"threadId" fact.thread-id
                                    "message" (.replace fact.message CODE-MODE-FAIL-CLOSED "Falling back to direct tools")}})
  (<- read (classify-line (json.dumps fallback)))
  (assert (= read (CodexWarning :thread-id fact.thread-id :message (get fallback "params" "message"))) read)
  ;; thread を名乗らない警告も読める(codex 0.162.1 の WarningNotification の thread の欄は無いこともある)。
  (<- bare (classify-line (json.dumps {"method" "warning" "params" {"message" fact.message}})))
  (assert (= bare (CodeModeUnavailable :thread-id None :message fact.message)) bare))


(deftest test-a-broken-host-that-exists-gives-no-warning-in-this-version
  ;; 比べの実測: codex 0.162.1 は code mode を使えるかを host の file が在るかだけで決める(codex-rs/code-mode の availability)。在るが
  ;; 起動できない host(起こすとすぐ code 1 で終わる)の隣の codex は警告を出さず、exec の呼びは stdout に item を出さずに model へ
  ;; 誤りを返し、ターンは completed で終わる(訳は stderr の `code-mode host exited during handshake` だけ)— この形は警告では
  ;; 捕まらない。この版で警告が出るようになったら、捕まえ方を決め直す。
  (<- records (recorded-records "code-mode-host-broken"))
  (val facts (tuple (gfor record records :if (isinstance record CodeModeUnavailable) record)))
  (assert (= facts #()) facts)
  (val messages (tuple (gfor record records :if (isinstance record CodexWarning) record.message)))
  (assert (not (any (gfor message messages (in "Code Mode" message)))) messages))


(deftest test-the-warning-before-the-turn-ends-the-turn-that-follows
  ;; 録った順(警告 → turn/start の答え → turn/started → 文だけの答え …)のまま振り分けると、答えで立ったターンはその場で
  ;; ToolsUnavailable で終わり、終えたターンの後の行は次のターンの頭(late)に取っておかれない。
  (<- runtime (fresh-runtime))
  (val lines (lfor line (.splitlines (.read-text (/ RECORDED "code-mode-unavailable.stdout.jsonl") :encoding "utf-8")) :if (.strip line)
                   line))
  (val messages (lfor line lines (json.loads line)))
  (setv runtime.continuation.thread-id (get (next (gfor message messages :if (= (.get message "id") 2) message)) "result" "thread" "id"))
  (val turn (get (next (gfor message messages :if (= (.get message "id") 3) message)) "result" "turn" "id"))
  ;; 1 つ目のターンの終わり(turn/completed)までを振り分ける — 録りの 2 つ目のターンは、本番では降ろした後の新しい process の物。
  (val first-end (next (gfor #(index message) (enumerate messages) :if (= (.get message "method") "turn/completed") index)))
  (for [line (cut lines 0 (+ first-end 1))]
    (<- record (classify-line line))
    (<- (absorb runtime record)))
  (val log (get runtime.turns turn))
  (assert (= log.end (ToolsUnavailable :detail runtime.tools-lost.message)) log.end)
  (assert (in CODE-MODE-FAIL-CLOSED log.end.detail) log.end)
  ;; ターンの出来事に文だけの答えは無い(終えた後の行はターンの外)。
  (val texts (tuple (gfor event log.events :if (isinstance event.record TextDelta) event.record)))
  (assert (= texts #()) log.events)
  (assert (= runtime.late #()) runtime.late)
  (assert (any (gfor record runtime.outside (isinstance record TextDelta))) runtime.outside))


(deftest test-a-warning-during-a-running-turn-ends-that-turn
  ;; 警告がターンの始まりより後に届く順(答えと通知の順は codex の中で別の道を通る)でも、走っているターンは ToolsUnavailable で終わり、
  ;; 警告はそのターンの出来事に積まれる。
  (<- runtime (fresh-runtime))
  (<- (absorb runtime (Response :id 3 :turn-id "u")))
  (<- (absorb runtime (TurnStarted :thread-id "t" :turn-id "u")))
  (assert (is (. (get runtime.turns "u") end) None) (get runtime.turns "u"))
  (val fact (CodeModeUnavailable :thread-id "t" :message (+ CODE-MODE-UNAVAILABLE-HEAD "the host is gone. " CODE-MODE-FAIL-CLOSED ".")))
  (<- (absorb runtime fact))
  (val log (get runtime.turns "u"))
  (assert (= log.end (ToolsUnavailable :detail fact.message)) log.end)
  (assert (in fact (tuple (gfor event log.events event.record))) log.events)
  ;; 終えたターンを名乗る後の行は、次の process のターンの頭へ持ち越さない。
  (<- (absorb runtime (TextDelta :thread-id "t" :turn-id "u" :item-id "m" :text "no tools")))
  (assert (= runtime.late #()) runtime.late))
