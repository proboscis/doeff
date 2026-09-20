;;; Semgrep fixture: doeff-agents-herdr-label-holders-must-not-be-indexed.
;;;
;;; A herdr label's workspace holders are a set with no distinguished element:
;;; workspace ids are not monotone in creation order (the counter carries from
;;; letters into digits — w3NZ -> w3N0, w3ZZ -> ... -> w303; probe 2026-08-14).
;;; Indexing the set picks an arbitrary workspace, which is how PR #587's first
;;; revision orphaned a live session and killed the wrong workspace. The line
;;; below is the banned shape and must keep firing the rule.

(defk herdr-kill-session-io [socket-path session-name]
  (setv holders (herdr-label-workspace-ids-io socket-path session-name))
  (herdr-call socket-path "workspace.close" {"workspace_id" (get holders 0)}))
;;;
;;; ⚠ 語彙（deff / defk）はこの検体の争点ではない — rule の pattern は holder 集合の添字
;;; の regex で、定義の綴りには依存しない。実 code には deff のまま残る関数も在るが、
;;; この検体は ADR-DOE-HY-004 の台帳（コメント・文字列の中の字面も数える）に載るので
;;; defk で書く。行番号は test_vm_failfast_semgrep_rules.py が pin しているので、
;;; 註は必ず file の末尾に置く（deff と defk は同じ 4 文字なので行はずれない）。
