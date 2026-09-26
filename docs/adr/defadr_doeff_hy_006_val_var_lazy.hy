;;; Executable ADR: val・var・lazy val・lazy var・session val・session var と (:= x 値)。
;;; 名前の束縛は「一度だけ(val)」を既定にし、書き換える名前は var と宣言させる。値の遅延(lazy — 1 回の
;;; 呼び出しの中)とセッションで共有する値(session — defhandler だけ・状態の効果 Get / Put を通す)を別の語に分ける。
;;; 設計の記録 = docs/design/defk-val-var-lazy/design.md。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk defp <- defhandler])
(import doeff [run EffectBase Some])
(import doeff_core_effects [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_hy.session [session-key])
(import dataclasses [dataclass])
(import types)
(import hy)
(import hy.errors)
(import pytest)

;; ---------------------------------------------------------------------------
;; 生きた probe — enforcement の deftest が実行する再現体。Probe の実行は probe-handler が log に残す。
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Probe [EffectBase]
  #^ str tag)

(defhandler probe-handler [log]
  (Probe [tag] (.append log tag) (resume tag)))

;; probe 1: lazy val — 使った回数によらず初期値の式は高々 1 回(使わなければ 0 回)
(defk lazy-uses [uses]
  {:pre [(: uses int)] :post [(: % list)]}
  (lazy val client !(Probe "connect"))
  (var seen [])
  (for [_ (range uses)]
    (:= seen (+ seen [client])))
  seen)

;; probe 2: lazy var — 初めて使う前に書き換えたら初期値の式は評価しない
(defk lazy-var-given [given]
  {:pre [(: given bool)] :post [(: % str)]}
  (lazy var name !(Probe "default"))
  (when given
    (:= name "given"))
  name)

;; probe 3: session val / session var — セッションの値は状態の効果(Get / Put)を通る
(defclass [(dataclass :frozen True)] Next [EffectBase])

(defhandler counter-handler
  (session var count 0)
  (Next []
    (:= count (+ count 1))
    (resume count)))

(defp two-nexts
  {:post [(: % list)]}
  (<- a (Next))
  (<- b (Next))
  [a b])

(defk expansion-error [code]
  {:pre [(: code str)] :post [(: % str)]}
  "Hy の断片の展開が誤りになることを確かめ、その文を返す。"
  (val module (types.ModuleType "_adr_doe_hy_006_probe"))
  (with [caught (pytest.raises hy.errors.HyMacroExpansionError)]
    (hy.eval (hy.read-many (+ "(require doeff-hy.macros [defk])\n" code)) :module module))
  (str caught.value))


(defadr ADR-DOE-HY-006
  :title "defk・deftest・defhandler の本体と module の直下で、名前の束縛を val(一度だけ)・var(:= で書き換える)・lazy val / lazy var(その呼び出しの中で初めて使った時に評価)で書く。セッションで共有する値は defhandler の session val / session var に置き、状態の効果(Get / Put)を通す。旧い lazy-val / lazy-var / set! は defhandler では動きを変えず移行を警告し、defk では誤り"
  :status "accepted"
  :scope ["packages/doeff-hy/src/doeff_hy/binding_forms.py"
          "packages/doeff-hy/src/doeff_hy/lazy.py"
          "packages/doeff-hy/src/doeff_hy/session.py"
          "packages/doeff-hy/src/doeff_hy/macros.hy"
          "packages/doeff-hy/src/doeff_hy/handle.hy"
          "packages/doeff-hy/src/doeff_hy/static_check.py"
          "docs/adr/defadr_doeff_hy_006_val_var_lazy.hy"]
  :problem
    [(fact
       "operator の決定(2026-09-26): \"i do want scala-semantic lazy and val var for doeff defk/deftest, so user can do like (lazy val some_variable !(effectful-expr))\" / \"ah yes val as default and enforce var, that sounds good\" / \"1. setv should be warned to use var/val. 2. we need val,var,lazy val, lazy var\" / \"yeah session it is...\" / \"for defhandler, we want to keep lazyval/lazyvar behavior unaffected for now and warn to migrate to session val / session var\""
       :evidence "設計の記録 docs/design/defk-val-var-lazy/design.md §1")
     (fact
       "旧い lazy / lazy-val / lazy-var は defhandler でも defk でも「セッションをまたいで状態の効果に保存する値」の意味だった。defk の中の lazy は関数の約束に出ない隠れた状態で、新しい lazy val(その呼び出しの中の遅延)と同じ語で意味が衝突する。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy の defk(2026-09-26 以前の lazy の注入)・handle.hy の _build-lazy-init-forms")
     (fact
       "本線 6 repo(agora-controllers・proboscis-ema・doeff・agent-control-plane・argus・herdr-hud)の実測: defk・deftest の中の旧い lazy / set! は 0 件、defhandler の旧い lazy 節 115・set! を持つ節 61。defk 7,381 個のうち 1,155 個(15.6%)が同じ名前を束縛し直している。"
       :evidence "設計の記録 §10(新しい解析 binding_forms.rewrite_body を各 repo の origin/main にそのまま当てた)")
     (fact
       "Hy 1.3.0 の reader は !(f) を ! と (f) の 2 つの要素に読み、(:= x 1) の頭を Keyword として読む。module の直下の (:= x v) は macro を通らず keyword の呼び出しとして compile され、何も書き換えない。"
       :evidence "packages/doeff-hy/tests/test_val_var_lazy.py::test_bang_written_as_two_tokens_is_the_same_as_the_bang_form・設計の記録 §3")]
  :context
    [(interpretation
       "束縛し直しの誤りは、新しい構文が関わる所(val を 2 回・val の名前の setv など)は最初から展開の誤りにできる(既存の code に新しい構文は無い)。旧い書き方どうしの束縛し直しは既存の defk の 15.6% に在るので、いきなり展開の誤りにすると本線が壊れる — まず静的な検査の赤にし、件数が 0 になってから展開の誤りへ切り替える。")
     (interpretation
       "session の値を Python の隠れた場所に持たず状態の効果を通すと、外側の handler が同じキーの Get に答えて値を差し替え、Put を受けて書き込みを観測できる。キーを作る関数を 1 つ公開すれば、外の handler と検査が同じキーを引ける。")]
  :decision
    [(rule R1 "(val x 式) は一度だけの束縛。同じ名前をもう一度束縛する(val をもう一度・setv・<-・for・拡張代入)のは展開の誤り。値の中身の書き換え(添字・属性への代入・method)は対象外。(<- x 効果) は val の別の書き方。")
     (rule R2 "(var x 式) は (:= x 新しい値) でだけ書き換える(本体の中)。var の名前の setv と、val・session val・宣言の無い名前への := は展開の誤り。")
     (rule R3 "(lazy val x 式) はその呼び出し(defk の 1 回・deftest の 1 回・defhandler の節の 1 回の実行)の中で x を初めて使った時にだけ式を評価して覚える。使わなければ評価しない。初回が例外なら覚えず、次に使った時にもう一度評価する。(lazy var x 式) は加えて := で書き換えられ、初めて使う前に書き換えたら初期値の式は評価しない。")
     (rule R4 "lazy の参照は裸の名前で、macro が参照を書き換える。lazy の宣言は本体の一番外の並びにだけ書ける。yield できない所(fn・内包表記・入れ子の定義・handle / defhandler の節)の参照、宣言より前の参照、同じ名前の影は展開の誤りで、「先に (val v x) で取り出す」と案内する。")
     (rule R5 "宣言と := の値の部分の `! 式`(reader が !(f) を 2 つの要素に読んだ形)は (! 式) と同じに受ける。")
     (rule R6 "旧い書き方どうしの束縛し直し(setv・<-・拡張代入で同じ名前を 2 回 — for の変数と互いに排他な枝どうしは数えない)は doeff-hy-check の赤(doeff-hy-rebind)。展開の誤りへの切り替えは、対象の各 repo の本線で doeff-hy-rebind が 0 件になってから(手順 = 設計の記録 §8.3)。")
     (rule R7 "defk・deftest・defhandler の節の本体の setv と、module の直下の setv(名前を束縛する物)は doeff-hy-check の警告(doeff-hy-setv)で val か var を勧める。展開は止めない。")
     (rule R8 "(session val x 式) / (session var x 式) は defhandler の直下にだけ書ける。値は状態の効果 Get / Put を通して読み書きし、キーは doeff_hy.session.session_key(module, handler, name) = \"<module>/<handler>/<name>\"(旧い lazy-val / lazy-var と同じ文字列)。session var の := は保存先へ書き戻す(Put)。defk・deftest の session は展開の誤り。")
     (rule R9 "defhandler の旧い lazy / lazy-val / lazy-var / set! は動きを一切変えず、展開の時の DeprecationWarning と doeff-hy-check の警告(doeff-hy-legacy-lazy)で session val / session var / := への移行を案内する。defk・deftest の旧い lazy / lazy-val / lazy-var / set! は展開の誤り。")
     (rule R10 "module の直下では (val x 式)・(var x 式)・(lazy val x 式) を書ける(require doeff-hy.macros [val var lazy])。lazy val は効果を使わない式だけ(効果を使う式は誤りで、defhandler の session val を案内する)。module の直下の lazy var・session・効果を使う val / var の式・同じ名前の 2 回目は展開の誤り。module の var は module の直下の setv で書き換える(Hy では module の直下の := が macro を通らない)。")]
  :laws
    [(law lazy-val-evaluates-at-most-once-per-call
       :statement "for_all n >= 0: effects(call(lazy-uses n)) == (if (= n 0) [] [\"connect\"]) — 呼び出しごとに初期値の式は高々 1 回・使わなければ 0 回"
       :counterexamples
         [(counterexample "旧い defk の lazy はセッションに保存したので、同じセッションの 2 回目の呼び出しでは初期値の式が走らず、引数で変わる値にも最初の値を使い回した")
          (counterexample "書いた所で評価する setv では、使わない枝でも効果が走る")])
     (law session-values-flow-through-state-effects
       :statement "for_all handler h with (session var x e): the value of x is read by (Get (session-key module h x)) and written by (Put (session-key module h x) (Some v)) — Python の隠れた場所に持たない"
       :counterexamples
         [(counterexample "handler の閉包や module の変数に値を持つと、外側の handler が値を差し替えることも書き込みを観測することもできない")])
     (law new-syntax-rebinding-is-loud
       :statement "for_all name declared by val / var / lazy / session: a second binding of name (other than := on a var) => expansion-time SyntaxError with fix prompt"
       :counterexamples
         [(counterexample "(val x 1) (setv x 2) — 黙って通すと val の約束が破れる")])]
  :enforcement
    [(deftest test-adr-doe-hy-006-lazy-val-at-most-once
       (for [#(uses expected) [#(0 []) #(1 ["connect"]) #(3 ["connect"])]]
         (setv log [])
         (setv seen (run ((probe-handler log) (lazy-uses uses))))
         (assert (= (len seen) uses))
         (assert (= log expected) (+ "初期値の式の実行の回数が違う: " (str log)))))
     (deftest test-adr-doe-hy-006-lazy-var-assigned-before-use-skips-init
       (setv log [])
       (assert (= (run ((probe-handler log) (lazy-var-given True))) "given"))
       (assert (= log []) "初めて使う前に書き換えたのに初期値の式が走った")
       (setv log2 [])
       (assert (= (run ((probe-handler log2) (lazy-var-given False))) "default"))
       (assert (= log2 ["default"])))
     (deftest test-adr-doe-hy-006-session-var-uses-the-state-key
       (setv key (session-key __name__ "counter-handler" "count"))
       (assert (= (run (scheduled ((state) (counter-handler two-nexts)))) [1 2]))
       (assert (= (run (scheduled ((state :initial {key (Some 10)}) (counter-handler two-nexts)))) [11 12])
               "session var が状態の効果のキーから読まれていない"))
     (deftest test-adr-doe-hy-006-val-twice-is-an-expansion-error
       (<- message (expansion-error "(defk f [x] {:pre [(: x int)] :post [(: % int)]} (val y 1) (val y 2) y)"))
       (assert (in "(val y …) で宣言済み" message) message))
     (deftest test-adr-doe-hy-006-legacy-lazy-in-defk-is-an-expansion-error
       (<- message (expansion-error "(defk f [] {:pre [] :post [(: % str)]} (lazy-val c \"c\") c)"))
       (assert (in "(lazy val c 式)" message) message)
       (assert (in "(session val c 式)" message) message))]
  :plans ["docs/design/defk-val-var-lazy/design.md"])
