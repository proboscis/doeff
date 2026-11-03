;; 会話の記録(rollout)の取り出しと書き戻しの検 — 本番の handler + 替え玉の app-server(状態の DB の行の path が無い thread の
;; thread/resume を、本物の codex 0.162.1 と同じ「no rollout found for thread id」で断る)と、記録の読み方の判断。
;;
;; 守る事: 家の外に預けた記録(CodexExportThread の答え)を持ち込めば、機体の掃除が rollout を外した後も同じ thread を続けられる
;; (書き戻す置き場は元と同じ — 本物の codex は別の日の dir に戻した記録を断る。2026-11-02 に zeus の一時の家で測った)。取り出しは
;; 行の終わりまでで、前の写しの長さから後ろだけを読める。これが崩れると、codex の会話の記録を手元から外せない(/work が週 20G 増える)か、
;; 外した後の会話が続きから始められない。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import dataclasses [replace])
(import sys)
(import pathlib [Path])
(import doeff [with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_codex [FreshThread ResumeThread CodexInput CodexSessionSpec CodexHome CodexHost codex-handler])
(import doeff_codex [CodexStartTurn CodexCloseSession CodexExportThread SessionClosed TurnStarted ThreadExported ThreadUnknown])
(import doeff_codex._rollout [read-rollout carry-place-of restore-rollout rollout-of])
(import tests.interpreters [STUB-PATH CODEX-HOME-DIR settings-for])
(import tests.scenario_steps [read-to-end])

(val TURN-WAIT-SECONDS 30.0)


(defk plain-spec [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % CodexSessionSpec)] :tags {:context "codex-test" :role "program"}}
  "家の資格のまま起こす会話の宣言を作るため(元の CODEX_HOME は tmp の下)。"
  (<- settings (settings-for "stub" tmp-path))
  (.mkdir (/ tmp-path CODEX-HOME-DIR) :exist-ok True)
  (replace settings.spec :home (CodexHome :env (| settings.spec.home.env {"STUB_ID_PREFIX" "r"}))))

(defk one-turn [#^ CodexSessionSpec spec origin]
  {:pre [(: spec CodexSessionSpec) (: origin (| FreshThread ResumeThread))] :post [(: % (| str ThreadUnknown))]
   :tags {:context "codex-test" :role "program"}}
  "会話の 1 ターンを終わりまで回して閉じるため。答え = thread の id か、続きを断られた ThreadUnknown。"
  (<- started (CodexStartTurn origin spec (CodexInput :text "say hello")))
  (when (isinstance started ThreadUnknown)
    (return started))
  (assert (isinstance started TurnStarted) started)
  (<- _so-far (read-to-end started.turn TURN-WAIT-SECONDS))
  (<- closed (CodexCloseSession started.turn.thread-id "検の終わり"))
  (assert (isinstance closed SessionClosed) closed)
  started.turn.thread-id)

(defk kept-reaped-and-resumed [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % dict)] :tags {:context "codex-test" :role "program"}}
  "新しい会話の 1 ターンの後に記録を取り出して預け、rollout を外し(機体の掃除の模型)、持ち込みなしと持ち込みありで続けるため。"
  (<- spec (plain-spec tmp-path))
  (<- thread-id (one-turn spec (FreshThread)))
  (<- exported (CodexExportThread spec.home thread-id None None))
  (assert (isinstance exported ThreadExported) exported)
  (val sessions (/ tmp-path CODEX-HOME-DIR "sessions"))
  (val place (! (rollout-of sessions thread-id)))
  (val kept-bytes (.read-bytes place))
  (.unlink place)
  (<- bare (one-turn spec (ResumeThread :thread-id thread-id)))
  (<- carried (one-turn spec (ResumeThread :thread-id thread-id :carry exported.jsonl-text)))
  {"thread" thread-id "exported" exported.jsonl-text "kept" kept-bytes "place" place "bare" bare "carried" carried})


(deftest test-a-kept-rollout-brings-a-reaped-thread-back-to-its-own-place [tmp-path]
  ;; 取り出した記録は file の中身ちょうど。rollout を外した thread の続きは、持ち込みなしでは断られ(本物の codex と同じ)、持ち込めば
  ;; 元と同じ置き場へ同じ bytes が書き戻されて続けられる。
  (<- outcome (scheduled (with_handlers [(sync-time-handler) (codex-handler (CodexHost #(sys.executable STUB-PATH) :launch-timeout 30.0))]
                                        (kept-reaped-and-resumed tmp-path))))
  (assert (= (.encode (get outcome "exported") "utf-8") (get outcome "kept")) outcome)
  (assert (isinstance (get outcome "bare") ThreadUnknown) outcome)
  (assert (in "no rollout found" (. (get outcome "bare") detail)) outcome)
  (assert (= (get outcome "carried") (get outcome "thread")) outcome)
  (assert (= (.read-bytes (get outcome "place")) (get outcome "kept")) outcome))


(val META "{\"type\":\"session_meta\",\"timestamp\":\"2026-11-01T18:22:44.123Z\",\"payload\":{\"id\":\"01a19859-7df8-7461-a4c6-c37e7671330c\",\"timestamp\":\"2026-11-01T18:22:44.123Z\"}}\n")
(val THREAD "01a19859-7df8-7461-a4c6-c37e7671330c")

(deftest test-reading-a-rollout-keeps-whole-lines-and-the-grown-part [tmp-path]
  ;; 取り出しは行の終わりまで(書きかけの最後の行を入れない)。前の写しの長さからは、その後ろの行だけ。字の途中・記録より後ろの
  ;; 位置は読まない。末尾の幅だけの読みは、切り口の後の最初の行の頭から。
  (val path (/ tmp-path "rollout.jsonl"))
  (val line "{\"type\":\"turn\",\"text\":\"こんにちは\"}\n")
  (val partial "{\"type\":\"tu")
  (.write-bytes path (.encode (+ META line partial) "utf-8"))
  (assert (= (! (read-rollout path None None)) (+ META line)))
  (val first-length (len (.encode META "utf-8")))
  (assert (= (! (read-rollout path None first-length)) line))
  (assert (is (! (read-rollout path None (+ first-length (.find (.encode line "utf-8") (.encode "ん" "utf-8")) 1))) None))
  (assert (is (! (read-rollout path None 1000000)) None))
  (assert (= (! (read-rollout path (+ (len (.encode line "utf-8")) (len partial) 5) None)) line))
  (assert (is (! (read-rollout (/ tmp-path "absent.jsonl") None None)) None)))


(deftest test-a-carry-goes-back-to-the-place-its-first-line-names-and-never-over-a-present-rollout [tmp-path]
  ;; 書き戻しの置き場は 1 行目の session_meta の timestamp(UTC)と id から導く(本物の codex の名の付け方)。名の違う記録・1 行目の
  ;; 無い記録は書き戻さない。在る rollout は上書きしない。
  (assert (= (! (carry-place-of META THREAD)) (+ "2026/11/01/rollout-2026-11-01T18-22-44-" THREAD ".jsonl")))
  (assert (is (! (carry-place-of META "another-thread")) None))
  (assert (is (! (carry-place-of "{\"type\":\"turn\"}\n" THREAD)) None))
  (val sessions (/ tmp-path "sessions"))
  (assert (is (! (restore-rollout sessions THREAD META)) None))
  (val restored (/ sessions "2026" "11" "01" (+ "rollout-2026-11-01T18-22-44-" THREAD ".jsonl")))
  (assert (= (.read-text restored :encoding "utf-8") META))
  (assert (is (! (restore-rollout sessions THREAD (+ META "{\"other\":1}\n"))) None))
  (assert (= (.read-text restored :encoding "utf-8") META))
  (assert (is-not (! (restore-rollout sessions "another-thread" META)) None)))
