;;; Executable ADR: defk/do! 本体の statement 位置で評価された Program/EffectBase を
;;; 黙って破棄することを禁止する(guard-error)。auto-bind は採用しない。
;;; エラーメッセージはエージェント書き手向けの修正プロンプトとして設計する。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk])
(import doeff [run])
(import pytest)


;; ADR-DOE-HY-001 反例の生きた再現体(enforcement が run する)。
;; `(_probe-inner)` は statement 位置の bare kleisli 呼び出し — Program を生成して破棄する。
(defk _probe-inner []
  {:pre [] :post [(: % int)]}
  1)

(defk _probe-outer []
  {:pre [] :post [(: % int)]}
  (_probe-inner)  ;; ← 罠: 現行マクロは「Plain statement — emit as-is」で素通しする
  2)


(defadr ADR-DOE-HY-001
  :title "statement-position bind guard: defk/do! 本体の statement 位置で評価された値が Program(DoExpr)/EffectBase であるとき、マクロが挿入する runtime guard が即時 RuntimeError を送出する(guard-error)。auto-bind(Haskell do 記法式の自動束縛)は採用しない — 書き手はエージェントであり、必要なのは書き味ではなく決定的で行動可能な赤である"
  :status "proposed"
  :scope ["packages/doeff-hy/src/doeff_hy/macros.hy"
          "docs/adr/defadr_doeff_hy_001_statement_bind_guard.hy"]
  :problem
    [(fact
       "現行マクロは defk/do! 本体の非最終 statement 位置の式を『Plain statement (setv, when, for, etc.) — emit as-is』として素通しする。bare kleisli 呼び出しは Program を生成して黙って捨て、実行も例外も起きない。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:623, 748, 1069(2026-07-14 時点)")
     (fact
       "既存 guard `_guard-performed` の守備範囲は『最終式が bare EffectBase』のみ・実行時のみ。DoExpr/Program は docstring で明示的に対象外(『The Program-return composition pattern returns a DoExpr ... so this never fires on it』)。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:300-317")
     (fact
       "下流実害: agent-control-plane の deff→defk 一斉移行計画は silent-passthrough(bind 忘れ → Program が走らず raise もしない)を非自明 gotcha として列挙。逆方向の色エラー(Program-as-value の過剰 bind)は 2026-07-13 に deftest/CI green のまま本番 supervisor を crash-loop させた。"
       :evidence "agent-control-plane ADR 0056 移行計画(facts a–p); ACP hotfix commit 2026-07-13『K2 潜伏欠陥: Program-as-value の過剰 bind を復元 — dogfood 停止 incident』")
     (fact
       "本リポジトリの開発様式: コードの書き手は常にエージェントであり、人間はレビュアーとしてのみ関与する(2026-07-14 maintainer 裁定)。エージェントは沈黙の意味論から学習せず、行番号と修正テンプレを含む決定的な赤に最も確実に反応する。"
       :evidence "2026-07-14 doeff 投資計画議論(docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md)")]
  :context
    [(interpretation
       "doeff は sync/async の色を消したのではなく『Program か値か』という新しい1色に統一した。bind 忘れ・過剰 bind はこの色の色エラーであり、async/await と違い構文が強制しないため、マクロ層(構文木を見られる唯一の層)が守るのが所有レイヤとして正しい。Python 側 linter での検出は型推論を要し偽陰性を作る — Hy が第一級言語である以上、防衛線はマクロに置く。")
     (interpretation
       "auto-bind は書き味では優るが二重に不適: (1) 休眠していた bare form が導入日に『そのファイルに一切 diff がないまま』動き出す挙動変更であり、(2) エージェント書き手に教育信号を一切与えない。guard-error はエージェントの実行ループ内で失敗し、そのメッセージがそのまま修正プロンプトになる。")
     (interpretation
       "静的(展開時)検出はリテラルな EffectBase コンストラクタ等の狭いケースにしか健全でない。動的言語で偽陰性の混じる静的検査より、一様で決定的な runtime guard(statement 1つあたり isinstance 1回)を選ぶ。")]
  :decision
    [(rule R1 "defk/do! 本体の statement 位置(束縛・return・既知の制御形以外)で評価された値が DoExpr/Program または EffectBase であるとき、マクロが挿入する runtime guard が即時 RuntimeError を送出する。")
     (rule R2 "auto-bind は採用しない。値を捨てて実行したい場合の正式な形は明示 discard-bind `(<- _ expr)` である。")
     (rule R3 "guard のエラーメッセージは修正プロンプト形式とする: 関数名、ファイル:行、評価された型名、修正テンプレ『(<- _ (<expr>))』を必ず含む。エラーメッセージは人間向け説明ではなくエージェント向け修正指示である。")
     (rule R4 "setv/print 等、Program でも EffectBase でもない値の statement は無傷で素通しする(guard は isinstance 検査のみ)。")
     (rule R5 "消費リポジトリへの展開は guard 有効化 → 休眠 discard の全数洗い出し・修正(計画 A2)を経る。洗い出し完了までは guard を doeff 本体と opt-in 消費者に限定してよい。")]
  :laws
    [(law statement-discard-impossible
       :statement "for_all defk_or_do!_body: evaluated_at_statement_position(v) AND isinstance(v, DoExpr | EffectBase) => raises(RuntimeError); silent_discard_count == 0"
       :counterexamples
         [(counterexample "中間 statement の bare kleisli 呼び出し `(notify-user order receipt)` — 通知 Program が生成・破棄され、通知は永遠に飛ばず、テストも例外も出ない(現行挙動)")
          (counterexample "do! の最終式手前の bare `(LaunchEffect ...)` — _guard-performed は最終式しか見ないため素通り")])]
  :enforcement
    [(deftest test-adr-doe-hy-001-bare-statement-program-raises
       ;; RED(2026-07-14): 現行マクロは _probe-outer の bare (_probe-inner) を黙って捨て、
       ;; run は 2 を返して成功する。R1 実装後、guard が RuntimeError を送出して green。
       (with [(pytest.raises RuntimeError)]
         (run (_probe-outer))))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
