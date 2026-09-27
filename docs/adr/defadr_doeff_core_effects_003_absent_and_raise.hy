;;; Executable ADR(提案): 想定内の「不在」と「失敗」を、意味で分けた 2 つの effect — Absent(Maybe の側)と Raise(Result の側)— にする。
;;; 出す側は最初から Absent / Raise を出し(Koka と同じ素直な書き方)、(<- x (危ない呼び)) の x は成功の値 T だけになる。答えを
;;; Maybe / Result の値で受けたい所だけ、境目の handler(maybe・result・on-raise・absent-as)で畳む。effect に答える handler は値で答え、
;;; 不在と失敗への変換は呼び手の側の <- が defeffect の宣言に従って行う。関数が出す effect は手で書かずに推論する。
;;;
;;; 出自 = operator との議論 2026-09-27 夜(f143ee92 の席・逐語は :problem の fact)。状態は提案 — 実装は無い。テストは、案が立って
;;; いる今の事実(値の型・Try・doeff-records の答え・VM と defhandler の振る舞い・doeff-traverse の Fail・:effects の宣言)を確かめる
;;; 緑のものだけを置く。
;;;
;;; 改訂の経緯(同じ日・書いている間): 初めの案は「成功だけを見る」範囲を字面のスコープ(macro success-only)で宣言する形だった。
;;; operator の逐語 7(success-only は要らないのでは)を受けて、f143ee92 の席が success-only を消す決定をした(戻せる決定 — R3)。
;;; 同じ行の意味が呼び手で変わらない、という success-only の狙いは、出す側が最初から Absent / Raise を出す形で満たされる。
;;;
;;; 置き場の判断: effect の語彙(Absent・Raise)と境目の handler は Try と同じ doeff-core-effects に置く物なので、ADR-DOE-CORE-EFFECTS の
;;; 3 つ目にした。<- の変換と defeffect の宣言は doeff-hy、effect の推論は doeff-effect-analyzer、規則は doeff-linter の仕事で、:scope に並べる。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つが消える。実装はまだ無い)。
;;;
;;; ---------------------------------------------------------------------------
;;; 書き方の例(operator の逐語 5 への答え — 案の形で、動くコードではない)
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
;;; After — この案の形(成功の道だけを書き、失敗は 1 か所の on-raise で業務の答えの型へ畳む):
;;;
;;;   (defk revise-input [revise]
;;;     {:pre [(: revise InputRevise)] :post [(: % (| WriteLanded WriteConflict WriteUnreachable))]
;;;      :tags {:context "conversation-input" :role "protocol"}}
;;;     "直しの書きを写すため: …"
;;;     (on-raise
;;;       (do
;;;         (<- body (read-typed PLACED-INPUT-TYPE #(revise.id))
;;;             :absent (Conflict (+ revise.id " の本文の行が無い")))
;;;         (<- placed (ReadPlacedInput :agent body.value.agent :ref revise.id)
;;;             :absent (Conflict (+ revise.id " は読んだ後に変わった")))
;;;         (unless (and (= body.version revise.version) (= placed.state LedgerState.PENDING))
;;;           (<- (Raise (Conflict (+ revise.id " は読んだ後に変わった")))))
;;;         (<- (revise-placed-input revise.id revise.text revise.edit-id))
;;;         (WriteLanded))
;;;       (Conflict d)        (WriteConflict :detail d)
;;;       (BodyConflict d)    (WriteConflict :detail d)
;;;       (Unreachable d)     (WriteUnreachable :detail d)
;;;       (BodyUnreachable d) (WriteUnreachable :detail d)))
;;;
;;;   - 外から見た型は Before と同じ: revise-input : InputRevise → Program[WriteLanded | WriteConflict | WriteUnreachable] ! {ReadRow, PutRow,
;;;     ReadPlacedInput}(Raise は on-raise が全部畳むので残らない)。手書きの :effects は推論(R13)に置き換わって消える。
;;;   - revise-placed-input は版の負けと届かないを BodyConflict・BodyUnreachable で出すので、on-raise はその 2 つも受ける。受けないと
;;;     Raise[BodyConflict | BodyUnreachable] が外へ漏れ、外から見た型が変わる(推論がそれを見せる)。
;;;   - Conflict はこの例のための失敗の型(説明の文 d を持つ)で、doeff-records の Conflict(current)とは別。名前は実装で決める。
;;;   - :absent の受け手は on-raise の内側に置かれるので、それが出す Raise は on-raise に届く(R4 の落とし穴には当たらない)。
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
(require doeff-hy.macros [deftest defk deff defeffect <- val var])
(require doeff-hy.handle [defhandler])
(import hy)
(import typing [Never get-args])
(import pathlib [Path])
(import doeff [run with-handlers Ok Err Some Nothing Pass Try EffectBase DoExpr K])
(import doeff.result [Maybe])
(import doeff-core-effects.handlers [try-handler])
(import doeff-records.values [ReadRowAnswer Row Missing Unreachable])
(import doeff-traverse.effects [Fail])
(import doeff-traverse.handlers [normalize-to-none])
(import doeff-hy.handle [_check-clause-terminates])
(import doeff-hy.declarations [EFFECT-KEYS])


(val REPO-ROOT (. (Path __file__) parent parent parent))


;; ---------------------------------------------------------------------------
;; 生きた probe — 今の VM と macro で書ける形・書けない形の実演(R1・R2・R4・R5・R13 が立つ土台の事実)。
;; ---------------------------------------------------------------------------

(defeffect ProbeAbsent
  "想定内の不在の見本(R1 の Absent の形 — 中身は持たず、説明の文だけ。答えは返らない)。"
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

(deff probe-maybe-handler [effect k]  ; defk にできない: defk は引数 (effect k) を handler と読んで断り、defhandler は再開しない節を断る — 続きを捨てる受け手は今は素の handler 関数でしか書けない(この ADR の fact)
  {:pre [(: effect EffectBase) (: k K)] :post [(: % DoExpr)]
   :tags {:context "doeff-core-effects-adr" :role "foundation"}}
  "R2 の maybe の受け手の見本: ProbeAbsent を受けたら再開せず Nothing でスコープを終える(続きは捨てる)。ほかは外へ渡す。"
  (if (isinstance effect ProbeAbsent)
      (probe-ending Nothing)
      (Pass effect k)))

(deff probe-outer-handler [effect k]  ; defk にできない: probe-maybe-handler と同じ(続きを捨てる受け手は今は素の handler 関数でしか書けない)
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


(defadr ADR-DOE-CORE-EFFECTS-003
  :title "想定内の『不在』と『失敗』を、意味で分けた 2 つの effect — Absent(Maybe の側・中身を持たず説明の文だけ)と Raise(Result の側・理由を持つ)— にする。出す側は最初から Absent / Raise を出し、(<- x (危ない呼び)) の x は成功の値 T だけ。答えを Maybe / Result の値で受けたい所だけ境目の handler(maybe・result・on-raise・absent-as)で畳み、入れ子の順が組み合わせの型を決める。effect に答える handler は値で答え、不在と失敗への変換は呼び手の側の <- が defeffect の宣言に従って行う。関数が出す effect は手で書かずに doeff-effect-analyzer で推論し、関数の型を『戻り値 + 残りの effect の集合』として見せる"
  :status "proposed"
  :scope ["docs/adr/defadr_doeff_core_effects_003_absent_and_raise.hy"
          "packages/doeff-core-effects/doeff_core_effects/effects.py"
          "packages/doeff-core-effects/doeff_core_effects/handlers.py"
          "packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/declarations.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
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
       "doeff.result は Result と Maybe の値を持つ: Ok / Err は Rust の doeff_vm から再び出したもの(Ok は value、Err は error と captured_traceback を持つ)。Some は value を持つ不変の値、Nothing は偽と評価される 1 つだけの値で、Maybe = Some | Nothing。doeff の最上位からも同じ名で出る。"
       :evidence "doeff/result.py:19(Ok / Err)・:22-54(Some)・:57-85(Nothing — :78-79 の __bool__ は False)・:94(Maybe)・doeff/__init__.py:52-56")
     (fact
       "Try(program) は、program の中で上がった Python の例外を Err に、成功を Ok に畳む effect で、try_handler が受ける。try_handler は内側の handler を付け直して program を走らせ、except Exception で Err(e) を返す — 例外を値に写す口であって、値として返った答えの中の失敗(Unreachable など)は見ない。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/effects.py:113-124(Try)・packages/doeff-core-effects/doeff_core_effects/handlers.py:126-164(try_handler — :153-156 が except Exception → Err)・doeff/__init__.py:68")
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
       "defeffect の頭の辞書が受けるキーは :fields・:answer・:tags・:pre で、答えは :answer の 1 つの型(union でよい)として __doeff_answer__ に残る。union のどの型が成功・不在・失敗かを宣言する場所は無い。doeff-records の effect は defeffect でなく素の defclass で書かれている。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy:94-95(EFFECT-KEYS)・:126-158(defeffect-form — :156 が __doeff_answer__)・packages/doeff-records/src/doeff_records/effects.hy:39")
     (fact
       "handler の節の中で出した effect は、その handler より外側の handler へ行き、本文とその handler の間に置いた受け手を飛び越える — この ADR のテスト effect-performed-in-a-handler-skips-the-callers-scope で確かめた(本文の側の maybe の受け手でなく、土台の handler より外の受け手が受けた)。"
       :evidence "この ADR の probe-read-handler・probe-maybe-handler・probe-outer-handler とテスト")
     (fact
       "defhandler の節は、すべての分かれ道で resume / transfer / reperform / raise のどれかに届かないと、展開の時に SyntaxError で断られる。defk は引数の名が (effect k) の形だと handler と読んで断る。一方 VM は、再開しないで値を返す handler を許し、その値でスコープを終える(続きは捨てられる)。続きを捨てて値で終える受け手は、今は理由の註つきの deff(素の handler 関数)でしか書けない。"
       :evidence "packages/doeff-hy/src/doeff_hy/handle.hy:13-17(節の終わりの操作)・:31-32・:223-266(_terminates)・:268-273(_check-clause-terminates)・packages/doeff-hy/src/doeff_hy/macros.hy:182-190(_reject-handler-signature)・この ADR のテスト handler-without-resume-ends-the-scope と defhandler-clause-must-resume")
     (fact
       "defk・deff・defp・defhandler の頭の辞書は既に :effects(その定義が出す effect の型の名の list・任意)を受け、__doeff_effects__ に残す(書かなければ None)。この宣言を読んで推論と照らす道具は無い(読むのは doeff-hy のテストだけ)。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy:1-13・:26(CONTRACT-KEYS)・:59-70(effects-form)・git grep '__doeff_effects__'(当たりは declarations.hy と packages/doeff-hy/tests/test_declarations.py だけ)")
     (fact
       "doeff-effect-analyzer の Python の入口は、Program の関数が出す effect を、呼び先を推移的にたどって集める。Hy は macro を展開してから読み、defhandler の節(isinstance の分かれ道)から handler が受ける effect を読み、追えない物は unresolved と報告して黙って落とさない。引数で渡された Program(Spawn の子など)は carried として別に報告し、畳むかどうかは呼び手が決める。module の cache は process の中だけ。"
       :evidence "packages/doeff-effect-analyzer/README.md:7-58・python/doeff_effect_analyzer/program_effects.py:159-180(_MODULE_CACHE)・:590(analyze_program)")
     (fact
       "ADR-DOE-CLUSTER-001 は、job の runner が handler を 1 つも足さないこと(R2)と、記録と再生の handler を Program の中の翻訳の handler と土台の handler の間に置き、境目の答えを書き留めること(R5・R5b)を決めている。"
       :evidence "docs/adr/defadr_doeff_cluster_001_job_accepts_a_program.hy:102・:108-109")
     (fact
       "code-quality の方針は、未知の値を空文字・成功・既定の状態へ黙って変換しないことを求める。"
       :evidence "~/repos/code-quality/docs/policy.md:24-25")]
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
       "try-catch との比較(逐語 6『try-catch に似ているが差し替えられる』への答え):\n観点 | try-catch(例外) | Absent / Raise(この案)\n飛んでくる所 | どの行からでも(呼び先の奥の例外も) | Absent / Raise を出す行と、宣言で失敗を返す effect を受ける <- だけ\n何が飛ぶか | 書かれない(Java の検査例外は手書きで古くなる) | 推論した effect の集合に出る(R13)\n不在と失敗 | 区別しない(null か例外) | Absent と Raise で分け、組み合わせは入れ子の順で選ぶ\n既定値 | catch の中で値を返してスコープを終える | absent-as が既定値で再開できる\n方針の差し替え | catch は書いた所に固定 | 組み立ての handler で差し替える(本番と模擬で別)\n再生 | 例外は記録に残らない | handler は値で答え記録係が書き留めるので、再生でも同じ道を通る\n受け取り忘れ | 走らせて初めて分かる | job の入口の残りの effect で、書いた時点に拾える\n何でも受ける形 | catch (Exception) が書ける | on-raise は型のパターンだけ(何でも受ける形は linter の違反)\n見た目 | try … catch | on-raise … パターン — ほぼ同じ形なので、読み手は新しい考え方を覚えずに読める(利点)")
     (interpretation
       "書き方の例(file の頭の註): revise-input の After は成功の道だけを書き、不在と失敗を 1 つの on-raise で業務の答えの型へ畳む。外から見た型(戻り値と残りの effect)は Before と同じで、手で書いた :effects は推論に置き換わって消える。notify-landed の例は、業務の core に境目を書かず、1 件ごとの失敗の扱いを組み立ての handler に任せる形。")
     (interpretation
       "既定の受け手は Absent / Raise を受けて続きを捨てるので、受けた後では再試行も既定値での再開もできない(続きが無い)。再試行は答えがまだ失敗の値のうち — 値で答える土台の handler の中 — で行う。既定値は effect ならではの形(Absent を受けた handler が既定値で続きを再開する)で書けるが、『知らない値を黙って既定値にしない』に触れるので、既定値を与える handler は書いた人が明示した物に限る。doeff-traverse の normalize_to_none(Fail を黙って None で再開する)は、明示しないと起きる形の実例。")
     (interpretation
       "記録と再生の handler(ADR-DOE-CLUSTER-001 R5)は、読みの effect の本物の答え(Unreachable のような失敗の値も)を境目の下で書き留める。<- の変換は本文の中の決定的な計算なので、再生でも同じ答えから同じ Absent / Raise が出る。")
     (interpretation
       "関数が Absent・Raise を出すかどうかは、その関数が呼ぶ関数と、途中で置いた handler で決まる(逐語 4)。手で書いた :effects は、呼び先が変わると黙って古くなる。呼び先を推移的にたどる推論(doeff-effect-analyzer)なら古くならない。関数の型は、戻り値(中身だけ — Maybe / Result に包まない)と、推論した残りの effect の集合の組として見せる。これは Eff などの論文の計算の型 A ! Δ と同じ考え方(Koka では exn int のように effect の列を戻り値の前に書く)。")
     (interpretation
       "R13 のために analyzer に足りない物は 3 つ: (1) Program の中で置いた handler(with-handlers・maybe・result・on-raise・<- の :absent の受け手)が受ける effect を集合から引く処理 — 今は Program の effect を集めるだけで、handler を引くのは env の coverage の側だけ。(2) 引数で渡された Program(carried)を、渡した先の意味に従って呼び手の側で畳む処理 — 今は呼び手が effect_types_with で明示して畳む。(3) 結果を cache して linter とエディタが読む形 — Jev の cache と同じ扱いで、保存のたびに module を import しない。今の cache は process の中の module の cache だけ。")]
  :decision
    [(rule R1 "不在と失敗を、意味で分けた 2 つの effect にする。Absent(想定内の不在・Maybe の側)は中身を持たず、記録と調べ物のための説明の文だけを添える。Raise(e)(失敗・Result の側)は理由 e を持つ。2 つの間に継承の関係を作らない(isinstance で互いに捕まらない)。境目の handler のうち maybe・result・on-raise と組み立ての方針の handler は、答えを返さず続きを捨てる。続きを再開してよいのは R8 の absent-as だけ。文献の fail / empty(Maybe の側)と raise / throw(Result の側)に当たる。失敗の側を Fail と呼ばない — 文献では Maybe の側の名であり、doeff-traverse の Fail(再開できる失敗の知らせ)とも紛れる。置き場は Try と同じ doeff-core-effects。")
     (rule R2 "Absent・Raise の受け取り方は、外側のスコープの境目の handler が決める。(maybe …) は Absent を Nothing に、成功を Some に写す。(result …) は Raise(e) を Err(e) に、成功を Ok に写す。組み合わせの型は入れ子の順で選ぶ: (result (maybe …)) は Result の中に Maybe(Ok(Some v)・Ok(Nothing)・Err(e))、(maybe (result …)) は Maybe の中に Result(Some(Ok v)・Some(Err e)・Nothing)。組み立ての方針の handler(1 件を飛ばして記録する・全体を止める)も Raise を受けてよい — 飛ばすのは受けたスコープ 1 つ分で、続きは戻らない。受け手の無い Absent / Raise は、ほかの effect と同じく未処理の effect として止まる(黙って Nothing や None にしない)。runner・env の組は境目の handler を既定で置かない(ADR-DOE-CLUSTER-001 R2)。")
     (rule R3 "出す側は最初から Absent / Raise を出すのを既定にする(Koka と同じ素直な書き方)。defk の本文は直に (<- (Raise e)) / (<- (Absent 説明)) を出す — 本文は呼び手の動的なスコープで走るので、呼び手の maybe / result に届く。(<- x (危ない呼び)) の x は成功の値 T だけで、誰に呼ばれても『T か、逃げる』の 1 通り — 同じ行の意味が呼び手で変わらない。『成功だけを見る』範囲を宣言する構文(success-only)は置かない(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定。戻し方: 範囲の宣言の macro を足し、R3 をその形の文へ戻す)。")
     (rule R4 "effect に答える handler(ReadRow に答える土台の handler・翻訳の handler)は、節の中で Absent / Raise を出してはいけない。handler の節で出した effect はその handler より外側の handler へ行き、呼び手の本文のスコープ(maybe / result)を飛び越える:\n  組み立ての根: [外の受け手 … 土台の handler(ReadRow に答える)]   ← 外側\n    呼び手の本文: (result (maybe (<- row (ReadRow …))))              ← 内側\n  ReadRow は本文から外へ向かい、maybe・result を素通りして土台の handler に届く。土台の handler がそこで Absent を出すと、それは土台の handler より外へ行き、内側の maybe・result には戻らない。\nだから handler は今までどおり値で答え(記録係も値を書き留め、再生も変わらない)、呼び手の側の <- が defeffect の宣言(R5)に従って、不在の答えを Absent に、失敗の答えを Raise(答え)に変えて本文のスコープで出す。大事な落とし穴なので、doeff-linter の規則にする(R9 の 2 つ目の閉じる所と同じ規則)。")
     (rule R5 "何を成功・不在・失敗とみなすかは effect ごとの defeffect の宣言で決め、型の名から推し量らない(キーの名は実装で決める。例: {:answer (| Row Missing Unreachable) :absent [Missing] :failure [Unreachable]} — 残りが成功)。<- は宣言に従い、成功なら中身を束ね、不在なら Absent を、失敗なら Raise(答え)を出す。宣言の無い effect の答えは変換せず、答えの型のまま束ねる。業務で普通に扱う答えは失敗と宣言しない — 例: PutRow の Conflict(版の負け)は CAS の繰り返しで普通に扱う答えなので、答えの値として宣言できる。不在を値で受けたい所は、その行を maybe で包む((maybe (<- (ReadRow …))) は Maybe の値)。doeff-records の effect(今は defclass)は defeffect へ移してこの宣言を持つ(移し方は R14)。")
     (rule R6 "役を 2 つに分ける(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定)。開く(値 → effect): <- だけ — 宣言に従う変換(R5)、:absent <失敗>、Result / Option の値を渡すと開く形。畳む(effect → 値): 境目の handler maybe・result・on-raise・absent-as だけ。:absent <失敗> は <- の引数で、その 1 行の束ねを包む狭い受け手として、出た Absent を Raise(<失敗>) に変える(束ねる行と意味を同じ場所に置く。受け手は呼び手の本文の内側にあるので、出した Raise は呼び手の境目に届く)。受け取った Result / Option の値は、もう一度 <- に渡せば開ける(Ok・Some → 中身、Err(e) → Raise(e)、Nothing → Absent)— 値と effect を行き来でき、開くための別の構文は作らない。型(Option は doeff の Maybe = Some | Nothing):\n  (<- x (dangerous-call))      x : T\n  dangerous-call               : Program[T] ! {Raise[E], …}\n  (maybe body)                 : Program[Option[T]] ! {Raise[E], …}\n  (result body)                : Program[Result[T]] ! {Absent, …}\n  (result (maybe body))        : Program[Result[Option[T]]] ! {…}")
     (rule R7 "境目の on-raise は (on-raise 本文 パターン 写し先 …) の形 — 失敗の型のパターンと、それを写す業務の答えだけを並べる。何でも受ける形(型を問わないパターン・_・Exception)は作らず、doeff-linter の違反にする。パターンに合わない Raise は受けずに外へ渡す。Python の例外(実装の誤り)は on-raise が受けない(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定)。")
     (rule R8 "(absent-as 既定値 …) は、中で出た Absent を既定値で再開する(effect ならではの形 — Maybe を値で受けて既定値へ畳む手間が要らない)。『知らない値を黙って既定値にしない』(code-quality の方針)に触れるので、既定値を与える handler はその場に書いた (absent-as …) のような明示の物に限り、runner・env の組・土台の handler に既定値での再開を置かない。再開の値が型に合うのは、Absent が absent-as の字面の中の <- で値の代わりに出た時だけ — 呼んだ defk の奥で出た Absent に既定値を返すと、奥の行が別の型の値を受ける。コードに直に書いた (<- (Absent …)) を再開すると、不在を前提にしない続きが走る。この 2 つの場合は再開せず、既定値で absent-as のスコープを終える。この境目は提案の時点の疑問として agora-redesign #836 に残す。")
     (rule R9 "境目の handler(maybe・result・on-raise・absent-as)を書くのは、不在か失敗に対して何かしたい所だけ。それ以外は成功の道だけを書き、Absent / Raise は外へ逃がす(try-catch を受け止めたい所にだけ書くのと同じ)。ただし構造上必ず閉じる所が 2 つある。(1) job の入口 — runner は handler を足さない(ADR-DOE-CLUSTER-001 R2)ので、service の本体の方針の handler(飛ばして記録・止める等)で閉じる。確かめ: job の Program の推論した残りの effect が空。(2) handler の節の中で Program を走らせて答える所(翻訳の handler)— handler の中から逃げた Raise は handler より外側へ行き呼び手のスコープを飛び越える(R4)ので、答えの値(業務の effect の答えの型)に閉じてから返す。確かめ: handler の節で走らせる Program の推論した残りの effect に Absent / Raise が無い — doeff-linter の新しい規則の候補(番号は未定)(2026-09-27・f143ee92 の席が推奨どおり決めた戻せる決定)。")
     (rule R10 "再試行は Raise を受けてからではできない(続きが捨てられている)。答えがまだ失敗の値のうちに、値で答える土台の handler の中(Unreachable なら出し直す)で行う。")
     (rule R11 "Python の例外は実装の誤りのまま扱う(doeff-records の決まりと同じ)。想定内の不在・失敗を例外で表さない。Try(例外を Result に畳む)は今のまま残し、Absent / Raise と混ぜない。")
     (rule R12 "記録と再生との関係: handler は値で答え、記録係は境目の下で本物の答え(失敗の値も)を書き留める(ADR-DOE-CLUSTER-001 R5)。<- の変換は本文の中の決定的な計算なので、再生でも同じ答えから同じ Absent / Raise が出る。Absent / Raise を記録係へ届く汎用の effect にしない(記録するのは読みの effect の答え)。")
     (rule R13 "関数がどの effect を出すか(Absent・Raise を含む)は、手で書かずに doeff-effect-analyzer で推論する(呼び先を推移的にたどり、Hy は macro を展開してから読み、defhandler の節から受ける effect を読み、追えない物は unresolved と報告する)。関数の型は、戻り値(中身だけ・Maybe / Result に包まない)と推論した残りの effect の集合の組として見せる。表記の例: settle-landing : str → Program[Row] ! {Absent, Raise[Unreachable | Refused], ReadRow, PutRow}(Eff などの論文の計算の型 A ! Δ と同じ考え方)。handler で包むと、その handler が受ける effect が集合から消える。:effects(defk の頭の辞書に既にある任意のキー)は必須にしない。公開の境目にだけ上限として書き、推論 ⊆ 宣言を linter が確かめる(宣言が古くならない)。使い道: エディタの hover で残りの effect を出す/job の入口の Program の残りの effect が空であることを書いた時点で確かめる(R9 の 1 つ目)/core の定義の残りの effect に汎用の effect(通信・記録)が入れば層の違反として出す。analyzer に足りない物は :context の最後の interpretation。")
     (rule R14 "移し替え(計画): 今の doeff-records の effect は失敗を値で返す決まり(README『失敗の答え(値で返す)』)。宣言を切り替えると、(match body (Missing) …) の既存の呼び手は Missing を見なくなる(決して通らない分岐が残る)。だから effect ごとに宣言を切り替え、古い分岐の残る呼び手を doeff-linter が拾い、型の決まった書き換えとして進める。一度に全部の effect を切り替えない。")]
  :laws
    [(law absent-and-raise-are-distinct
       :statement "Absent は中身を持たない(説明の文だけ)∧ Raise は理由を持つ ∧ Absent と Raise は継承の関係を持たない ∧ for_all 境目の handler h ∈ {maybe, result, on-raise, 組み立ての方針の handler}: h は Absent / Raise を受けて続きを再開しない"
       :counterexamples
         [(counterexample "不在も失敗も 1 つの Fail で出す — 受け手が不在と失敗を分けられず、Result の中の Maybe が作れない")
          (counterexample "Raise を Absent の子の型にする — (maybe …) が失敗まで Nothing に潰し、理由が消える")
          (counterexample "失敗の側を Fail と名づける — doeff-traverse の Fail(None で再開できる)と同じ名で、続きを捨てるかどうかが名前から読めない")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 提案の状態で、Absent / Raise も境目の handler もまだ無い")
     (law fold-handlers-choose-the-shape
       :statement "for_all スコープ s: s の外へ出る値の形は、s を囲む境目の handler の入れ子の順だけで決まる — (result (maybe b)) : Result[Maybe[T]] ∧ (maybe (result b)) : Maybe[Result[T]] ∧ 受け手の無い Absent / Raise は未処理の effect として止まる ∧ runner・env の組は境目の handler を置かない"
       :counterexamples
         [(counterexample "runner や env の組が既定で maybe を置く — Program を読んでもどの形で返るか分からない(ADR-DOE-CLUSTER-001 R2 と同じ害)")
          (counterexample "受け手の無い Raise を最上位で黙って None にする — 失敗が成功の値に化ける")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 提案の状態で、境目の handler がまだ無い。defhandler の節は続きを捨てて値で終える形を今は断る(テスト defhandler-clause-must-resume)")
     (law a-bind-has-one-meaning
       :statement "for_all 束ね (<- x e) の行 l: x の型は e の宣言だけで決まる成功の型 T で、l を呼ぶ側に置いた handler では変わらない — 不在・失敗は x に入らず Absent / Raise として逃げる"
       :counterexamples
         [(counterexample "呼び手の handler で答えの型を切り替える — ある呼び手では Row、別の呼び手では Row | Missing | Unreachable が x に入り、中の (. row value) が壊れる")
          (counterexample "『成功だけを見る』範囲を宣言する構文(success-only)を足す — 範囲の内と外で同じ行の型が変わり、書き方が 2 つ並ぶ")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 提案の状態で、<- の変換がまだ無い")
     (law handlers-answer-with-values
       :statement "for_all effect E に答える handler h: h の節は Absent / Raise を出さず、E の答えの値で答える ∧ 不在と失敗への変換は呼び手の本文の <- が行う"
       :counterexamples
         [(counterexample "ReadRow に答える土台の handler が、行が無い時に (<- (Absent 行が無い)) を出す — Absent は土台の handler より外へ行き、呼び手の maybe を飛び越えて未処理になる(または無関係の外の受け手に捕まる)")
          (counterexample "翻訳の handler が節の中で業務の Program を走らせ、その Program の Raise を閉じずに返す — 同じく呼び手の result を飛び越える")
          (counterexample "記録係が Raise を書き留める形にする — 答えの値が記録に残らず、再生で同じ変換を通れない")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— doeff-linter の規則(handler の節で走らせる Program の残りの effect に Absent / Raise が無い)は候補で番号は未定。VM の振る舞いはテスト effect-performed-in-a-handler-skips-the-callers-scope が見せる")
     (law outcomes-are-declared-on-the-effect
       :statement "for_all effect の型 E, 答えの型 t ∈ answer(E): t が成功・不在・失敗のどれかは E の宣言(defeffect)から読める ∧ <- の変換はその宣言だけに従う(型の名を読まない)"
       :counterexamples
         [(counterexample "型の名に Missing・NotFound が含まれたら不在と推し量る — 名前を変えると意味が変わり、失敗の名を持つ新しい型は見落とす")
          (counterexample "PutRow の Conflict を一律に失敗と決める — CAS の繰り返しで普通に扱う答えまで Raise になり、呼び手が毎回 on-raise で値へ戻す")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— defeffect はまだ :answer の 1 つの型しか持たない")
     (law on-raise-has-no-catch-all
       :statement "for_all on-raise o: o のパターンはどれも失敗の型のパターン(型を問わない形・_・Exception を含まない)∧ o は Python の例外を受けない ∧ パターンに合わない Raise は o の外へ渡る"
       :counterexamples
         [(counterexample "何でも受ける on-raise(パターン _ で全部を WriteUnreachable にする)— 新しい失敗の型が増えても黙って同じ答えに潰れ、推論した effect の集合にも出ない")
          (counterexample "on-raise が Python の例外も受ける — 実装の誤りが業務の失敗の答えに化ける")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— on-raise も doeff-linter の規則もまだ無い")
     (law defaults-are-explicit-handlers
       :statement "for_all Absent を既定値で再開する handler h: h はその場に書いた明示の物(absent-as)∧ h が再開するのは absent-as の字面の中の <- が値の代わりに出した Absent だけ ∧ runner・env の組・土台の handler は Absent を再開しない"
       :counterexamples
         [(counterexample "土台の handler が Absent を受けたら空文字で再開する — 呼び手の知らない所で不在が値に化ける(doeff-traverse の normalize_to_none と同じ形)")
          (counterexample "(absent-as 0 …) が、呼んだ defk の奥の (<- row (ReadRow …)) で出た Absent を 0 で再開する — 奥の行が Row の代わりに 0 を受ける")
          (counterexample "(absent-as 0 …) が、コードに直に書いた (<- (Absent 説明)) を 0 で再開する — 不在を前提にしない続きが走る")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 提案の状態で、absent-as がまだ無い。明示でない既定値の再開を見つける linter の規則も未定")
     (law scopes-appear-only-where-failure-is-handled
       :statement "for_all 境目の handler の置き場 b: b では不在か失敗に対して何かをする ∧ for_all job の Program j: 推論した残りの effect(j) = ∅(方針の handler で閉じる)∧ for_all handler の節で走らせる Program p: Absent ∉ 残り(p) ∧ Raise ∉ 残り(p)"
       :counterexamples
         [(counterexample "全部の呼び出しを result で包む — 成功の道だけのコードに Result の値の分岐が戻り、try-catch を全行に書くのと同じになる")
          (counterexample "handler の節で走らせた Program の Raise が漏れる — 呼び手のスコープを飛び越えて未処理になる")
          (counterexample "job の入口で閉じない — runner は handler を足さないので、Raise が未処理の effect として job を止める")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— analyzer は Program の中の handler を引かず、doeff-linter の規則も候補で番号は未定")
     (law retry-happens-before-raise
       :statement "for_all 再試行 r: r は答えがまだ失敗の値のうちに、値で答える土台の handler の中で行う — Raise を受けた handler は再試行しない(続きが無い)"
       :counterexamples
         [(counterexample "(result …) の受け手が Err を見て同じ Program をもう一度走らせる — Program の前半の effect(書き)も繰り返される")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 提案の状態で、機械で確かめる物は無い")
     (law effects-are-inferred-not-hand-written
       :statement "for_all 定義 f: f の出す effect の集合は推論(doeff-effect-analyzer)で得る ∧ f に :effects があれば 推論(f) ⊆ 宣言(f) を linter が確かめる ∧ :effects の無い f は違反ではない"
       :counterexamples
         [(counterexample "全部の defk に :effects を書かせる — 呼び先が変わるたびに黙って古くなり、書き手に推論の代わりをさせる")
          (counterexample "宣言の :effects だけを信じて推論と照らさない — 呼び先が Raise を出し始めても宣言は変わらず、job の入口の確かめが偽の緑になる")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— analyzer は Program の中の handler を引かず、結果を道具が読む cache も無い。推論 ⊆ 宣言 の linter の規則は未定")
     (law declarations-switch-one-effect-at-a-time
       :statement "for_all effect E の宣言を切り替えた版: E の不在・失敗の答えの型を値として match する呼び手は 0(doeff-linter が拾う)∧ 1 つの版で切り替える effect は、その呼び手を同じ版で書き換え終えた物だけ"
       :counterexamples
         [(counterexample "ReadRow の宣言を切り替えたのに (match body (Missing) …) の分岐が残る — 決して通らない分岐が残り、読み手は不在がそこで扱われていると誤る")
          (counterexample "doeff-records の 7 つの effect の宣言を一度に切り替える — 書き換えの漏れが一度に全部の呼び手へ広がる")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 古い分岐を拾う doeff-linter の規則は未定")]
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
       ;; 事実: ReadRow の答えは成功・不在・失敗が 1 つの union に並ぶ(R5 の宣言が要る理由)。
       (assert (= (get-args ReadRowAnswer) #(Row Missing Unreachable)))
       (val readme (.read-text (/ REPO-ROOT "packages/doeff-records/README.md") :encoding "utf-8"))
       (assert (in "失敗の答え" readme))
       (assert (in "値で返す" readme))
       (assert (in "例外で上がるのは組み立ての誤り" readme)))
     (deftest test-adr-doe-core-effects-003-handler-without-resume-ends-the-scope
       ;; 事実: VM は、再開しないで値を返す handler を許し、その値でスコープを終える(続きは捨てられる)
       ;; — R1 の Absent と R2 の maybe の形は、今の VM で書ける。
       (val seen [])
       (assert (is (run (with-handlers [probe-maybe-handler] (probe-reads-then-continues seen))) Nothing))
       (assert (= seen []) "受け手が再開しないので、Absent の後ろは走らない"))
     (deftest test-adr-doe-core-effects-003-effect-performed-in-a-handler-skips-the-callers-scope
       ;; 事実(R4 の落とし穴): handler の節の中で出した effect は、その handler より外側へ行く。本文の側に置いた
       ;; maybe の受け手(本文と土台の handler の間)は飛び越えられ、土台の handler より外の受け手が受ける。
       (val answer (run (with-handlers [probe-outer-handler probe-read-handler]
                          (with-handlers [probe-maybe-handler] (probe-reads-a-row)))))
       (assert (= answer "外側が受けた") answer))
     (deftest test-adr-doe-core-effects-003-defhandler-clause-must-resume
       ;; 事実: defhandler の節は resume / transfer / reperform / raise に届かないと展開の時に断られる。
       ;; 続きを捨てて値で終える受け手(R2 の maybe・result・on-raise)は、今は defhandler で書けない。R2 を実装する時は
       ;; 節の終わりの操作を足し、このテストを書き換える。
       (var refused False)
       (try
         (_check-clause-terminates "ProbeAbsent" [(hy.models.Symbol "Nothing")])
         (except [SyntaxError]
           (:= refused True)))
       (assert refused "値を返すだけの節は断られる")
       (_check-clause-terminates "ProbeAbsent" [(hy.read "(resume None)")]))
     (deftest test-adr-doe-core-effects-003-traverse-fail-resumes-with-a-substitute
       ;; 事実: doeff-traverse の Fail は再開できる失敗の知らせで、normalize-to-none は None で続きを再開する
       ;; — R1 が失敗の側を Fail と呼ばない理由で、R8 が既定値の再開を明示の handler に限る理由でもある。
       (assert (= (run (normalize-to-none (probe-fails-then-continues))) #("続いた" None))))
     (deftest test-adr-doe-core-effects-003-declarations-carry-one-answer-and-optional-effects
       ;; 事実: defeffect は :answer の 1 つの型だけを持ち(成功・不在・失敗の区分は無い — R5)、
       ;; defk の :effects は任意で、書けば __doeff_effects__ に残り、書かなければ None(R13)。
       (assert (in ":answer" EFFECT-KEYS))
       (assert (is (. ProbeAbsent __doeff_answer__) Never))
       (assert (= (. probe-declared-effects __doeff_effects__) #(ProbeAbsent)))
       (assert (is (. probe-reads-then-continues __doeff_effects__) None)))]
  :plans ["agora-redesign #836"])
