;;; Executable ADR(決定): 想定内の「不在」と「失敗」を、意味で分けた 2 つの effect — Absent(Maybe の側)と Raise(Result の側)— にする。
;;; 出す側は最初から Absent / Raise を出し(Koka と同じ素直な書き方)、(<- x (危ない呼び)) の x は成功の値 T だけになる。答えを
;;; Maybe / Result の値で受けたい所だけ、境目の handler(maybe・result・on-raise・absent-as)で畳む。effect に答える handler は値で答え、
;;; 不在と失敗への変換は呼び手の側の <- が defeffect の宣言に従って行う。関数が出す effect は手で書かずに推論する。
;;;
;;; 出自 = operator との議論 2026-09-27 夜(f143ee92 の席・逐語は :problem の fact)。同じ夜に operator が案を採った
;;; (逐語 "lets go with it" — :problem の fact)。状態は決定。実装は段階 1〜5(R16)で進め、この版で段階 1 と段階 2 を実装した:
;;;   段階 1 = Absent / Raise の effect・境目の handler・defhandler の終わる節 (finish) と節の終わり方の検め(R15)
;;;   段階 2 = defeffect の答えの宣言(:absent / :failure / :value)と、<- / ! の変換(:absent・Result / Option の値を開く形を含む)
;;; 段階 3(doeff-records の effect に宣言を付ける)と段階 5(agora の呼び手の書き換え)は同じ版で effect ごとに切り替える
;;; — 手順は R14。
;;;
;;; 改訂の経緯(2026-09-27・書いている間): 初めの案は「成功だけを見る」範囲を字面のスコープ(macro success-only)で宣言する形だった。
;;; operator の逐語 7(success-only は要らないのでは)を受けて、f143ee92 の席が success-only を消す決定をした(戻せる決定 — R3)。
;;; 同じ行の意味が呼び手で変わらない、という success-only の狙いは、出す側が最初から Absent / Raise を出す形で満たされる。
;;; 2026-09-28: 提案から決定へ。operator の決定の逐語・段階の計画(R16)・4 種類の失敗の割り当て(R17)・handler の節の終わり方(R15 —
;;; operator の懸念「再開の書き忘れ」から)を足し、段階 1・2 の実装で決めた戻せる決定を :context の最後の interpretation に記した。
;;;
;;; 置き場の判断: effect の語彙(Absent・Raise)と境目の handler は Try と同じ doeff-core-effects に置く物なので、ADR-DOE-CORE-EFFECTS の
;;; 3 つ目にした。<- の変換と defeffect の宣言と defhandler の終わる節は doeff-hy、effect の推論は doeff-effect-analyzer、規則は
;;; doeff-linter の仕事で、:scope に並べる。
;;;
;;; 戻し方: 段階 2 の commit を revert すると <- は今までの (yield e) に戻り、defeffect の :absent / :failure / :value は知らない鍵になる。
;;; 段階 1 の commit を revert すると Absent / Raise・境目の handler・finish が消え、defhandler の節の検めは提案の前の形に戻る。
;;; 宣言を持つ effect はまだ無い(段階 3 の前)ので、どちらの revert も既存の呼び手の振る舞いを変えない。
;;;
;;; ---------------------------------------------------------------------------
;;; 書き方の例(operator の逐語 5 への答え — 段階 3 の後の形。revise-input は agora-controllers の関数)
;;; ---------------------------------------------------------------------------
;;;
;;; Before — agora-controllers の本線 eb2abb27 の controllers/protocol/conversation_input.hy:73-91(そのまま):
;;;
;;;   (defk revise-input [revise]
;;;     {:pre [(: revise InputRevise)] :post [(: % (| WriteLanded WriteConflict WriteUnreachable))]
;;;      :effects [ReadRow PutRow ReadPlacedInput] :tags {:context "conversation-input" :role "protocol"}}
;;;     "直しの書きを写すため: …"
;;;     (<- body (| TypedRow Missing Unreachable) (read-typed PLACED-INPUT-TYPE #(revise.id)))
;;;     (match body
;;;       (Unreachable) (return (WriteUnreachable :detail body.detail))
;;;       (Missing) (return (WriteConflict :detail (+ revise.id " の本文の行が無い")))
;;;       _ None)
;;;     (<- placed (| PlacedInput None) (ReadPlacedInput :agent body.value.agent :ref revise.id))
;;;     (when (or (!= body.version revise.version) (is placed None) (!= placed.state LedgerState.PENDING))
;;;       (return (WriteConflict :detail (+ revise.id " は読んだ後に変わった"))))
;;;     (<- written (| BodyRecorded BodyUnreachable BodyConflict) (revise-placed-input revise.id revise.text revise.edit-id))
;;;     (match written
;;;       (BodyRecorded) (WriteLanded)
;;;       (BodyConflict) (WriteConflict :detail written.detail)
;;;       (BodyUnreachable) (WriteUnreachable :detail written.detail)))
;;;
;;; After — 段階 1・2 で実装した形(成功の道だけを書き、失敗は 1 か所の on-raise で業務の答えの型へ畳む):
;;;
;;;   (defk revise-input [revise]
;;;     {:pre [(: revise InputRevise)] :post [(: % (| WriteLanded WriteConflict WriteUnreachable))]
;;;      :tags {:context "conversation-input" :role "protocol"}}
;;;     "直しの書きを写すため: …"
;;;     (<- answer
;;;         (on-raise
;;;           (do!
;;;             (<- body (read-typed PLACED-INPUT-TYPE #(revise.id))
;;;                 :absent (Conflict (+ revise.id " の本文の行が無い")))
;;;             (<- placed (ReadPlacedInput :agent body.value.agent :ref revise.id)
;;;                 :absent (Conflict (+ revise.id " は読んだ後に変わった")))
;;;             (unless (and (= body.version revise.version) (= placed.state LedgerState.PENDING))
;;;               (<- (Raise (Conflict (+ revise.id " は読んだ後に変わった")))))
;;;             (<- (revise-placed-input revise.id revise.text revise.edit-id))
;;;             (WriteLanded))
;;;           (Conflict d)        (WriteConflict :detail d)
;;;           (BodyConflict d)    (WriteConflict :detail d)
;;;           (Unreachable d)     (WriteUnreachable :detail d)
;;;           (BodyUnreachable d) (WriteUnreachable :detail d)))
;;;     answer)
;;;
;;;   - 外から見た型は Before と同じ: revise-input : InputRevise → Program[WriteLanded | WriteConflict | WriteUnreachable] ! {ReadRow, PutRow,
;;;     ReadPlacedInput}(Raise は on-raise が全部畳むので残らない)。手書きの :effects は推論(R13)に置き換わって消える。
;;;   - revise-placed-input は版の負けと届かないを BodyConflict・BodyUnreachable で出すので、on-raise はその 2 つも受ける。受けないと
;;;     Raise[BodyConflict | BodyUnreachable] が外へ漏れ、外から見た型が変わる(推論がそれを見せる)。
;;;   - Conflict はこの例のための失敗の型(説明の文 d を持つ)で、doeff-records の Conflict(current)とは別。
;;;   - :absent の受け手は on-raise の内側(束ねる行)に置かれるので、それが出す Raise は on-raise に届く(R4 の落とし穴には当たらない)。
;;;   - on-raise の本文は Program の式 1 つ(複数の行は do! で包む)。maybe・result は関数で、(maybe (ReadRow …)) のように宣言を持つ
;;;     effect をそのまま渡してもよい(開いてから畳む)。
;;;
;;; 業務の core では境目を書かない例 — notify-landed(本線には無い名。近い実物は controllers/core/land_notice.hy:53-70 の
;;; post-notice・notify-outcome で、今は PostMessage の答え Accepted / Rejected / Unavailable を毎回 match で数えに写す):
;;;
;;;   (defk notify-landed [outcome recipient]
;;;     {:pre [(: outcome LandOutcome) (: recipient str)] :post [(: % Accepted)]
;;;      :tags {:context "land-notice" :role "program"}}
;;;     "便の結末 1 つを宛先 1 つへ置くため。断りと届かないは Raise のまま外へ出し、扱い方は組み立ての handler に任せる。"
;;;     (<- request (notice-request outcome recipient))
;;;     (<- (PostMessage request)))
;;;
;;;   - 推論した型: notify-landed : LandOutcome × str → Program[Accepted] ! {Raise[Rejected | Unavailable], PostMessage}
;;;     (PostMessage の宣言で Accepted を成功・Rejected と Unavailable を失敗とした場合)。
;;;   - 呼び手は 1 件ずつ Traverse で回し、1 件ごとの失敗の扱いは Traverse の handler(組み立てで選ぶ)が決める。本番 = Rejected は
;;;     飛ばして数えに載せる・Unavailable はその周期を終えて同じ位置から読み直す。模擬とテスト = 最初の Raise で止め、テストが理由を見る。
;;;     1 件を飛ばすには 1 件ごとのスコープが要り、それを持つのが Traverse の handler(今は Fail を 1 件ごとに受ける。Raise を受けるのは
;;;     実装の時に足す)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk deff defeffect <- val var do! on-raise absent-as])
(require doeff-hy.handle [defhandler])
(require doeff-hy.record [defrecord])
(import contextlib [suppress])
(import dataclasses [dataclass fields])
(import hy)
(import pytest)
(import typing [Never get-args])
(import pathlib [Path])
(import doeff [run with-handlers Ok Err Some Nothing Pass Try EffectBase DoExpr K])
(import doeff.result [Maybe])
(import doeff_vm [UnhandledEffect])
(import doeff-core-effects.handlers [try-handler])
(import doeff-core-effects.effects [Absent Raise Resumption resumption-of])
(import doeff-core-effects.effects [Raise :as ProbeAliasedRaise])  ; R15 の反例: 名前では Raise と分からない別名
(import doeff-core-effects.outcomes [maybe result open-bind RaiseCase Outcomes])
(import doeff-records.values [ReadRowAnswer Row Missing Unreachable])
(import doeff-traverse.effects [Fail])
(import doeff-traverse.handlers [normalize-to-none])
(import doeff-hy.handle [_check-clause-terminates])
(import doeff-hy.declarations [EFFECT-KEYS])
(import doeff-hy.clause-endings [ClauseEndingError])


(val REPO-ROOT (. (Path __file__) parent parent parent))


;; ---------------------------------------------------------------------------
;; 生きた probe — 今の VM と macro で書ける形・書けない形の実演(R1・R2・R4・R5・R13 が立つ土台の事実)。
;; ---------------------------------------------------------------------------

(defeffect ProbeAbsent
  "想定内の不在の見本(提案の時点の形 — 普通の effect。R1 の Absent は doeff_core_effects.effects.Absent)。"
  {:fields [(: why str)]
   :answer Never
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(defeffect ProbeRead
  "handler が値で答える読みの見本(R4 — handler の節の中で effect を出すと、呼び手のスコープを飛び越えることを見せるため)。"
  {:answer str
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(defk probe-ending [value]
  {:pre [(: value (| str (type Nothing)))] :post [(: % (| str (type Nothing)))]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "受け手が再開せずにスコープを終える時の値を返す Program — 素の handler 関数がその値でスコープを終えるため。"
  value)

(deff probe-maybe-handler [effect k]  ; defk にできない: defk は引数 (effect k) を handler と読んで断る — VM が素の handler 関数を受けることを見せる見本(defhandler では (finish 値) で書ける — R15)
  {:pre [(: effect EffectBase) (: k K)] :post [(: % DoExpr)]
   :tags {:context "doeff-core-effects-adr" :role "foundation"}}
  "R2 の maybe の受け手の見本: ProbeAbsent を受けたら再開せず Nothing でスコープを終える(続きは捨てる)。ほかは外へ渡す。"
  (if (isinstance effect ProbeAbsent)
      (probe-ending Nothing)
      (Pass effect k)))

(deff probe-outer-handler [effect k]  ; defk にできない: probe-maybe-handler と同じ(VM が素の handler 関数を受けることを見せる見本)
  {:pre [(: effect EffectBase) (: k K)] :post [(: % DoExpr)]
   :tags {:context "doeff-core-effects-adr" :role "foundation"}}
  "土台の handler より外側に置く受け手の見本: ProbeAbsent を受けたら印の文でスコープを終える — どの受け手が受けたかを見分けるため。"
  (if (isinstance effect ProbeAbsent)
      (probe-ending "外側が受けた")
      (Pass effect k)))

(defhandler probe-read-handler
  "R4 の落とし穴の見本: ProbeRead に答える handler が、節の中で ProbeAbsent を出す(してはいけない形)。"
  (ProbeRead []
    (<- (ProbeAbsent "handler の節の中で出した"))
    (resume "来ない")))

(defk probe-reads-then-continues [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "ProbeAbsent を出し、その後ろで seen に印を足す — 受け手が続きを捨てれば印は残らない。"
  (<- (ProbeAbsent "行が無い"))
  (.append seen "続いた")
  1)

(defk probe-reads-a-row []
  {:pre [] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "ProbeRead を 1 つ読んで返す本文(R4 の見本の呼び手)。"
  (<- row (ProbeRead))
  row)

(defk probe-declared-effects []
  {:pre [] :post [(: % int)]
   :effects [ProbeAbsent]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "頭の辞書の :effects を手で書いた見本(R13 — 書けば __doeff_effects__ に残る)。"
  (<- (ProbeAbsent "宣言つき"))
  1)

(defk probe-boom []
  {:pre [] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Python の例外を上げる Program(R11 — 例外は実装の誤りの見本)。"
  (raise (ValueError "boom")))

(defk probe-one []
  {:pre [] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "成功する Program(Try が Ok に畳む見本)。"
  1)

(defk probe-try [program]
  {:pre [(: program DoExpr)] :post [(: % (| Ok Err))]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "program を Try で包み、例外を Result の値に畳んだ答えを返す。"
  (<- answer (Try program))
  answer)

(defk probe-fails-then-continues []
  {:pre [] :post [(: % tuple)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "doeff-traverse の Fail を出し、再開された値で続ける。"
  (<- substitute (Fail (ValueError "壊れた入力")))
  #("続いた" substitute))


;; ---------------------------------------------------------------------------
;; 段階 1・2 の probe — 実装した Absent / Raise・境目の handler・<- の変換・handler の節の終わり方
;; ---------------------------------------------------------------------------

(defrecord ProbeFound
  #^ str value)

(defrecord ProbeGone
  #^ str key)

(defrecord ProbeDown
  #^ str detail)

(defrecord ProbeConflict
  #^ str detail)

(defeffect ProbeDeclaredRead
  "答えを 成功(ProbeFound)・不在(ProbeGone)・失敗(ProbeDown)に分けて宣言した読み(R5)。"
  {:fields [(: key str)]
   :answer (| ProbeFound ProbeGone ProbeDown)
   :absent [ProbeGone]
   :failure [ProbeDown]
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(defeffect ProbePlainRead
  "宣言の無い読み(答えの型は同じ union)— <- は答えをそのまま束ねる。"
  {:fields [(: key str)]
   :answer (| ProbeFound ProbeGone ProbeDown)
   :tags {:context "doeff-core-effects-adr" :role "intent"}})

(val PROBE-TABLE {"a" (ProbeFound :value "A") "down" (ProbeDown :detail "網が落ちた")})

(defhandler probe-rows
  "PROBE-TABLE から読みに値で答える土台の handler(不在も失敗も答えの値 — R4)。"
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeDeclaredRead [key] (resume (.get PROBE-TABLE key (ProbeGone :key key))))
  (ProbePlainRead [key] (resume (.get PROBE-TABLE key (ProbeGone :key key)))))

(defk probe-read-value [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "宣言を持つ読みを 1 つ束ねる — x は成功の型だけ(不在と失敗は Absent / Raise として逃げる)。"
  (<- found (ProbeDeclaredRead key))
  found.value)

(defk probe-read-plain [key]
  {:pre [(: key str)] :post [(: % (| ProbeFound ProbeGone ProbeDown))]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "宣言の無い読みを 1 つ束ねる — 答えをそのまま返す。"
  (<- answer (ProbePlainRead key))
  answer)

(defk probe-read-or-conflict [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "(e) この束ねの中で出た不在(奥の defk の中の物も)を ProbeConflict の失敗として投げる(<- の :absent)。"
  (<- value (probe-read-value key) :absent (ProbeConflict :detail (+ key " の行が無い")))
  value)

(defk probe-absent-then-note [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Absent を出し、その後ろで seen に印を足す — 境目の handler が続きを捨てれば印は残らない。"
  (<- (Absent "行が無い"))
  (.append seen "続いた")
  1)

(defk probe-raise-then-note [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "Raise を出し、その後ろで seen に印を足す。"
  (<- (Raise (ProbeDown :detail "届かない")))
  (.append seen "続いた")
  1)

(defk probe-raises-python []
  {:pre [] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "(d) Python の例外を上げる本文を on-raise で包む — 例外は受けない。"
  (<- answer (on-raise (probe-boom) (ProbeConflict :detail d) d))
  (str answer))

(defhandler probe-raising-read-handler
  "(b) R4 の落とし穴の見本(してはいけない形): ProbeRead に答える handler が、節の中で Raise を出す。"
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeRead []
    (<- (Raise (ProbeConflict :detail "handler の節の中で出した")))
    (resume "来ない")))

(defk probe-direct-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "absent-as の字面の中の <- の不在は既定値で再開し、続きが走る(R8)。"
  (<- value (absent-as (ProbeFound :value "既定") (do! (<- found (ProbeDeclaredRead key)) (+ "続いた: " found.value))))
  value)

(defk probe-deep-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "呼んだ defk の奥の不在は再開せず、既定値でスコープを終える(R8)。"
  (<- value (absent-as "既定" (do! (<- got (probe-read-value key)) (+ "続いた: " got))))
  value)

(defk probe-written-absent-default []
  {:pre [] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "program"}}
  "直に書いた (<- (Absent …)) は再開せず、既定値でスコープを終える(R8)。"
  (<- value (absent-as "既定" (do! (<- (Absent "書いた不在")) "続いた")))
  value)

(defhandler probe-deadline [limit]
  "普通の effect を意図して打ち切る節の見本(R15 — :finish-reason の理由つき)。"
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに打ち切る閾値を変えるため(Ask で読むと検の組み立てが増える)
  (ProbeRead []
    :finish-reason "閾値を越えたら処理全体を止める(時間切れの見本)"
    (if (> limit 0) (resume "読めた") (finish "打ち切った"))))

(defhandler probe-aliased-raise-resumer
  "R15 の反例(してはいけない形): 別名で import した Raise を resume する節 — 名前では分からないので、初めて本文に被せた時に断る。"
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeAliasedRaise [reason] (resume 0)))

(defhandler probe-swallowing-handler
  "R15 の反例(してはいけない形): 例外を黙らせる with の中の resume — 展開の時は通るが、resume せずに抜けるので実行の時に誤りにする。"
  {:tags {:context "doeff-core-effects-adr" :role "foundation"}}
  (ProbeRead [] (with [(suppress ValueError)] (resume (int "数でない")))))

(deff probe-expansion-refusal [source]
  {:pre [(: source str)] :post [(: % str)]
   :tags {:context "doeff-core-effects-adr" :role "judgment"}}
  "source を展開した時の誤りの文(通れば空の文字列)— 展開の時に断る macro の規則を検で見るため。"
  (try
    (hy.eval (hy.read-many source))
    (return "")
    (except [e hy.errors.HyMacroExpansionError]
      (return (str e)))))


(defadr ADR-DOE-CORE-EFFECTS-003
  :title "想定内の『不在』と『失敗』を、意味で分けた 2 つの effect — Absent(Maybe の側・中身を持たず説明の文だけ)と Raise(Result の側・理由を持つ)— にする。出す側は最初から Absent / Raise を出し、(<- x (危ない呼び)) の x は成功の値 T だけ。答えを Maybe / Result の値で受けたい所だけ境目の handler(maybe・result・on-raise・absent-as)で畳み、入れ子の順が組み合わせの型を決める。effect に答える handler は値で答え、不在と失敗への変換は呼び手の側の <- が defeffect の宣言に従って行う。関数が出す effect は手で書かずに doeff-effect-analyzer で推論し、関数の型を『戻り値 + 残りの effect の集合』として見せる"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_core_effects_003_absent_and_raise.hy"
          "packages/doeff-core-effects/doeff_core_effects/effects.py"
          "packages/doeff-core-effects/doeff_core_effects/outcomes.py"
          "packages/doeff-core-effects/doeff_core_effects/handlers.py"
          "packages/doeff-core-effects/tests/test_outcomes.hy"
          "packages/doeff-core-effects/tests/test_outcome_binds.hy"
          "packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/declarations.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "packages/doeff-hy/src/doeff_hy/outcome_forms.hy"
          "packages/doeff-hy/src/doeff_hy/clause_endings.hy"
          "packages/doeff-hy/src/doeff_hy/binding_forms.py"
          "packages/doeff-effect-analyzer"
          "packages/doeff-records"
          "packages/doeff-traverse"
          "packages/doeff-linter"]
  :problem
    [(fact
       "operator の問い 2026-09-27 夜(逐語 1〜3): \"I want to also discuss about the use of Result/Maybe monads in algebraic effects, where the code mainly cares about the success cases. I am feeling that whether the caller want Result/Maybe instance or just the internal value depends on the context so we need to have a scoped way of saying 'I only care about success cases in this scope so i directly touch all those results and maybe values' etc\" / \"hmm but usually we combine both Optional and Result, and my other concern is that if we should yield Fail effect or Nothing effect? (what's the equivalent of Fail for Maybe context in algebraic effects?)\" / \"interesting, so we yield either Absent or Fail and decide how to recieve them with scoped handler?\""
       :evidence "Claude Code の会話(2026-09-27 夜・f143ee92 の席)— coordinator 経由")
     (fact
       "operator の問い 2026-09-27 夜(逐語 4・R13 の出自): \"right... the only concern is that currently defk doesnt say it yields Fail or Absent,,, we only have return type of Option or Result. we need to be able to tell if a func yields Absent or Fail, right? hmm. we could make the defk have :effects field but that dynamically changes by what it internally calls so\""
       :evidence "Claude Code の会話(2026-09-27 夜・f143ee92 の席)— coordinator 経由")
     (fact
       "operator の問い 2026-09-27 夜(逐語 5〜6・書き方の例と型の出自): \"then how will this code look like if we introduce such Fail and Absent\" / \"hmm this look very similar to try-catch but customizable\" / \"so it sounds like we can always treat such Fail/Absent etc in our style, even in merged style,,, what's the return type of succes-only? Result[T] or T that yields Absent/Fail?\""
       :evidence "Claude Code の会話(2026-09-27 夜・f143ee92 の席)— coordinator 経由")
     (fact
       "operator の問い 2026-09-27 夜(逐語 7〜8・success-only を消す決定と R9 の出自): \"hmm if success-only only cares success case, we dont even need to have success-only.. do we? we can just do (<- x (dangerous_call)) (<- y (maybe_1))\" / \"so i feel like the only case we want to use some scope, is when we want to do something in failure case... right?\""
       :evidence "Claude Code の会話(2026-09-27 夜・f143ee92 の席)— coordinator 経由")
     (fact
       "operator の決定 2026-09-27 夜(逐語 9・この ADR を決定にした出自): \"lets go with it, that means we need to update the doeff-record to yield such effects?\"。議論の要約: 繰り返し出る 4 種類の失敗(Unreachable・Missing・Refused・Conflict)のうち、Unreachable と Refused は Raise、Missing は Absent の effect にし、Conflict は値のまま。effect は『積み重なるもう 1 本のデータの口』(逐語 \"very interesting,, so effects are like another channel of data passing that is stackable\")。4 種類はコードの本文では effect、外の世界との境目(handler の答え・記録)と『何かしたい』境目(maybe・result・on-raise)ではデータ(逐語 \"it means that 4 kinds become effects instead of returned data type\")。HTTP の状態コードも翻訳の層の 1 か所で 4 種類に写す(逐語 \"so we could for example have such failure case effect for Http specific codes\")"
       :evidence "Claude Code の会話(2026-09-27 夜・f143ee92 の席)— coordinator 経由(agora-redesign #836)")
     (fact
       "operator の懸念 2026-09-27 夜(逐語 10・R15 の出自): \"about the terminal case of a handler... we need to be careful to not accidentally forget to resume right?\" — coordinator が条件にした: 終わる節は明示の操作(finish)でだけ書ける・節のすべての道が resume・finish・transfer のどれかで終わらなければ展開の時に誤り・effect ごとの再開の扱いと照らす・普通の effect の打ち切りは理由の註つきだけ・実行の時に黙って止まらない"
       :evidence "coordinator の追加の依頼(2026-09-27 夜・agora-redesign #836)")
     (fact
       "doeff.result は Result と Maybe の値を持つ: Ok / Err は Rust の doeff_vm から再び出したもの(Ok は value、Err は error と captured_traceback を持つ)。Some は value を持つ不変の値、Nothing は偽と評価される 1 つだけの値で、Maybe = Some | Nothing。doeff の最上位からも同じ名で出る。Ok / Err は値で比べられない(Ok(1) == Ok(1) は偽 — 2026-09-28 に確かめた)。"
       :evidence "doeff/result.py:19(Ok / Err)・:22-54(Some)・:57-85(Nothing — :78-79 の __bool__ は False)・:94(Maybe)・doeff/__init__.py:52-56")
     (fact
       "Try(program) は、program の中で上がった Python の例外を Err に、成功を Ok に畳む effect で、try_handler が受ける。try_handler は内側の handler を付け直して program を走らせ、except Exception で Err(e) を返す — 例外を値に写す口であって、値として返った答えの中の失敗(Unreachable など)は見ない。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/effects.py(Try)・packages/doeff-core-effects/doeff_core_effects/handlers.py(_try_handler — except Exception → Err)・doeff/__init__.py:68")
     (fact
       "doeff-records の effect は失敗を値で返す決まりで、README の表の列『失敗の答え(値で返す)』に並ぶ。例外で上がるのは組み立ての誤りと実装の誤りだけ。ReadRow の答えは Row | Missing | Unreachable — 成功(Row)・想定内の不在(Missing = 行が無い)・失敗(Unreachable = 置き場に届かない)が 1 つの union に並び、どれが成功でどれが失敗かは型に書かれていない。表ごとの型の層の read-typed も Missing と Unreachable をそのまま呼び手へ返す。"
       :evidence "packages/doeff-records/README.md:11-19・:39・packages/doeff-records/src/doeff_records/values.hy:279-280(Missing)・:371-373(Unreachable)・:387(ReadRowAnswer)・effects.hy:39-45(ReadRow)・typed.hy:114-118(read-typed)")
     (fact
       "成功の道だけを書きたい呼び手は、答えを受けるたびに不在と失敗を手で分けている: agora-controllers の本線 eb2abb27 で、isinstance による Missing / Unreachable の判定が 43 file・74 行ある。revise-input(controllers/protocol/conversation_input.hy:73-91)は 19 行のうち 2 つの match と 1 つの when が不在と失敗の分岐で、:effects [ReadRow PutRow ReadPlacedInput] を手で書いている(:75)。"
       :evidence "agora-controllers で git grep -P 'isinstance \\S+ (#?\\()?[^)]*(Missing|Unreachable)' origin/main -- '*.hy'(2026-09-27)・git show origin/main:controllers/protocol/conversation_input.hy")
     (fact
       "revise-input が呼ぶ revise-placed-input の答えは BodyRecorded | BodyUnreachable | BodyConflict(どれも detail の文を持つ)、ReadPlacedInput の答えは PlacedInput か None(行が無い)。land_notice の post-notice は PostMessage の答え Accepted / Rejected / Unavailable を match で数えに写す。notify-landed という関数は本線にも履歴にも無い(書き方の例の名)。"
       :evidence "agora-controllers origin/main eb2abb27: controllers/messaging/placed_input.hy:84-101・:167-168・controllers/scheduling/input_port.hy:130-131・controllers/core/land_notice.hy:53-70・git log --all -S 'notify-landed'(当たり 0)")
     (fact
       "doeff には既に Fail という名の effect がある(doeff-traverse)。意味は『失敗の知らせ』で、handler が続きを代わりの値で再開できる — normalize_to_none は Fail を None で再開し、fail_handler は例外として投げ直す。try_call は Python の関数の例外を Fail にする。Traverse は 1 件ごとの失敗の扱いを handler に任せ、sequential は Traverse の中の未処理の Fail をその 1 件の失敗にする。"
       :evidence "packages/doeff-traverse/doeff_traverse/effects.py:20-38(Fail)・:40-51(Traverse — Handler decides … error strategy per item)・handlers.py:18-27(sequential)・:502-517(fail_handler)・:520-533(normalize_to_none)・helpers.py:9-22(try_call)")
     (fact
       "提案の時点(2026-09-27)の defeffect の頭の辞書が受けるキーは :fields・:answer・:tags・:pre で、答えは :answer の 1 つの型(union でよい)として __doeff_answer__ に残るだけだった — union のどの型が成功・不在・失敗かを宣言する場所は無かった(段階 2 で :absent / :failure / :value を足した — R5)。doeff-records の effect は defeffect でなく素の defclass で書かれている。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy(EFFECT-KEYS・defeffect-form)・packages/doeff-records/src/doeff_records/effects.hy:39")
     (fact
       "handler の節の中で出した effect は、その handler より外側の handler へ行き、本文とその handler の間に置いた受け手を飛び越える — この ADR のテスト effect-performed-in-a-handler-skips-the-callers-scope(提案の時点の probe)と raise-in-a-handler-clause-skips-the-bodys-result(実装した Raise と result)で確かめた。"
       :evidence "この ADR の probe-read-handler・probe-raising-read-handler とテスト")
     (fact
       "提案の時点(2026-09-27)の defhandler の節は、すべての分かれ道で resume / transfer / reperform / raise のどれかに届かないと展開の時に SyntaxError で断られ、続きを捨てて値で終える節は書けなかった(段階 1 で (finish 値) を足した — R15)。defk は引数の名が (effect k) の形だと handler と読んで断る。VM は、再開しないで値を返す handler を許し、その値でスコープを終える(続きは捨てられる)。再開せずに None を返して抜けた handler でも、VM は黙ってスコープを None で終える(2026-09-28 に確かめた — R15 の実行の時の検めの理由)。"
       :evidence "packages/doeff-hy/src/doeff_hy/handle.hy(_terminates・_check-clause-terminates)・packages/doeff-hy/src/doeff_hy/macros.hy(_reject-handler-signature)・この ADR のテスト handler-without-resume-ends-the-scope と defhandler-clause-must-end-explicitly・uv run python で @do の handler が return None した時の run の答え(None)")
     (fact
       "defk・deff・defp・defhandler の頭の辞書は既に :effects(その定義が出す effect の型の名の list・任意)を受け、__doeff_effects__ に残す(書かなければ None)。この宣言を読んで推論と照らす道具は無い(読むのは doeff-hy のテストだけ)。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy:1-13・:26(CONTRACT-KEYS)・:59-70(effects-form)・git grep '__doeff_effects__'(当たりは declarations.hy と packages/doeff-hy/tests/test_declarations.py だけ)")
     (fact
       "doeff-effect-analyzer の Python の入口は、Program の関数が出す effect を、呼び先を推移的にたどって集める。Hy は macro を展開してから読み、defhandler の節(isinstance の分かれ道)から handler が受ける effect を読み、追えない物は unresolved と報告して黙って落とさない。引数で渡された Program(Spawn の子など)は carried として別に報告し、畳むかどうかは呼び手が決める。module の cache は process の中だけ。Rust の Hy の読み(hy_analyzer.rs の extract_effect_from_bind)は <- の形を 2〜4 要素でだけ読み、末尾の :absent <失敗> を知らない(段階 4 で直す)。"
       :evidence "packages/doeff-effect-analyzer/README.md:7-58・python/doeff_effect_analyzer/program_effects.py:159-180(_MODULE_CACHE)・:590(analyze_program)・src/hy_analyzer.rs:418-438")
     (fact
       "ADR-DOE-CLUSTER-001 は、job の runner が handler を 1 つも足さないこと(R2)と、記録と再生の handler を Program の中の翻訳の handler と土台の handler の間に置き、境目の答えを書き留めること(R5・R5b)を決めている。"
       :evidence "docs/adr/defadr_doeff_cluster_001_job_accepts_a_program.hy:102・:108-109")
     (fact
       "code-quality の方針は、未知の値を空文字・成功・既定の状態へ黙って変換しないことを求める。"
       :evidence "~/repos/code-quality/docs/policy.md:24-25")
     (fact
       "段階 1・2 の実装の時点(2026-09-28)の実測: 今の VM は Ok / Err / Some / Nothing を yield すると誤り(expected DoExpr or EffectBase)にする — <- が値を開く形は、今まで誤りだった道だけに効く。<- の展開に足した open-bind の呼び出し(展開の中の import を含む)は 1 回の束ねで約 0.2µs。doeff の handler を持つ 115 file はすべて新しい節の検め(R15)を通り、agora-controllers の本線では 1 か所(controllers/protocol/webapp_reception.hy の AwaitRequest の節 — 型で場合を全部並べた match に最後の _ が無い)が断られる(doeff を上げる時に `_ (raise …)` の 1 行を足す)。"
       :evidence "uv run python で yield Ok(1) を撃った VM の誤り・timeit(2026-09-28)・doeff の全 .hy と agora-controllers origin/main の全 .hy を hy_compile だけで展開した走査(2026-09-28)")]
  :context
    [(interpretation
       "文献では、中身を持たない失敗を Maybe の側に、理由を持つ失敗を Result(Either)の側に置く。Haskell の MonadFail の fail(説明の文を受けるが Maybe では捨てて Nothing にする)と Alternative の empty が前者、Plotkin と Pretnar の例外の raise・Koka の throw・Haskell の throwError が後者。operator の問い『Maybe の文脈で Fail に当たる物は何か』の答えは、文献では fail / empty がまさに Maybe の側で、Result の側は raise / throw と呼ぶ、となる。この ADR は紛れを避けて、Maybe の側を Absent、Result の側を Raise と呼び、どちらにも Fail の名を使わない(doeff-traverse の Fail は再開できる別の意味を既に持つ)。")
     (interpretation
       "1 つの effect(Nothing だけ・Fail だけ)にすると、受け手が不在と失敗を分けられず、Result の中の Maybe と Maybe の中の Result を作り分けられない。2 つの effect に分ければ、受け手の handler の入れ子の順だけで組み合わせの型が決まる — モナド変換子の積み順(MaybeT (Either e) と ExceptT e Maybe)と同じことを、handler の順で言える。")
     (interpretation
       "答えを値で受けるか中身で受けるかは呼び手の文脈で変わる(逐語 1)。初めの案はこれを『成功だけを見る』範囲の宣言(success-only)で表したが、範囲の内と外で同じ行の型が変わる。出す側が最初から Absent / Raise を出せば、(<- x (危ない呼び)) は誰に呼ばれても『T か、逃げる』の 1 通りで、行の型は呼び手で変わらない。値で受けたい呼び手だけが、その場で境目の handler で畳む(逐語 7・8)。Koka の書き方と同じ。")
     (interpretation
       "effect に答える handler は、呼び手の本文より外側(組み立ての根)に置かれる。本文と handler の間には呼び手の maybe / result がある。handler の節の中で Absent / Raise を出すと、それは handler より外側へ行き、呼び手の maybe / result を飛び越える(fact と テストで確かめた)。だから不在と失敗への変換は handler の中でなく、呼び手の本文の中 — 答えを受け取る <- — で行う。handler は今までどおり値で答え、記録係も値を書き留める。")
     (interpretation
       "try-catch との比較(逐語 6『try-catch に似ているが差し替えられる』への答え):\n観点 | try-catch(例外) | Absent / Raise(この案)\n飛んでくる所 | どの行からでも(呼び先の奥の例外も) | Absent / Raise を出す行と、宣言で失敗を返す effect を受ける <- だけ\n何が飛ぶか | 書かれない(Java の検査例外は手書きで古くなる) | 推論した effect の集合に出る(R13)\n不在と失敗 | 区別しない(null か例外) | Absent と Raise で分け、組み合わせは入れ子の順で選ぶ\n既定値 | catch の中で値を返してスコープを終える | absent-as が既定値で再開できる\n方針の差し替え | catch は書いた所に固定 | 組み立ての handler で差し替える(本番と模擬で別)\n再生 | 例外は記録に残らない | handler は値で答え記録係が書き留めるので、再生でも同じ道を通る\n受け取り忘れ | 走らせて初めて分かる | job の入口の残りの effect で、書いた時点に拾える\n何でも受ける形 | catch (Exception) が書ける | on-raise は型のパターンだけ(何でも受ける形は展開の時に断る)\n見た目 | try … catch | on-raise … パターン — ほぼ同じ形なので、読み手は新しい考え方を覚えずに読める(利点)")
     (interpretation
       "書き方の例(file の頭の註): revise-input の After は成功の道だけを書き、不在と失敗を 1 つの on-raise で業務の答えの型へ畳む。外から見た型(戻り値と残りの effect)は Before と同じで、手で書いた :effects は推論に置き換わって消える。notify-landed の例は、業務の core に境目を書かず、1 件ごとの失敗の扱いを組み立ての handler に任せる形。")
     (interpretation
       "既定の受け手は Absent / Raise を受けて続きを捨てるので、受けた後では再試行も既定値での再開もできない(続きが無い)。再試行は答えがまだ失敗の値のうち — 値で答える土台の handler の中 — で行う。既定値は effect ならではの形(Absent を受けた handler が既定値で続きを再開する)で書けるが、『知らない値を黙って既定値にしない』に触れるので、既定値を与える handler は書いた人が明示した物に限る。doeff-traverse の normalize_to_none(Fail を黙って None で再開する)は、明示しないと起きる形の実例。")
     (interpretation
       "記録と再生の handler(ADR-DOE-CLUSTER-001 R5)は、読みの effect の本物の答え(Unreachable のような失敗の値も)を境目の下で書き留める。<- の変換は本文の中の決定的な計算なので、再生でも同じ答えから同じ Absent / Raise が出る。")
     (interpretation
       "関数が Absent・Raise を出すかどうかは、その関数が呼ぶ関数と、途中で置いた handler で決まる(逐語 4)。手で書いた :effects は、呼び先が変わると黙って古くなる。呼び先を推移的にたどる推論(doeff-effect-analyzer)なら古くならない。関数の型は、戻り値(中身だけ — Maybe / Result に包まない)と、推論した残りの effect の集合の組として見せる。これは Eff などの論文の計算の型 A ! Δ と同じ考え方(Koka では exn int のように effect の列を戻り値の前に書く)。")
     (interpretation
       "R13 のために analyzer に足りない物は 3 つ: (1) Program の中で置いた handler(with-handlers・maybe・result・on-raise・<- の :absent の受け手)が受ける effect を集合から引く処理 — 今は Program の effect を集めるだけで、handler を引くのは env の coverage の側だけ。(2) 引数で渡された Program(carried)を、渡した先の意味に従って呼び手の側で畳む処理 — 今は呼び手が effect_types_with で明示して畳む。(3) 結果を cache して linter とエディタが読む形 — Jev の cache と同じ扱いで、保存のたびに module を import しない。今の cache は process の中の module の cache だけ。")
     (interpretation
       "effect は『積み重なるもう 1 本のデータの口』(逐語 9)。4 種類の失敗は、本文の中では effect として外へ流れ、境目で値に戻る — 外の世界との境目(handler の答え・記録)では答えの値、呼び手が『何かしたい』境目(maybe・result・on-raise・absent-as)でも値。だから handler と記録の形は変えず(値で答える)、変わるのは本文の中の受け取り方(<- の開き)と、宣言(defeffect の :absent / :failure / :value)だけになる。doeff-records の handler を変えない理由も同じ: handler の節で Raise を出すと呼び手のスコープを飛び越える(R4)。")
     (interpretation
       "段階 1・2 の実装で決めた戻せる決定(2026-09-28・この実装の担当が推奨どおり決めた — 戻し方は各項の後ろ):\n(1) maybe・result は Program を受ける関数(doeff_core_effects.outcomes)で、on-raise・absent-as は Program の式 1 つを受ける macro — 複数の行は do! で包む(handle・Try と同じ形。ADR の (maybe (<- (ReadRow …))) は (maybe (ReadRow …)) と書く)。戻し方: 本文の form を並べて受ける macro を足す。\n(2) handler の終わる節の名は (finish 値)、普通の effect を打ち切る理由は節の鍵 :finish-reason \"理由\"(Hy の reader は註を捨てるので、macro が読める鍵にした)。戻し方: 名と鍵を差し替える。\n(3) Raise の理由に Python の例外を置けない(Raise を作る時に TypeError)。<- が Err(Python の例外) を開く時は、Raise にせず例外のまま上げる(Try が畳んだ実装の誤りを業務の失敗に化けさせない — R11)。戻し方: effects.py の検めと outcomes.py の _open_value の 1 枝を消す。\n(4) <- の :absent <失敗> の失敗は、不在の時にだけ評価する値の式(効果は使えない — 展開の時に断る)。戻し方: open-bind に値を渡す形へ戻す。\n(5) <- と ! の展開は、束ねごとに doeff_core_effects.outcomes.open-bind を呼ぶ(import を展開の中に持つ — どこに書いた <- でも名前が解ける・1 回で約 0.2µs)。宣言の無い effect と Program には受け取った物そのものを返すので、yield する物・束ねる答え・例外は今までと同じ。展開の字面は変わる(tests/test_typed_effect_bind.py の形を直した)。戻し方: 段階 2 の commit の revert。\n(6) 効果の再開の扱いは effect の型の属性 __doeff_resumption__(Resumption)。宣言と節の照合は、名前で分かる物は展開の時、分からない物は handler を初めて本文に被せた時(定義の時にすると、handler より後に定義した effect の型を引けない)。戻し方: clause_endings.check-clause-endings-once の呼び出しを消す。\n(7) 節の終わりの検めを厳しくした: cond は最後が True、match は最後が番の無い _(名前 1 つの capture)、try は except の本体も終わること、入れ子の関数・内包表記の中の resume は数えない。戻し方: handle.hy の _terminates の枝を戻す。\n(8) 段階 4 の中身(この ADR の R16 の段階 4)は、段階 3 と 5 を同時に切り替える前に要る道具(古い分岐を拾う doeff-linter の規則・R13 の推論・Rust の analyzer の :absent の読み)と解した — coordinator の依頼に段階 4 の定義が無かったため。戻し方: R16 の段階 4 の文を差し替える。")]
  :decision
    [(rule R1 "不在と失敗を、意味で分けた 2 つの effect にする。Absent(想定内の不在・Maybe の側)は中身を持たず、記録と調べ物のための説明の文だけを添える。Raise(e)(失敗・Result の側)は理由 e を持つ(理由に Python の例外は置けない — R11)。2 つの間に継承の関係を作らない(isinstance で互いに捕まらない)。境目の handler のうち maybe・result・on-raise と組み立ての方針の handler は、答えを返さず続きを捨てる。続きを再開してよいのは R8 の absent-as だけ。文献の fail / empty(Maybe の側)と raise / throw(Result の側)に当たる。失敗の側を Fail と呼ばない — 文献では Maybe の側の名であり、doeff-traverse の Fail(再開できる失敗の知らせ)とも紛れる。置き場は Try と同じ doeff-core-effects(doeff_core_effects.effects の Absent・Raise、doeff_core_effects.outcomes の境目の handler)。")
     (rule R2 "Absent・Raise の受け取り方は、外側のスコープの境目の handler が決める。(maybe body) は Absent を Nothing に、成功を Some に写す。(result body) は Raise(e) を Err(e) に、成功を Ok に写す。組み合わせの型は入れ子の順で選ぶ: (result (maybe …)) は Result の中に Maybe(Ok(Some v)・Ok(Nothing)・Err(e))、(maybe (result …)) は Maybe の中に Result(Some(Ok v)・Some(Err e)・Nothing)。組み立ての方針の handler(1 件を飛ばして記録する・全体を止める)も Raise を受けてよい — 飛ばすのは受けたスコープ 1 つ分で、続きは戻らない。受け手の無い Absent / Raise は、ほかの effect と同じく未処理の effect として止まる(黙って Nothing や None にしない)。runner・env の組は境目の handler を既定で置かない(ADR-DOE-CLUSTER-001 R2)。")
     (rule R3 "出す側は最初から Absent / Raise を出すのを既定にする(Koka と同じ素直な書き方)。defk の本文は直に (<- (Raise e)) / (<- (Absent 説明)) を出す — 本文は呼び手の動的なスコープで走るので、呼び手の maybe / result に届く。(<- x (危ない呼び)) の x は成功の値 T だけで、誰に呼ばれても『T か、逃げる』の 1 通り — 同じ行の意味が呼び手で変わらない。『成功だけを見る』範囲を宣言する構文(success-only)は置かない(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定。戻し方: 範囲の宣言の macro を足し、R3 をその形の文へ戻す)。")
     (rule R4 "effect に答える handler(ReadRow に答える土台の handler・翻訳の handler)は、節の中で Absent / Raise を出してはいけない。handler の節で出した effect はその handler より外側の handler へ行き、呼び手の本文のスコープ(maybe / result)を飛び越える:\n  組み立ての根: [外の受け手 … 土台の handler(ReadRow に答える)]   ← 外側\n    呼び手の本文: (result (maybe (<- row (ReadRow …))))              ← 内側\n  ReadRow は本文から外へ向かい、maybe・result を素通りして土台の handler に届く。土台の handler がそこで Absent を出すと、それは土台の handler より外へ行き、内側の maybe・result には戻らない。\nだから handler は今までどおり値で答え(記録係も値を書き留め、再生も変わらない)、呼び手の側の <- が defeffect の宣言(R5)に従って、不在の答えを Absent に、失敗の答えを Raise(答え)に変えて本文のスコープで出す。大事な落とし穴なので、doeff-linter の規則にする(R9 の 2 つ目の閉じる所と同じ規則)。")
     (rule R5 "何を成功・不在・失敗・値とみなすかは effect ごとの defeffect の宣言で決め、型の名から推し量らない。形は {:answer (| Row Missing Unreachable Conflict) :absent [Missing] :failure [Unreachable] :value [Conflict]} — :absent / :failure / :value はどれも任意で、:answer の union の要素の list(None も書ける)。どれにも書かない要素が成功。要素が :answer に無い・2 つの鍵に重なるのは展開の時の誤り(:answer が union の form でない時は定義の時に Outcomes.declare が断る)。1 つでも書けば effect の型に __doeff_outcomes__(doeff_core_effects.outcomes.Outcomes)が付く。<- は宣言に従い、成功と値なら中身を束ね、不在なら Absent を、失敗なら Raise(答え)を出す。宣言の無い effect の答えは変換せず、答えのまま束ねる(yield する物も今までと同じ)。業務で普通に扱う答えは失敗と宣言しない — 例: PutRow の Conflict(版の負け)は CAS の繰り返しで普通に扱う答えなので :value と宣言する。不在を値で受けたい所は、その行を maybe で包む((maybe (ReadRow …)) は Maybe の値)。doeff-records の effect(今は defclass)は defeffect へ移してこの宣言を持つ(移し方は R14)。")
     (rule R6 "役を 2 つに分ける(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定)。開く(値 → effect): <- と ! だけ(! は <- と同じ開きを通る)— 宣言に従う変換(R5)、:absent <失敗>、Result / Option の値を渡すと開く形。畳む(effect → 値): 境目の handler maybe・result・on-raise・absent-as だけ。:absent <失敗> は <- の末尾の引数で、その 1 行の束ねを包む狭い受け手として、中で出た Absent(直にも、呼んだ defk の奥にも)を Raise(<失敗>) に変える(束ねる行と意味を同じ場所に置く。受け手は呼び手の本文の内側にあるので、出した Raise は呼び手の境目に届く)。<失敗> は不在の時にだけ評価する値の式で、効果は使えない。受け取った Result / Option の値は、もう一度 <- に渡せば開ける(Ok・Some → 中身、Err(e) → Raise(e)、Nothing → Absent。Err の中身が Python の例外なら例外のまま上げる — R11)— 値と effect を行き来でき、開くための別の構文は作らない。型(Option は doeff の Maybe = Some | Nothing):\n  (<- x (dangerous-call))      x : T\n  dangerous-call               : Program[T] ! {Raise[E], …}\n  (maybe body)                 : Program[Option[T]] ! {Raise[E], …}\n  (result body)                : Program[Result[T]] ! {Absent, …}\n  (result (maybe body))        : Program[Result[Option[T]]] ! {…}")
     (rule R7 "境目の on-raise は (on-raise 本文 パターン [:if 番] 写し先 …) の形 — 失敗の型のパターン((型 欄 …) か (| (A …) (B …)))と、それを写す業務の答えだけを並べる。何でも受ける形(_・名前 1 つの capture・object・Exception・BaseException)は展開の時に断り、例外の型を名指す受けは作る時に断る(RaiseCase)。パターンに合わない Raise は受けずに外へ渡す。Python の例外(実装の誤り)は on-raise が受けない(Raise だけを見る)。番と写し先は値の式(効果は使えない)(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定)。")
     (rule R8 "(absent-as 既定値 本文) は、中で出た Absent を既定値に畳む(effect ならではの形 — Maybe を値で受けて既定値へ畳む手間が要らない)。『知らない値を黙って既定値にしない』(code-quality の方針)に触れるので、既定値を与える handler はその場に書いた (absent-as …) のような明示の物に限り、runner・env の組・土台の handler に既定値での再開を置かない。再開の値が型に合うのは、Absent が absent-as の字面の中の <- で値の代わりに出た時だけ — 本文が (do! …) なら、その字面の中に直に書いた <- と ! が出した不在だけを既定値で再開する(その束ねが既定値を受けて続く)。呼んだ defk の奥で出た Absent に既定値を返すと、奥の行が別の型の値を受ける。コードに直に書いた (<- (Absent …)) を再開すると、不在を前提にしない続きが走る。この 2 つの場合は再開せず、既定値で absent-as のスコープを終える。入れ子の do! / fnk / 関数の中の束ねは『奥』に数える。")
     (rule R9 "境目の handler(maybe・result・on-raise・absent-as)を書くのは、不在か失敗に対して何かしたい所だけ。それ以外は成功の道だけを書き、Absent / Raise は外へ逃がす(try-catch を受け止めたい所にだけ書くのと同じ)。ただし構造上必ず閉じる所が 2 つある。(1) job の入口 — runner は handler を足さない(ADR-DOE-CLUSTER-001 R2)ので、service の本体の方針の handler(飛ばして記録・止める等)で閉じる。確かめ: job の Program の推論した残りの effect が空。(2) handler の節の中で Program を走らせて答える所(翻訳の handler)— handler の中から逃げた Raise は handler より外側へ行き呼び手のスコープを飛び越える(R4)ので、答えの値(業務の effect の答えの型)に閉じてから返す。確かめ: handler の節で走らせる Program の推論した残りの effect に Absent / Raise が無い — doeff-linter の新しい規則の候補(番号は未定)(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定)。")
     (rule R10 "再試行は Raise を受けてからではできない(続きが捨てられている)。答えがまだ失敗の値のうちに、値で答える土台の handler の中(Unreachable なら出し直す)で行う。")
     (rule R11 "Python の例外は実装の誤りのまま扱う(doeff-records の決まりと同じ)。想定内の不在・失敗を例外で表さない — Raise の理由に Python の例外は置けない。Try(例外を Result に畳む)は今のまま残し、Absent / Raise と混ぜない — <- が Try の Err(例外) を開く時は Raise にせず例外のまま上げる。")
     (rule R12 "記録と再生との関係: handler は値で答え、記録係は境目の下で本物の答え(失敗の値も)を書き留める(ADR-DOE-CLUSTER-001 R5)。<- の変換は本文の中の決定的な計算なので、再生でも同じ答えから同じ Absent / Raise が出る。Absent / Raise を記録係へ届く汎用の effect にしない(記録するのは読みの effect の答え)。")
     (rule R13 "関数がどの effect を出すか(Absent・Raise を含む)は、手で書かずに doeff-effect-analyzer で推論する(呼び先を推移的にたどり、Hy は macro を展開してから読み、defhandler の節から受ける effect を読み、追えない物は unresolved と報告する)。関数の型は、戻り値(中身だけ・Maybe / Result に包まない)と推論した残りの effect の集合の組として見せる。表記の例: settle-landing : str → Program[Row] ! {Absent, Raise[Unreachable | Refused], ReadRow, PutRow}(Eff などの論文の計算の型 A ! Δ と同じ考え方)。handler で包むと、その handler が受ける effect が集合から消える。:effects(defk の頭の辞書に既にある任意のキー)は必須にしない。公開の境目にだけ上限として書き、推論 ⊆ 宣言を linter が確かめる(宣言が古くならない)。使い道: エディタの hover で残りの effect を出す/job の入口の Program の残りの effect が空であることを書いた時点で確かめる(R9 の 1 つ目)/core の定義の残りの effect に汎用の effect(通信・記録)が入れば層の違反として出す。analyzer に足りない物は :context の interpretation。")
     (rule R14 "移し替え(段階 3 と段階 5 の切り替えの手順): 今の doeff-records の effect は失敗を値で返す決まり(README『失敗の答え(値で返す)』)。宣言を切り替えると、(match body (Missing) …) の既存の呼び手は Missing を見なくなる(決して通らない分岐が残る)。だから次の順で、effect ごとに(1 つの版で 1 つの effect — 一度に全部の effect を切り替えない)、doeff-records の宣言と agora の呼び手を同じ版で切り替える:\n(1) 切り替える effect E を 1 つ選ぶ(呼び手の少ない物から — ReadPlacedInput・PostMessage など。ReadRow は最後)。\n(2) doeff-records の E を defclass から defeffect へ移し、:absent / :failure / :value を R17 の割り当てで宣言する。E に答える handler は変えない(値で答える — handler の節で Raise を出すと呼び手のスコープを飛び越える: R4)。記録の形も変わらないので、既存の記録はそのまま再生できる。\n(3) 同じ版で agora の E の呼び手を書き換える: 不在・失敗を (match …) / isinstance で分けていた呼び手は成功の道だけにし、不在で何かしたい所は <- の :absent <失敗> か absent-as、失敗で何かしたい所は on-raise にする。Missing を普通の値として持ち回る・数える呼び手は (maybe …) で包み、Maybe の値(Missing の代わりに Nothing)を受ける形にする。:value と宣言した答え(Conflict)の呼び手は変えない。job の入口と、handler の節で Program を走らせる翻訳の handler で Absent / Raise を閉じる(R9)。\n(4) 古い分岐(E の不在・失敗と宣言した型を値として match / isinstance する分岐)は doeff-linter の規則が拾う(段階 4 の道具)。当たりが 0 になるまでその版を着地させない — law declarations-switch-one-effect-at-a-time。\n(5) 型の決まった書き換え(古い分岐の削除・maybe での包み・:absent への移し替え)は Sonnet 5 に任せる — effect ごとの手順(この R14)と linter の当たりの一覧を渡して機械的に進める。不在を何の失敗にするか・どこで閉じるかの判断が要る所は、その呼び手の持ち主が決める。\n(6) HTTP の答えの状態コードは、翻訳の層(HTTP の答えを業務の答えへ写す handler)の 1 か所で 4 種類に写し(R17)、呼び手に状態コードを見せない。")
     (rule R15 "handler の節の終わり方(operator の懸念『再開の書き忘れ』— 逐語 10): 節は resume(非末尾なら続きの答えが節へ戻る)・transfer・finish(続きを捨て、handler を置いたスコープの答えをその値にする)・reperform・raise のどれかで終わる。書き忘れは今までどおり誤り — 節のすべての道(if・cond・match・try の枝ごと。cond は最後が True、match は最後が番の無い _、try は except の本体も)が終わらなければ展開の時に SyntaxError。when・unless・and・or・loop・入れ子の関数と内包表記の中の resume は終わりに数えない。effect ごとに再開の扱いを宣言する(effect の型の属性 __doeff_resumption__ = doeff_core_effects.effects.Resumption: Raise = NEVER(誰も再開しない)・Absent = ABSENT_AS_ONLY(absent-as だけが再開する)・宣言しない普通の effect = REQUIRED)。宣言に反する節(Raise / Absent を resume・transfer する節、普通の effect を finish で打ち切る節)は違反 — 名前で分かる物(節の頭が Raise / Absent・finish の節に理由が無い)は展開の時に SyntaxError、分からない物(別名で import した Raise・__doeff_resumption__ を宣言した型)は handler を初めて本文に被せた時に ClauseEndingError。とくに Raise の resume は『失敗したのに成功したかのように続く』ので、宣言(NEVER)と展開の両方で断り、<- が開いた Raise / Absent を素の handler 関数が再開しても、開く側が RuntimeError にする。普通の effect を意図して打ち切る節(時間切れで処理全体を止める等)は、節に :finish-reason \"理由\" を書いた時だけ許す(deff の理由の註と同じ扱い)。実行の時: 節が resume も finish もせずに抜けたら(例外を黙らせる with の中の resume など、展開の時に見えない道)、defhandler の包みが RuntimeError にする — VM は再開しない handler の値でスコープを黙って終えるため。素の Python の handler 関数(@do の関数・deff の handler)にはこの包みが無く、VM は『再開しないで値を返す』を正当な終わり方として受けるので、実行の時の検めは効かない(塞げない理由: finish と書き忘れを見分ける印が VM に無い — 見分けるには VM に明示の終わりの操作を足し、既存の Python の handler を全部書き換える必要がある)。素の handler 関数の終わり方は doeff-linter の規則の候補(番号は未定)。")
     (rule R16 "段階の計画(2026-09-27 夜・operator の決定 — 逐語 9):\n段階 1(振る舞いを変えない・足すだけ): Absent / Raise の effect・境目の handler maybe・result・on-raise・absent-as(奥の不在は既定値でスコープを終える)・defhandler の終わる節 (finish) と節の終わり方の検め(R15)。\n段階 2(振る舞いを変えない — 宣言を持つ effect がまだ無い): defeffect の答えの宣言(R5)・<- と ! の変換・:absent・Result / Option の値を開く形(R6)・absent-as の字面の中の <- の再開(R8)。宣言の無い effect の <- は yield する物も束ねる答えも今までと同じ。\n段階 3: doeff-records の effect を defeffect へ移して宣言を付ける(handler は変えない)。\n段階 4: 段階 3 と 5 を切り替える前に要る道具 — 古い分岐を拾う doeff-linter の規則(R14 (4))・on-raise の何でも受ける形と handler の節の Absent / Raise の規則(R4・R9)・推論(R13)・Rust の analyzer の <- の :absent の読み。\n段階 5: agora の呼び手の書き換え。段階 3 と段階 5 は同時に — effect ごとに同じ版で — 切り替える(R14)。")
     (rule R17 "繰り返し出る 4 種類の失敗の割り当て(operator の決定 — 逐語 9): Unreachable(置き場・相手に届かない)と Refused(相手が断った)は失敗(:failure — <- は Raise を出す)、Missing(行が無い)は不在(:absent — <- は Absent を出す)、Conflict(版の負け)は値(:value — CAS の繰り返しで普通に扱う答え)。4 種類はコードの本文では effect、外の世界との境目(handler の答え・記録)と呼び手が何かしたい境目(maybe・result・on-raise・absent-as)ではデータ。HTTP の状態コードは翻訳の層の 1 か所でこの 4 種類に写す(例: 404 → Missing、409・412 → Conflict、401・403 → Refused、5xx と通信の失敗 → Unreachable — 写し方の表は翻訳の層が持つ)。\n追補(2026-09-28・agora-redesign #840 — 5 つ目の失敗の種類): Malformed(外から来た JSON が型の約束の形でない — どの型の・どの欄が・なぜを持つ。doeff_hy.wire)は失敗(:failure — <- は Raise を出す)。相手の版の食い違いは運用で起きうるので、実装の誤りの例外ではなく業務の失敗として扱う。出所は defwire の解き手 parse / parse-json の 1 か所だけ(ADR-DOE-HY-007 R8)。段階 3 の切り替え(R14)までは parse は Malformed を値で返し(答えの型 = (| T Malformed))、呼び手は値で分ける。切り替えで parse を :failure の宣言つきにし、Raise(Malformed) に寄せる(effect ごとに 1 つの版で — 他の 4 種類と同じ手順)。決めた席 = #840 の担当(operator の決定 \"A\" の後・推奨どおり — 戻せる決定)。戻し方: この追補の文を消し、parse の答えを Malformed の値のままにする。")]
  :laws
    [(law absent-and-raise-are-distinct
       :statement "Absent は中身を持たない(説明の文だけ)∧ Raise は理由を持つ ∧ Absent と Raise は継承の関係を持たない ∧ for_all 境目の handler h ∈ {maybe, result, on-raise, 組み立ての方針の handler}: h は Absent / Raise を受けて続きを再開しない"
       :counterexamples
         [(counterexample "不在も失敗も 1 つの Fail で出す — 受け手が不在と失敗を分けられず、Result の中の Maybe が作れない")
          (counterexample "Raise を Absent の子の型にする — (maybe …) が失敗まで Nothing に潰し、理由が消える")
          (counterexample "失敗の側を Fail と名づける — doeff-traverse の Fail(None で再開できる)と同じ名で、続きを捨てるかどうかが名前から読めない")]
       :enforced-by ["test-adr-doe-core-effects-003-absent-and-raise-are-distinct"]
       :wiring "配線済み(2026-09-28)— 欄・継承・再開の宣言と、maybe / result が続きを再開しないことを検が確かめる。組み立ての方針の handler(Traverse の 1 件ごとの受け手)は段階 4 の後")
     (law fold-handlers-choose-the-shape
       :statement "for_all スコープ s: s の外へ出る値の形は、s を囲む境目の handler の入れ子の順だけで決まる — (result (maybe b)) : Result[Maybe[T]] ∧ (maybe (result b)) : Maybe[Result[T]] ∧ 受け手の無い Absent / Raise は未処理の effect として止まる ∧ runner・env の組は境目の handler を置かない"
       :counterexamples
         [(counterexample "runner や env の組が既定で maybe を置く — Program を読んでもどの形で返るか分からない(ADR-DOE-CLUSTER-001 R2 と同じ害)")
          (counterexample "受け手の無い Raise を最上位で黙って None にする — 失敗が成功の値に化ける")]
       :enforced-by ["test-adr-doe-core-effects-003-nesting-order-chooses-the-shape"]
       :wiring "配線済み(2026-09-28)— 入れ子の順の 6 通りと、受け手の無い Absent / Raise が UnhandledEffect で止まることを検が確かめる。runner が境目の handler を置かないことは ADR-DOE-CLUSTER-001 の台帳の検が見る")
     (law a-bind-has-one-meaning
       :statement "for_all 束ね (<- x e) の行 l: x の型は e の宣言だけで決まる成功の型 T で、l を呼ぶ側に置いた handler では変わらない — 不在・失敗は x に入らず Absent / Raise として逃げる ∧ :absent <失敗> はその束ねの中の不在だけを Raise(<失敗>) に写す"
       :counterexamples
         [(counterexample "呼び手の handler で答えの型を切り替える — ある呼び手では Row、別の呼び手では Row | Missing | Unreachable が x に入り、中の (. row value) が壊れる")
          (counterexample "『成功だけを見る』範囲を宣言する構文(success-only)を足す — 範囲の内と外で同じ行の型が変わり、書き方が 2 つ並ぶ")
          (counterexample ":absent の受け手を呼び手のスコープの外(組み立ての根)に置く — 別の束ねの不在まで同じ失敗に写り、どの行の不在かが消える")]
       :enforced-by ["test-adr-doe-core-effects-003-a-bind-has-one-meaning"
                     "test-adr-doe-core-effects-003-absent-option-maps-one-bind"]
       :wiring "配線済み(2026-09-28)— 同じ束ねの行が maybe・result・on-raise のどの下でも成功の型を束ねることと、:absent の写しを検が確かめる")
     (law handlers-answer-with-values
       :statement "for_all effect E に答える handler h: h の節は Absent / Raise を出さず、E の答えの値で答える ∧ 不在と失敗への変換は呼び手の本文の <- が行う"
       :counterexamples
         [(counterexample "ReadRow に答える土台の handler が、行が無い時に (<- (Absent 行が無い)) を出す — Absent は土台の handler より外へ行き、呼び手の maybe を飛び越えて未処理になる(または無関係の外の受け手に捕まる)")
          (counterexample "翻訳の handler が節の中で業務の Program を走らせ、その Program の Raise を閉じずに返す — 同じく呼び手の result を飛び越える")
          (counterexample "記録係が Raise を書き留める形にする — 答えの値が記録に残らず、再生で同じ変換を通れない")]
       :enforced-by ["test-adr-doe-core-effects-003-raise-in-a-handler-clause-skips-the-bodys-result"
                     "test-adr-doe-core-effects-003-declared-bind-opens-the-answer"]
       :wiring "一部配線(2026-09-28)— 検は、handler の節で出した Raise が本文の result を飛び越えること(落とし穴の実演)と、<- が呼び手の本文で答えを開くことを確かめる。handler の節が Absent / Raise を出さないことを見る doeff-linter の規則は候補で番号は未定(段階 4)")
     (law outcomes-are-declared-on-the-effect
       :statement "for_all effect の型 E, 答えの型 t ∈ answer(E): t が成功・不在・失敗・値のどれかは E の宣言(defeffect)から読める ∧ <- の変換はその宣言だけに従う(型の名を読まない)∧ 宣言の無い E の <- は yield する物も束ねる答えも変えない"
       :counterexamples
         [(counterexample "型の名に Missing・NotFound が含まれたら不在と推し量る — 名前を変えると意味が変わり、失敗の名を持つ新しい型は見落とす")
          (counterexample "PutRow の Conflict を一律に失敗と決める — CAS の繰り返しで普通に扱う答えまで Raise になり、呼び手が毎回 on-raise で値へ戻す")
          (counterexample "宣言の無い effect の答えの Missing も <- が Absent にする — 段階 3 の前に既存の呼び手の振る舞いが黙って変わる")]
       :enforced-by ["test-adr-doe-core-effects-003-declared-bind-opens-the-answer"
                     "test-adr-doe-core-effects-003-undeclared-bind-is-unchanged"]
       :wiring "配線済み(2026-09-28)— 宣言(Outcomes)・宣言に従う開き・宣言の無い effect の <- が同じ物を yield して同じ答えを束ねることを検が確かめる")
     (law on-raise-has-no-catch-all
       :statement "for_all on-raise o: o のパターンはどれも失敗の型のパターン(型を問わない形・_・Exception を含まない)∧ o は Python の例外を受けない ∧ パターンに合わない Raise は o の外へ渡る"
       :counterexamples
         [(counterexample "何でも受ける on-raise(パターン _ で全部を WriteUnreachable にする)— 新しい失敗の型が増えても黙って同じ答えに潰れ、推論した effect の集合にも出ない")
          (counterexample "on-raise が Python の例外も受ける — 実装の誤りが業務の失敗の答えに化ける")]
       :enforced-by ["test-adr-doe-core-effects-003-on-raise-refuses-python-exceptions-and-catch-all"]
       :wiring "配線済み(2026-09-28)— 何でも受けるパターンは展開の時に、例外の型を名指す受けは作る時に断り、Python の例外が on-raise を素通りすることを検が確かめる")
     (law defaults-are-explicit-handlers
       :statement "for_all Absent を既定値で再開する handler h: h はその場に書いた明示の物(absent-as)∧ h が再開するのは absent-as の字面の中の <- が値の代わりに出した Absent だけ ∧ runner・env の組・土台の handler は Absent を再開しない"
       :counterexamples
         [(counterexample "土台の handler が Absent を受けたら空文字で再開する — 呼び手の知らない所で不在が値に化ける(doeff-traverse の normalize_to_none と同じ形)")
          (counterexample "(absent-as 0 …) が、呼んだ defk の奥の (<- row (ReadRow …)) で出た Absent を 0 で再開する — 奥の行が Row の代わりに 0 を受ける")
          (counterexample "(absent-as 0 …) が、コードに直に書いた (<- (Absent 説明)) を 0 で再開する — 不在を前提にしない続きが走る")]
       :enforced-by ["test-adr-doe-core-effects-003-absent-as-resumes-only-direct-binds"]
       :wiring "一部配線(2026-09-28)— 字面の中の <- だけを再開し、奥の不在と直に書いた Absent ではスコープを終えることを検が確かめる。Absent を再開できるのが absent-as だけであることは R15 の節の検めと開く側の RuntimeError が守る。明示でない既定値の再開を見つける linter の規則は未定")
     (law handler-clauses-end-explicitly
       :statement "for_all defhandler / handle の節 c: c のすべての道が resume・transfer・finish・reperform・raise のどれかで終わる(展開の時)∧ c の終わり方が c の effect の Resumption に反しない(Raise・Absent を resume しない・普通の effect の finish には理由がある — 展開の時か、handler を初めて本文に被せた時)∧ c が実行の時に resume も finish もせずに抜けたら誤り(黙ってスコープを終えない)"
       :counterexamples
         [(counterexample "resume を書き忘れた節 (Tick [now] now) — 値を返して抜け、VM は黙ってスコープを now で終える")
          (counterexample "枝の片方だけ resume する節 (Tick [now] (if (> now 1) (resume 1) None)) — 片方の道で黙ってスコープが None で終わる")
          (counterexample "Raise を resume する節 (Raise [reason] (resume 0)) — 失敗したのに成功したかのように続く")
          (counterexample "理由の無い打ち切り (Tick [now] (finish None)) — 書き忘れと見分けられない")
          (counterexample "型で場合を並べた match に最後の _ が無い節 — 並べ漏れた値で黙って抜ける")]
       :enforced-by ["test-adr-doe-core-effects-003-defhandler-clause-must-end-explicitly"
                     "test-adr-doe-core-effects-003-clause-endings-checked-at-install-and-run"]
       :wiring "一部配線(2026-09-28)— defhandler と handle の節は展開・初めて被せた時・実行の時の 3 点で検める。素の Python の handler 関数(@do の関数・deff の handler)の終わり方は doeff-linter の規則の候補で番号は未定(R15)")
     (law scopes-appear-only-where-failure-is-handled
       :statement "for_all 境目の handler の置き場 b: b では不在か失敗に対して何かをする ∧ for_all job の Program j: 推論した残りの effect(j) = ∅(方針の handler で閉じる)∧ for_all handler の節で走らせる Program p: Absent ∉ 残り(p) ∧ Raise ∉ 残り(p)"
       :counterexamples
         [(counterexample "全部の呼び出しを result で包む — 成功の道だけのコードに Result の値の分岐が戻り、try-catch を全行に書くのと同じになる")
          (counterexample "handler の節で走らせた Program の Raise が漏れる — 呼び手のスコープを飛び越えて未処理になる")
          (counterexample "job の入口で閉じない — runner は handler を足さないので、Raise が未処理の effect として job を止める")]
       :enforced-by []
       :wiring "未配線(2026-09-28)— analyzer は Program の中の handler を引かず、doeff-linter の規則も候補で番号は未定(段階 4)")
     (law retry-happens-before-raise
       :statement "for_all 再試行 r: r は答えがまだ失敗の値のうちに、値で答える土台の handler の中で行う — Raise を受けた handler は再試行しない(続きが無い)"
       :counterexamples
         [(counterexample "(result …) の受け手が Err を見て同じ Program をもう一度走らせる — Program の前半の effect(書き)も繰り返される")]
       :enforced-by []
       :wiring "未配線(2026-09-28)— 機械で確かめる物は無い")
     (law effects-are-inferred-not-hand-written
       :statement "for_all 定義 f: f の出す effect の集合は推論(doeff-effect-analyzer)で得る ∧ f に :effects があれば 推論(f) ⊆ 宣言(f) を linter が確かめる ∧ :effects の無い f は違反ではない"
       :counterexamples
         [(counterexample "全部の defk に :effects を書かせる — 呼び先が変わるたびに黙って古くなり、書き手に推論の代わりをさせる")
          (counterexample "宣言の :effects だけを信じて推論と照らさない — 呼び先が Raise を出し始めても宣言は変わらず、job の入口の確かめが偽の緑になる")]
       :enforced-by []
       :wiring "未配線(2026-09-28)— analyzer は Program の中の handler を引かず、結果を道具が読む cache も無い。推論 ⊆ 宣言 の linter の規則は未定(段階 4)")
     (law declarations-switch-one-effect-at-a-time
       :statement "for_all effect E の宣言を切り替えた版: E の不在・失敗の答えの型を値として match する呼び手は 0(doeff-linter が拾う)∧ 1 つの版で切り替える effect は、その呼び手を同じ版で書き換え終えた物だけ"
       :counterexamples
         [(counterexample "ReadRow の宣言を切り替えたのに (match body (Missing) …) の分岐が残る — 決して通らない分岐が残り、読み手は不在がそこで扱われていると誤る")
          (counterexample "doeff-records の 7 つの effect の宣言を一度に切り替える — 書き換えの漏れが一度に全部の呼び手へ広がる")
          (counterexample "doeff-records の handler の節で Missing の時に Absent を出す形にして切り替える — 呼び手の maybe を飛び越える(R4)")]
       :enforced-by []
       :wiring "未配線(2026-09-28)— 古い分岐を拾う doeff-linter の規則は段階 4 で足す。切り替えの手順は R14")]
  :enforcement
    [(deftest test-adr-doe-core-effects-003-result-and-maybe-values
       ;; 事実: doeff.result の Ok / Err / Some / Nothing は今の形で使える(R2 の境目の handler が返す値)。
       (val boom (ValueError "boom"))
       (assert (= (. (Ok 1) value) 1))
       (assert (is (. (Err boom) error) boom))
       (assert (= (. (Some 2) value) 2))
       (assert (not Nothing) "Nothing は偽と評価される 1 つだけの値")
       (assert (isinstance (Some 2) Maybe))
       (assert (isinstance Nothing Maybe)))
     (deftest test-adr-doe-core-effects-003-try-folds-exceptions-into-result
       ;; 事実: Try は Program の中の Python の例外を Err に、成功を Ok に畳む(R11 — 今のまま残す口)。
       (val failed (run (try-handler (probe-try (probe-boom)))))
       (assert (isinstance failed Err) failed)
       (assert (isinstance (. failed error) ValueError) failed)
       (val succeeded (run (try-handler (probe-try (probe-one)))))
       (assert (isinstance succeeded Ok) succeeded)
       (assert (= (. succeeded value) 1)))
     (deftest test-adr-doe-core-effects-003-records-answers-mix-success-absence-failure
       ;; 事実: ReadRow の答えは成功・不在・失敗が 1 つの union に並ぶ(R5 の宣言が要る理由 — 段階 3 の前なので宣言はまだ無い)。
       (assert (= (get-args ReadRowAnswer) #(Row Missing Unreachable)))
       (val readme (.read-text (/ REPO-ROOT "packages/doeff-records/README.md") :encoding "utf-8"))
       (assert (in "失敗の答え" readme))
       (assert (in "値で返す" readme))
       (assert (in "例外で上がるのは組み立ての誤り" readme)))
     (deftest test-adr-doe-core-effects-003-handler-without-resume-ends-the-scope
       ;; 事実: VM は、再開しないで値を返す handler を許し、その値でスコープを終える(続きは捨てられる)
       ;; — R1 の Absent と R2 の maybe の形は、今の VM で書ける(R15 の実行の時の検めが要る理由でもある)。
       (val seen [])
       (assert (is (run (with-handlers [probe-maybe-handler] (probe-reads-then-continues seen))) Nothing))
       (assert (= seen []) "受け手が再開しないので、Absent の後ろは走らない"))
     (deftest test-adr-doe-core-effects-003-effect-performed-in-a-handler-skips-the-callers-scope
       ;; 事実(R4 の落とし穴): handler の節の中で出した effect は、その handler より外側へ行く。本文の側に置いた
       ;; maybe の受け手(本文と土台の handler の間)は飛び越えられ、土台の handler より外の受け手が受ける。
       (val answer (run (with-handlers [probe-outer-handler probe-read-handler]
                          (with-handlers [probe-maybe-handler] (probe-reads-a-row)))))
       (assert (= answer "外側が受けた") answer))
     (deftest test-adr-doe-core-effects-003-defhandler-clause-must-end-explicitly
       ;; R15(展開の時): 値を返すだけの節・枝の片方だけ resume する節・Raise を resume する節・理由の無い打ち切り・最後の _ の
       ;; 無い match は断られる。(finish 値) と理由つきの打ち切りは通る。
       (var refused False)
       (try
         (_check-clause-terminates "ProbeAbsent" [(hy.models.Symbol "Nothing")])
         (except [SyntaxError]
           (:= refused True)))
       (assert refused "値を返すだけの節は断られる")
       (_check-clause-terminates "ProbeAbsent" [(hy.read "(resume None)")])
       (_check-clause-terminates "ProbeAbsent" [(hy.read "(finish None)")])
       (val head "(require doeff-hy.macros [defhandler]) (import doeff_core_effects.effects [Raise Absent]) (defhandler probe-h ")
       (assert (in "missing resume" (probe-expansion-refusal (+ head "(ProbeRead [] \"値\"))"))) "resume の書き忘れ")
       (assert (in "missing resume" (probe-expansion-refusal (+ head "(ProbeRead [] (if True (resume 1) None)))"))) "枝の片方だけ")
       (assert (in "missing resume" (probe-expansion-refusal (+ head "(ProbeRead [] (when True (resume 1))))"))) "when だけ")
       (assert (in "missing resume" (probe-expansion-refusal (+ head "(ProbeRead [] (match 1 1 (resume 1) 2 (resume 2))))"))) "最後の _ の無い match")
       (assert (in "Raise の節は" (probe-expansion-refusal (+ head "(Raise [reason] (resume 0)))"))) "Raise の resume")
       (assert (in "Absent の節は" (probe-expansion-refusal (+ head "(Absent [why] (transfer 0)))"))) "Absent の再開")
       (assert (in ":finish-reason" (probe-expansion-refusal (+ head "(ProbeRead [] (finish None)))"))) "理由の無い打ち切り")
       (assert (= "" (probe-expansion-refusal (+ head "(Raise [reason] (finish reason)) (Absent [why] (finish None)))"))))
       (assert (= "" (probe-expansion-refusal (+ head "(ProbeRead [] :finish-reason \"時間切れ\" (finish None)))")))))
     (deftest test-adr-doe-core-effects-003-clause-endings-checked-at-install-and-run
       ;; R15(被せた時と実行の時): 別名で import した Raise の resume は初めて本文に被せた時に断り、展開の時に見えない道で
       ;; resume も finish もせずに抜けた節は実行の時に誤りにする。理由つきの打ち切りは handler のスコープを値で終える。
       (with [(pytest.raises ClauseEndingError :match "Resumption.NEVER")]
         (probe-aliased-raise-resumer (probe-one)))
       (with [(pytest.raises RuntimeError :match "どれにも届かずに抜けた")]
         (run (probe-swallowing-handler (probe-reads-a-row))))
       (assert (= (run ((probe-deadline 1) (probe-reads-a-row))) "読めた"))
       (assert (= (run ((probe-deadline 0) (probe-reads-a-row))) "打ち切った") "理由つきの finish はスコープを値で終える"))
     (deftest test-adr-doe-core-effects-003-traverse-fail-resumes-with-a-substitute
       ;; 事実: doeff-traverse の Fail は再開できる失敗の知らせで、normalize-to-none は None で続きを再開する
       ;; — R1 が失敗の側を Fail と呼ばない理由で、R8 が既定値の再開を明示の handler に限る理由でもある。
       (assert (= (run (normalize-to-none (probe-fails-then-continues))) #("続いた" None))))
     (deftest test-adr-doe-core-effects-003-declarations-carry-one-answer-and-optional-effects
       ;; 事実: defeffect は :answer の型を持ち、答えの分け方(:absent / :failure / :value)は任意 — 書かなければ
       ;; __doeff_outcomes__ は無い(R5)。defk の :effects は任意で、書けば __doeff_effects__ に残り、書かなければ None(R13)。
       (assert (in ":answer" EFFECT-KEYS))
       (assert (all (gfor key [":absent" ":failure" ":value"] (in key EFFECT-KEYS))))
       (assert (is (. ProbeAbsent __doeff_answer__) Never))
       (assert (is (getattr ProbeAbsent "__doeff_outcomes__" None) None))
       (assert (= (. probe-declared-effects __doeff_effects__) #(ProbeAbsent)))
       (assert (is (. probe-reads-then-continues __doeff_effects__) None)))
     (deftest test-adr-doe-core-effects-003-absent-and-raise-are-distinct
       ;; R1: Absent は説明の文だけ・Raise は理由を持つ・互いに継承しない・Raise の理由に Python の例外は置けない・
       ;; 境目の handler は続きを再開しない。
       (assert (= (lfor f (fields Absent) f.name) ["why"]))
       (assert (= (lfor f (fields Raise) f.name) ["reason"]))
       (assert (not (or (issubclass Raise Absent) (issubclass Absent Raise))))
       (assert (= #((resumption-of Raise) (resumption-of Absent) (resumption-of ProbeRead))
                  #(Resumption.NEVER Resumption.ABSENT-AS-ONLY Resumption.REQUIRED)))
       (with [(pytest.raises TypeError)]
         (Raise (ValueError "例外は理由にならない")))
       (val seen [])
       (assert (is (run (maybe (probe-absent-then-note seen))) Nothing))
       (assert (= (repr (run (result (probe-raise-then-note seen)))) "Err(ProbeDown(detail='届かない'))"))
       (assert (= seen []) "maybe と result は続きを再開しない"))
     (deftest test-adr-doe-core-effects-003-nesting-order-chooses-the-shape
       ;; (a) R2: (result (maybe b)) と (maybe (result b)) で形が変わる(Ok / Err は値で比べられないので repr で比べる)。
       (val shapes
         (lfor b [probe-one (fn [] (probe-absent-then-note [])) (fn [] (probe-raise-then-note []))]
               #((repr (run (result (maybe (b))))) (repr (run (maybe (result (b))))))))
       (assert (= shapes [#("Ok(Some(1))" "Some(Ok(1))")
                          #("Ok(Nothing)" "Nothing")
                          #("Err(ProbeDown(detail='届かない'))" "Some(Err(ProbeDown(detail='届かない')))")])
               shapes)
       (with [(pytest.raises UnhandledEffect)]
         (run (probe-absent-then-note [])))
       (with [(pytest.raises UnhandledEffect)]
         (run (probe-raise-then-note []))))
     (deftest test-adr-doe-core-effects-003-raise-in-a-handler-clause-skips-the-bodys-result
       ;; (b) R4: handler の節の中で出した Raise は、本文を包んだ result を飛び越え、handler より外の result に届く
       ;; (内側の result が受けていれば Ok(Err …) になるはず)。
       (val answer (run (result (probe-raising-read-handler (result (probe-reads-a-row))))))
       (assert (= (repr answer) "Err(ProbeConflict(detail='handler の節の中で出した'))") answer))
     (deftest test-adr-doe-core-effects-003-declared-bind-opens-the-answer
       ;; R5・R6: 宣言を持つ effect の <- は成功を束ね、不在を Absent に、失敗を Raise(答え) に変えて呼び手のスコープで出す。
       (assert (isinstance ProbeDeclaredRead.__doeff_outcomes__ Outcomes))
       (assert (= (run (probe-rows (maybe (probe-read-value "a")))) (Some "A")))
       (assert (is (run (probe-rows (maybe (probe-read-value "zz")))) Nothing))
       (assert (= (repr (run (probe-rows (result (probe-read-value "down"))))) "Err(ProbeDown(detail='網が落ちた'))"))
       (assert (is (run (probe-rows (maybe (ProbeDeclaredRead "zz")))) Nothing) "maybe に宣言を持つ effect をそのまま渡しても開く"))
     (deftest test-adr-doe-core-effects-003-undeclared-bind-is-unchanged
       ;; (c) R5: 宣言の無い effect の <- は、yield する物も束ねる答えも今までと同じ(不在・失敗の値もそのまま束ね、外へ何も出さない)。
       (val effect (ProbePlainRead "zz"))
       (assert (is (open-bind effect) effect) "宣言の無い effect は同じ物を yield する")
       (val program (probe-read-plain "zz"))
       (assert (is (open-bind program) program) "Program も同じ物を yield する")
       (assert (= (run (probe-rows (probe-read-plain "zz"))) (ProbeGone :key "zz")) "不在の値をそのまま束ねる")
       (assert (= (run (probe-rows (probe-read-plain "down"))) (ProbeDown :detail "網が落ちた")) "失敗の値をそのまま束ねる"))
     (deftest test-adr-doe-core-effects-003-a-bind-has-one-meaning
       ;; R3: 同じ束ねの行(probe-read-value の <-)は、呼び手の maybe・result・on-raise のどの下でも成功の型を束ねる。
       (val under-maybe (run (probe-rows (maybe (probe-read-value "a")))))
       (val under-result (run (probe-rows (result (probe-read-value "a")))))
       (val under-on-raise (run (probe-rows (on-raise (probe-read-value "a") (ProbeDown :detail d) d))))
       (assert (= #(under-maybe (repr under-result) under-on-raise) #((Some "A") "Ok('A')" "A"))
               #(under-maybe under-result under-on-raise)))
     (deftest test-adr-doe-core-effects-003-absent-option-maps-one-bind
       ;; (e) R6: <- の :absent は、その束ねの中の不在(呼んだ defk の奥の物も)を Raise(失敗) に写す。失敗はそのまま。
       (assert (= (repr (run (probe-rows (result (probe-read-or-conflict "zz")))))
                  "Err(ProbeConflict(detail='zz の行が無い'))"))
       (assert (= (repr (run (probe-rows (result (probe-read-or-conflict "down")))))
                  "Err(ProbeDown(detail='網が落ちた'))"))
       (assert (= (repr (run (probe-rows (result (probe-read-or-conflict "a"))))) "Ok('A')")))
     (deftest test-adr-doe-core-effects-003-on-raise-refuses-python-exceptions-and-catch-all
       ;; (d) R7: on-raise は Python の例外を受けない・何でも受けるパターンは展開の時に断る・例外の型を名指す受けは作る時に断る。
       (with [(pytest.raises ValueError :match "boom")]
         (run (probe-raises-python)))
       (for [pattern ["_" "reason" "(Exception)" "(object)"]]
         (assert (in "受けられない" (probe-expansion-refusal (+ "(require doeff-hy.macros [on-raise]) (on-raise body " pattern " 0)")))
                 pattern))
       (with [(pytest.raises TypeError)]
         (RaiseCase #(ValueError) (fn [r] (Some r))))
       (assert (= (run (on-raise (probe-raise-then-note []) (ProbeDown :detail d) d)) "届かない") "合う型の Raise は写す"))
     (deftest test-adr-doe-core-effects-003-absent-as-resumes-only-direct-binds
       ;; R8: absent-as は字面の中の <- の不在だけを既定値で再開し、奥の不在と直に書いた Absent では既定値でスコープを終える。
       (val direct (run (probe-rows (probe-direct-default "zz"))))
       (val deep (run (probe-rows (probe-deep-default "zz"))))
       (val written (run (probe-written-absent-default)))
       (val present (run (probe-rows (probe-direct-default "a"))))
       (assert (= #(direct deep written present) #("続いた: 既定" "既定" "既定" "続いた: A"))
               #(direct deep written present)))]
  :plans ["agora-redesign #836"
          "段階 3 と段階 5 の切り替えの手順 = この ADR の R14(effect ごとに同じ版で切り替える)"])
