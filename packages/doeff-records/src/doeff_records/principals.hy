;;; 記録の service の呼び手の書き手の名 — 呼び手が X-Records-Writer の見出しで名乗った名を、そのまま書き手の名にする(純粋)。
;;;
;;; 名乗らない呼び手(見出しが無い・空)は、名の無い書き手 ANONYMOUS として通す。service は名簿の file を読まず、Authorization の見出しも読まない
;;; (#3008・利用者 2026-10-02「頼んでいない token・password・security を入れない」)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "judgment"})
(import dataclasses [dataclass])

(setv ANONYMOUS "anonymous")


(defclass [(dataclass :frozen True)] Principal []
  "呼び手(name = 書き手の名・名乗らなければ ANONYMOUS)。"
  (#^ str name))


(defk writer-of [declared]
  {:pre [(: declared (| str None))] :post [(: % Principal)]}
  "要求の書き手の名を決めるため: 呼び手が X-Records-Writer で名乗った名(空でなければ確かめずに使う)→ 無ければ ANONYMOUS。"
  (val named (if (is declared None) "" (.strip declared)))
  (Principal (if named named ANONYMOUS)))
