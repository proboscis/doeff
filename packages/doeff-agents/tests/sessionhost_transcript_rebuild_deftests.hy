;;; 会話の記録から Claude Code の transcript を組み直して `--resume` で続ける腕の焦点の検
;;; (card acp:kanban-issue:ki-c3aace97d825)。
;;;
;;; agora-redesign #668(削除 5-1): transcript の組み立てと腕の選び(acp/judgment.hy)は agentd と一緒に退いた。
;;; ここに残るのは、組み直した transcript を起こす側の起こし方(policy.launch-conversation-plan-of)の検だけ。
;;; HTTP も subprocess も無い(純関数だけ)。

(require doeff-hy.macros [deftest])

(import doeff [run])
(import doeff_agents.sessionhost.policy [launch-conversation-plan-of])


(deftest test-an-adopted-transcript-is-resumed-so-the-cold-compaction-runs-first
  ;; operator 決定 2026-09-23: account / profile / model / 機体 の変更が避けられない手番では、最初の model の
  ;; 呼び出しの**前に**必ず圧縮を走らせる(cache はどのみち書き直されるので、そこで文脈を小さくするのが最も安い)。
  ;; 組み直しの腕はまさにその手番なので、起こし方が "resume" でなければならない —— headless.hy はこの値ちょうどを
  ;; 見て起動前の `/compact fast-jev-if-cold` を撃つ(headless-cold-compaction-run の門)。
  ;; さらに、組み直しは**新しい会話の id**を鋳造する(会話 + 記録の頭から導く)ので、圧縮の plugin から見て
  ;; その session の温かい記録は構造的に存在しない = 必ず「冷えた」と判定される。
  (setv minted {"session_id" "11111111-2222-3333-4444-555555555555"})
  (setv plan (run (launch-conversation-plan-of None minted True "claude" False)))
  (assert (= (get plan "mode") "resume") plan)
  (assert (= (get plan "conversation") minted) plan)
  (assert (= (get plan "row_conversation") minted) plan)
  ;; 継いでいない手番は今日どおり(新しい会話 = --session-id・起動前の圧縮は撃たない — 温かい cache が無いので不要)。
  (setv fresh (run (launch-conversation-plan-of None minted False "claude" False)))
  (assert (= (get fresh "mode") None) fresh)
  (assert (= (get fresh "conversation") minted) fresh)
  ;; 蘇生の腕(session.resume / fork)は今日どおり親会話。
  (setv parent {"session_id" "99999999-2222-3333-4444-555555555555"})
  (setv resumed (run (launch-conversation-plan-of {"mode" "resume" "conversation" parent} minted True "claude" False)))
  (assert (= (get resumed "conversation") parent) resumed)
  (setv forked (run (launch-conversation-plan-of {"mode" "fork" "conversation" parent} minted True "claude" False)))
  (assert (= (get forked "row_conversation") None) forked))
