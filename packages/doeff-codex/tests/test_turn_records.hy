;; ターンを名乗る記録の集まり(lines.TurnRecord)の検(盲検 A)。
;;
;; 守る事: 「どの記録がターンを名乗るか」は記録の型を持つ lines が 1 か所(TurnRecord)で名乗り、handler はそれで記録をターンの列へ
;; 振り分ける(handler は記録の class を列挙しない)。ターンの id(turn-id)を必ず持つ記録を足して TurnRecord に入れ忘れると、handler は
;; その記録を「走っているターン(無ければターンの外)」へ落とし、名乗ったターンと違う列に積む — この検が赤にする。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <- var])
(import dataclasses [fields MISSING])
(import typing [get-args])
(import doeff_codex [CodexLine]
        doeff_codex._lines [TurnRecord])


(defk names-a-turn [#^ type record-type]
  {:pre [(: record-type type)] :post [(: % bool)] :tags {:context "codex-test" :role "foundation"}}
  "記録の型が、ターンの id を必ず持つ(既定値の無い欄 turn_id を持つ)かを答えるため。"
  (any (gfor field (fields record-type)
             (and (= field.name "turn_id") (is field.default MISSING) (is field.default-factory MISSING)))))


(deftest test-every-record-that-must-name-a-turn-is-a-turn-record
  (val records (get-args CodexLine))
  (var naming #())
  (for [record records]
    (<- names (names-a-turn record))
    (when names (:= naming (+ naming #(record)))))
  (val declared (frozenset (get-args TurnRecord)))
  (assert (<= declared (frozenset records)) (.format "CodexLine に無い記録が TurnRecord に在る: {}" (- declared (frozenset records))))
  (assert (= (frozenset naming) declared)
          (.format "ターンの id を必ず持つのに TurnRecord に無い: {} / TurnRecord に在るのにターンの id を必ず持たない: {}"
                   (sorted (gfor kind (- (frozenset naming) declared) kind.__name__))
                   (sorted (gfor kind (- declared (frozenset naming)) kind.__name__)))))
