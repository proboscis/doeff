;;; semgrep hit fixture: doeff-agents-substrate-does-not-import-acp
;;; (card acp:kanban-issue:ki-554e364641e8 / 法 ACP 575b1e — 器の kind module は判断を持たない。
;;;  acp を import すると digest の式や判定が第 2 の家へ写る)。

;; BAD: 器が同一性の式を acp から引いて自分で比べる
(import doeff_agents.sessionhost.acp.judgment [memory-sha256-of-text])

;; BAD: 器が判定の型を acp から引く(判断をこの層へ持ち込む入口)
(import doeff_agents.sessionhost.acp.effects [MemoryHold])

;; BAD: 綴りだけの参照でも同じ(import の形を変えても当たる)
(setv verdict (doeff_agents.sessionhost.acp.judgment.memory-hydrate-verdict head prior local))
