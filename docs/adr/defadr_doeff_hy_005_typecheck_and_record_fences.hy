;;; Executable ADR: Hy の柵の残り 2 本 —
;;;   柵 3 TypeCheck の effect: 静的の型検査は常時・実行時の検査は handler で
;;;        切り替える(既定 off・検で on)。
;;;   柵 5 記録の足場の macro: 手番の記録の書き手が使う macro は doeff-hy に
;;;        投影の規則と一緒に置き、各 repo に macro を生やさない。
;;;
;;; 出自 = agora-redesign 段 7 lane 7c(決定 1.4 の残り・
;;; `docs/plans/decisions-merge-2026-09-12.md`)。柵 1(型つき `<-` と defk の
;;; 署名)は段 0 で着地、柵 2(`"value"`・Any・object・裸の dict / list の禁止)と
;;; pure 層の I/O import の lint は共通の品質検査の hy ルールで運転中。
;;;
;;; 既知の形: algebraic effects(要求 = 値・handler = 実行の家・composition
;;; root が家を選ぶ)。柵 3 は「実行時の検査」をその形の中へ入れる変更で、
;;; 新しい仕組みを足していない。
;;;
;;; 語彙: ここは「道具」の層。手番(turn)は仕組みの語として記録の型名
;;; (TurnRecord)にだけ現れ、domain の言葉には持ち込まない。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <-])
(import doeff [do run])
(import json)
(import re)
(import pathlib [Path])

(import doeff_hy.typecheck [TypeCheckError type-check type-check-verdict])
(import doeff_hy.typecheck_handlers [run-with-type-checks run-without-type-checks])


;; ---------------------------------------------------------------------------
;; 生きた probe — 境界の値を要求として確かめる program(柵 3 の使い方の実演)。
;; ---------------------------------------------------------------------------

(defk agreeing-boundary [value]
  {:pre [(: value int)]
   :post [(: % int)]}
  "境界の約束(int)と要求の期待(int)が一致している program。"
  (<- _ (type-check "agreeing-boundary" "value" int value))
  value)


(defk mismatched-boundary [value]
  {:pre [(: value int)]
   :post [(: % int)]}
  "境界の約束(int)と要求の期待(str)が食い違っている program。

   defk の契約は「呼び手との約束」で、TypeCheck の要求は「境界で現に受けた値」
   を見る別の軸 — 食い違いは off では通り、on では赤になる。"
  (<- _ (type-check "mismatched-boundary" "value" str value))
  value)


;; ---------------------------------------------------------------------------
;; macro の凍結台帳 — `defmacro` を定義してよい file(2026-09-12 の実測)。
;;
;; 新しい macro は doeff-hy にだけ置く(柵)。既存の所有者は凍結し、名簿の
;; 外の file が `defmacro` を持ったら赤。doeff-hy の中で macro を増やすのは
;; 自由(そこが macro の家)。
;; ---------------------------------------------------------------------------

(setv MACRO-OWNER-ROSTER
  #{"packages/doeff-hy/src/doeff_hy/macros.hy"
    "packages/doeff-hy/src/doeff_hy/handle.hy"
    "packages/doeff-hy/src/doeff_hy/conductor.hy"
    "packages/doeff-hy/src/doeff_hy/record.hy"
    "packages/doeff-adr/src/doeff_adr/macros.hy"
    "packages/doeff-domain/src/doeff_domain/macros.hy"
    "packages/doeff-docker/src/doeff_docker/compose.hy"})

;; 走査から除く木(一時複製・生成物・環境)。
(setv SCAN-SKIP-PARTS
  #{".git" ".venv" ".claude" ".worktrees" "__pycache__" "node_modules"
    "dist" ".mypy_cache" ".pytest_cache" "scratchpad" "attic"})


(defn #^ tuple macro-owning-files [#^ Path repo-root]
  "`defmacro` を持つ .hy の一覧(針と台帳の共通の物差し)。"
  (setv found [])
  (for [source (sorted (.rglob repo-root "*.hy"))]
    (setv rel (.relative-to source repo-root))
    (when (& (set rel.parts) SCAN-SKIP-PARTS)
      (continue))
    (when (re.search r"\(defmacro\s"
                     (.read-text source :encoding "utf-8" :errors "replace"))
      (.append found (str rel))))
  (tuple found))


(defn #^ tuple contract-modules [#^ Path repo-root]
  "共通の品質検査の module 契約(静的の検査が常時かかる面の宣言)。"
  (setv declaration (json.loads (.read-text (/ repo-root ".agents" "code-quality.json")
                                            :encoding "utf-8")))
  (tuple (get declaration "modules")))


(defadr ADR-DOE-HY-005
  :title "Hy の柵の残り 2 本: 実行時の型検査は TypeCheck の要求にして handler で切り替える(既定 off・静的は常時)。記録の足場の macro は doeff-hy に投影の規則と一緒に置き、各 repo に macro を生やさない(既存の所有者は凍結台帳)"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_005_typecheck_and_record_fences.hy"
          "packages/doeff-hy/src/doeff_hy/typecheck.hy"
          "packages/doeff-hy/src/doeff_hy/typecheck_handlers.hy"
          "packages/doeff-hy/src/doeff_hy/record.hy"
          ".agents/code-quality.json"]
  :problem
    [(fact
       "operator の決め(agora-redesign 決定 1.4、2026-09-12): Hy の柵 7 本のうち残りは「TypeCheck の effect(静的は常時・実行時の検査は handler で切替)」「記録の足場の macro」「読み側の射影を operator に置かない」「新しい macro は doeff-hy に投影の規則と一緒に」。"
       :evidence "agora-redesign docs/plans/decisions-merge-2026-09-12.md 1.4")
     (fact
       "defk / deff の :pre / :post は展開時に無条件の assert (isinstance …) になる。切替の口が無いので、静的に分かる型を hot path で毎回払う。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy の _expand-check")
     (fact
       "手番の記録の書き手が行を裸の dict で組むと、柵 2(`\"value\"`・Any・object・裸の dict / list の禁止)を破り、欄の綴り違いも型違いも実行時まで見えない。"
       :evidence "決定 1.4 の柵 2(共通の品質検査 agent/quality の hy ルールで運転中)")
     (fact
       "macro は repo ごとに生えると、静的検査がその意味を知らないまま欠測になる。共通の品質検査は macro を実行しないので、投影の規則(どう読むか)が checker 側に無い macro は『未対応』で検査の外に落ちる。"
       :evidence "~/dotfiles/agent/quality/hy_projection.py の DOEFF_MACROS 固定表と declaration_import")
     (fact
       "実測 2026-09-12: doeff で `defmacro` を持つ .hy は 7 file(doeff-hy 4 / doeff-adr 1 / doeff-domain 1 / doeff-docker 1)。"
       :evidence "本 ADR の MACRO-OWNER-ROSTER(走査条件同一の凍結断面)")]
  :context
    [(interpretation
       "型検査は二層に分かれる。静的は source を投影して常時かけるもので、切替の口を持たせるべきではない(切れる検査は切られる)。実行時は値そのものを見るもので、静的に見えない境界(外から来た値・台帳から読んだ行)にだけ要る — だから要求として出し、果たすかを handler に決めさせる。")
     (interpretation
       "既定を off にする理由は速さではなく意味だ。静的が常時効いている面で実行時にもう一度確かめるのは、同じことを二度言うだけで、赤の出どころを 2 つにする。検だけが on にすることで『静的に見えない境界はどこか』が検の側に明示される。")
     (interpretation
       "記録の足場は「形」だけを持ち、「中身」は持たない。手番の記録の欄の正本は ACP の契約(docs/contracts)で、doeff-hy が知ってよいのは『欄の名前と型を宣言した凍結の record を建てる』という形だけ。ここに欄を書くと正本が 2 つになる。")
     (interpretation
       "macro を doeff-hy に集める理由は house style ではなく検査の到達である。macro と投影の規則が 1 対 1 で同じ便に置かれる限り、使う側の repo は宣言を 1 つも書かずに静的検査を通る。repo ごとに macro を生やすと、その repo の契約に macros を宣言する手間と、checker が現物の形を検証できる範囲の制約が毎回付いてくる。")]
  :decision
    [(rule R1 "実行時の型検査は `doeff_hy.typecheck` の TypeCheck の要求として出す。果たす家は 2 つ(`type-checks-off` / `type-checks-on`)で、どちらを当てるかは composition root が選ぶ。既定は off。")
     (rule R2 "静的の型検査に切替の口を作らない。Hy の面は共通の品質検査の module 契約に `typed: true` で登録し、投影と pyright が常時かかる状態を保つ。")
     (rule R3 "赤の文言(何がどの型を期待して何だったか)の定義点は `type-check-verdict` の 1 点。handler の中に文言を埋めない。")
     (rule R4 "記録の足場の macro は `doeff_hy.record` が所有し、共通の品質検査の投影の規則(`quality.hy_record`)と同じ便で出荷する。記録の欄(中身)はここに書かない。")
     (rule R5 "新しい macro は doeff-hy にだけ置く。MACRO-OWNER-ROSTER の外の file が `defmacro` を持ったら赤。名簿の増額は operator 裁定のみ。")]
  :laws
    [(law runtime-type-checks-are-off-by-default
       :statement "for_all program p with a broken TypeCheck: run(type_checks_off, p) returns the value unchanged and run(type_checks_on, p) raises TypeCheckError"
       :counterexamples
         [(counterexample "既定の家が値を見る — 静的に分かる型を hot path で二度払い、赤の出どころが静的と実行時の 2 つに割れる")
          (counterexample "on の家が値を見ない(要求を素通しする)— 検が『境界を確かめた』と言えるのに何も確かめていない(空の検査)")])
     (law static-type-check-is-always-on
       :statement "for_all hy module m in doeff_hy fences: contract(m).typed = true — 静的の検査に切替の口は無い"
       :counterexamples
         [(counterexample "柵の module を typed: false で登録する — 実行時を off にした上で静的も外れ、型の保証が 1 つも残らない")])
     (law new-macros-live-only-in-doeff-hy
       :statement "for_all hy_file f with defmacro: f in MACRO-OWNER-ROSTER — 名簿の外に macro は生えない"
       :counterexamples
         [(counterexample "repo の中の module が自分用の macro を定義する — 共通の品質検査は macro を実行しないので、その repo の契約に macros を宣言するまで使用側は欠測になる(検査の到達が静かに落ちる)")])]
  :enforcement
    [(deftest test-adr-doe-hy-005-runtime-type-check-is-switched-by-the-handler
       ;; 針: 同じ program を 2 つの家で回し、既定(off)は素通し・検(on)は赤。
       (assert (= (run-without-type-checks (agreeing-boundary 7)) 7))
       (assert (= (run-without-type-checks (mismatched-boundary 7)) 7)
               "既定の家が値を見ている(ADR-DOE-HY-005 R1 — 既定は off)")
       (assert (= (run-with-type-checks (agreeing-boundary 7)) 7))
       (setv raised None)
       (try
         (run-with-type-checks (mismatched-boundary 7))
         (except [error TypeCheckError] (setv raised error)))
       (assert (is-not raised None)
               "検の家が値を見ていない(ADR-DOE-HY-005 R1 — 検で on)")
       (assert (in "mismatched-boundary" (str raised)))
       (assert (in "value" (str raised))))
     (deftest test-adr-doe-hy-005-verdict-is-the-single-definition-point
       ;; 針: 文言の定義点が 1 つ(handler を通らずに同じ判断が撃てる)。
       (assert (is (type-check-verdict "owner" "x" str "ok") None))
       (setv verdict (type-check-verdict "owner" "x" str 7))
       (assert (is-not verdict None))
       (assert (in "owner" verdict))
       (assert (in "int" verdict)))
     (deftest test-adr-doe-hy-005-static-check-is-always-on
       ;; 針: 柵の module が module 契約に typed: true で載っている。
       (setv repo-root (. (Path __file__) parent parent parent))
       (setv modules (contract-modules repo-root))
       (setv fences ["packages/doeff-hy/src/doeff_hy/typecheck.hy"
                     "packages/doeff-hy/src/doeff_hy/record.hy"])
       (for [fence fences]
         (setv owners (lfor m modules :if (in fence (get m "paths")) m))
         (assert (= (len owners) 1)
                 (+ "柵の module が契約に 1 つで載っていない(ADR-DOE-HY-005 R2): " fence))
         (assert (get (get owners 0) "typed")
                 (+ "柵の module が typed: false で登録されている"
                    "(ADR-DOE-HY-005 R2 — 静的に切替の口は作らない): " fence))))
     (deftest test-adr-doe-hy-005-macros-live-only-in-doeff-hy
       ;; 針: 実測 = macro-owning-files、法 = MACRO-OWNER-ROSTER との一致。
       (setv repo-root (. (Path __file__) parent parent parent))
       (setv found (macro-owning-files repo-root))
       (setv strays (sorted (lfor rel found :if (not-in rel MACRO-OWNER-ROSTER) rel)))
       (assert (= strays [])
               (+ "名簿の外の file が macro を定義している(ADR-DOE-HY-005 R5 — "
                  "新しい macro は doeff-hy に投影の規則と一緒に置く): " (str strays)))
       (setv stale (sorted (lfor rel MACRO-OWNER-ROSTER :if (not-in rel found) rel)))
       (assert (= stale [])
               (+ "台帳の削り忘れ(ADR-DOE-HY-005 R5 — macro を消した便は名簿も"
                  "同便で削る): " (str stale))))]
  :plans [])
