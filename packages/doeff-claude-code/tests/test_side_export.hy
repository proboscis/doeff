;;; 会話の側の記録(<会話の id>/ の下の subagents/・tool-results/ の file)の取り出し ClaudeExportSessionSide の検。
;;; 本番の handler は替え玉の家(tmp の dir)に置いた file を、fake は世界に置いた file を、since-ms より後に変わった物だけ同じ bytes で
;;; 返す。本体(transcript)の無い会話は SessionNotFound。
(require doeff-hy.macros [deftest defk <- val])
(import os)
(import uuid)
(import pathlib [Path])
(import doeff [with_handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_claude_code [ClaudeHome ClaudeSessionSpec FreshSession ClaudeStartTurn TurnStarted ClaudeForgetSession
                           ClaudeExportSessionSide SessionSideExported SideFile SessionNotFound
                           ClaudeCodeHost claude-code-handler FakeClaudeWorld FakeReply fake-claude-code-handler])
(import doeff_claude_code._argv [transcript-path])
(import doeff_claude_code._clock [clock-of])
(import tests.interpreters [child-env])
(import tests.scenario_steps [read-to-end typed])

;; 置く file の変えた刻(epoch ミリ秒)。since-ms は OLD と NEW の間。
(val OLD-MS 1793600000000)
(val SINCE-MS 1793610000000)
(val NEW-MS 1793620000000)
;; binary の中身(png の頭・NUL・utf-8 でない byte)。
(val PNG-BYTES (+ b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" (bytes (range 256))))


(defn real-host []
  (ClaudeCodeHost #("/nonexistent/claude-binary") (clock-of (sync-time-handler)) 8 7200.0))

(defn with-real-handler [program]
  (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler (real-host))] program))

(defn put-file [#^ Path path #^ bytes data #^ int modified-ms]
  (.mkdir path.parent :parents True :exist-ok True)
  (.write-bytes path data)
  (os.utime path :ns #((* modified-ms 1000000) (* modified-ms 1000000))))

(defn real-home [#^ Path tmp-path]
  "替え玉の家(tmp の dir)。"
  (ClaudeHome (str (/ tmp-path "home")) (child-env "")))

(defn real-cwd [#^ Path tmp-path]
  "替え玉の作業場(作って path を返す)。"
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (str work))

(defn side-dir [#^ ClaudeHome home #^ str cwd #^ str sid]
  (Path (.removesuffix (transcript-path home.config-dir (os.path.realpath cwd) sid) ".jsonl")))


(deftest test-real-export-of-the-side-returns-only-files-changed-after-since [tmp-path]
  ;; 本番の handler: 側の dir の下の file のうち since-ms より後に変わった物だけを、<会話の id>/ からの相対の path の順に、同じ bytes・
  ;; 大きさ・変えた刻で返す。前の物は返らない。since-ms なし = 全部。cwd は実体の path で置き場を決める(symlink の cwd でも同じ)。
  (val home (real-home tmp-path))
  (val cwd (real-cwd tmp-path))
  (val sid (str (uuid.uuid4)))
  (val body "{\"type\":\"user\"}\n")
  (put-file (Path (transcript-path home.config-dir (os.path.realpath cwd) sid)) (.encode body "utf-8") OLD-MS)
  (val side (side-dir home cwd sid))
  (val new-agent (.encode "{\"type\":\"assistant\",\"text\":\"子の記録\"}\n" "utf-8"))
  (val new-meta b"{\"agentType\":\"general-purpose\"}")
  (val old-agent b"{\"old\":true}\n")
  (put-file (/ side "subagents" "agent-a1.jsonl") new-agent NEW-MS)
  (put-file (/ side "subagents" "agent-a1.meta.json") new-meta NEW-MS)
  (put-file (/ side "subagents" "agent-a0.jsonl") old-agent OLD-MS)
  (put-file (/ side "subagents" "workflows" "wf_1" "journal.jsonl") b"{}\n" NEW-MS)
  (put-file (/ side "tool-results" "shot.png") PNG-BYTES NEW-MS)
  (put-file (/ side "tool-results" "old.txt") b"old" OLD-MS)
  ;; ちょうど since-ms に変えた file は「より後」でない。
  (put-file (/ side "tool-results" "edge.txt") b"edge" SINCE-MS)
  (val alias (/ tmp-path "alias"))
  (.symlink-to alias cwd)
  (<- grown (with-real-handler (ClaudeExportSessionSide home (str alias) sid SINCE-MS)))
  (assert (= grown (SessionSideExported
                     #((SideFile "subagents/agent-a1.jsonl" (len new-agent) NEW-MS new-agent)
                       (SideFile "subagents/agent-a1.meta.json" (len new-meta) NEW-MS new-meta)
                       (SideFile "subagents/workflows/wf_1/journal.jsonl" 3 NEW-MS b"{}\n")
                       (SideFile "tool-results/shot.png" (len PNG-BYTES) NEW-MS PNG-BYTES))))
          (repr grown))
  (<- whole (with-real-handler (ClaudeExportSessionSide home cwd sid)))
  (assert (= (lfor f whole.files f.path)
             ["subagents/agent-a0.jsonl" "subagents/agent-a1.jsonl" "subagents/agent-a1.meta.json"
              "subagents/workflows/wf_1/journal.jsonl" "tool-results/edge.txt" "tool-results/old.txt" "tool-results/shot.png"])
          (repr whole))
  (<- later (with-real-handler (ClaudeExportSessionSide home cwd sid NEW-MS)))
  (assert (= later (SessionSideExported #())) (repr later)))


(deftest test-real-export-of-the-side-without-the-transcript-is-session-not-found [tmp-path]
  ;; 本体(transcript)の無い会話は、側の dir が在っても SessionNotFound。本体が在って側の dir が無い会話は空の並び。
  (val home (real-home tmp-path))
  (val cwd (real-cwd tmp-path))
  (val orphan (str (uuid.uuid4)))
  (val bare (str (uuid.uuid4)))
  (put-file (/ (side-dir home cwd orphan) "subagents" "agent-x.jsonl") b"{}\n" NEW-MS)
  (put-file (Path (transcript-path home.config-dir (os.path.realpath cwd) bare)) b"{}\n" NEW-MS)
  (<- missing (with-real-handler (ClaudeExportSessionSide home cwd orphan 0)))
  (assert (= missing (SessionNotFound orphan)) (repr missing))
  (<- empty (with-real-handler (ClaudeExportSessionSide home cwd bare 0)))
  (assert (= empty (SessionSideExported #())) (repr empty)))


(deftest test-real-export-of-the-side-skips-links-out-of-the-side-dir [tmp-path]
  ;; 側の dir の中の symlink は辿らない(置き場の外の file を写さない)。
  (val home (real-home tmp-path))
  (val cwd (real-cwd tmp-path))
  (val sid (str (uuid.uuid4)))
  (put-file (Path (transcript-path home.config-dir (os.path.realpath cwd) sid)) b"{}\n" OLD-MS)
  (val outside (/ tmp-path "secret.txt"))
  (put-file outside b"secret" NEW-MS)
  (val side (side-dir home cwd sid))
  (put-file (/ side "tool-results" "kept.txt") b"kept" NEW-MS)
  (.symlink-to (/ side "tool-results" "link.txt") outside)
  (.symlink-to (/ side "linked-dir") tmp-path)
  (<- exported (with-real-handler (ClaudeExportSessionSide home cwd sid SINCE-MS)))
  (assert (= (lfor f exported.files f.path) ["tool-results/kept.txt"]) (repr exported)))


(deftest test-the-side-export-request-checks-its-values
  ;; session-id は会話の id の綴り(置き場の外を名指せない)・since-ms は 0 以上の整数か None。
  (val home (ClaudeHome "home"))
  (for [bad ["../x" "not-a-uuid"]]
    (try
      (ClaudeExportSessionSide home "/work" bad)
      (assert False (.format "綴りの外の id {!r} を受けた" bad))
      (except [ValueError] None)))
  (for [bad [-1 True 1.5]]
    (try
      (ClaudeExportSessionSide home "/work" (str (uuid.uuid4)) bad)
      (assert False (.format "since-ms {!r} を受けた" bad))
      (except [ValueError] None))))


;; --- fake ----------------------------------------------------------------------------------------

(val FAKE-SPEC (ClaudeSessionSpec :home (ClaudeHome "fake-home") :cwd "/work"))

(defk side-after-one-turn [#^ FakeClaudeWorld world #^ str sid]
  {:pre [(: world FakeClaudeWorld) (: sid str)] :post [(: % tuple)]}
  "fake の会話で手番を 1 つ走らせ、側の file を世界に置いて since-ms で取り出し、会話を忘れた後にもう一度取り出す。"
  (<- started (ClaudeStartTurn (FreshSession sid) FAKE-SPEC (typed "hello")))
  (assert (isinstance started TurnStarted) (repr started))
  (<- (read-to-end started.turn 120.0))
  (.put-side-file world FAKE-SPEC.home FAKE-SPEC.cwd sid "subagents/agent-a1.jsonl" b"{\"new\":1}\n" NEW-MS)
  (.put-side-file world FAKE-SPEC.home FAKE-SPEC.cwd sid "subagents/agent-a0.jsonl" b"{\"old\":1}\n" OLD-MS)
  (.put-side-file world FAKE-SPEC.home FAKE-SPEC.cwd sid "tool-results/shot.png" PNG-BYTES NEW-MS)
  (<- grown (ClaudeExportSessionSide FAKE-SPEC.home FAKE-SPEC.cwd sid SINCE-MS))
  (<- whole (ClaudeExportSessionSide FAKE-SPEC.home FAKE-SPEC.cwd sid))
  (<- (ClaudeForgetSession sid))
  (<- forgotten (ClaudeExportSessionSide FAKE-SPEC.home FAKE-SPEC.cwd sid))
  (<- unknown (ClaudeExportSessionSide FAKE-SPEC.home FAKE-SPEC.cwd (str (uuid.uuid4))))
  #(grown whole forgotten unknown))

(deftest test-fake-export-of-the-side-answers-like-the-real-handler
  ;; fake: 世界に置いた側の file のうち since-ms より後の物だけを path の順に同じ bytes で返す。忘れた会話・知らない会話は SessionNotFound。
  (val world (FakeClaudeWorld (fn [text memory] (FakeReply text))))
  (val sid (str (uuid.uuid4)))
  (<- #(grown whole forgotten unknown)
      (with_handlers [(sim-time-handler :clock (SimClock)) (fake-claude-code-handler world)] (side-after-one-turn world sid)))
  (assert (= grown (SessionSideExported
                     #((SideFile "subagents/agent-a1.jsonl" 10 NEW-MS b"{\"new\":1}\n")
                       (SideFile "tool-results/shot.png" (len PNG-BYTES) NEW-MS PNG-BYTES))))
          (repr grown))
  (assert (= (lfor f whole.files f.path) ["subagents/agent-a0.jsonl" "subagents/agent-a1.jsonl" "tool-results/shot.png"])
          (repr whole))
  (assert (= forgotten (SessionNotFound sid)) (repr forgotten))
  (assert (isinstance unknown SessionNotFound) (repr unknown)))
