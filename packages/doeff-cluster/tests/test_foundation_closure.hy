;;; job の本体が本番の土台で閉じているかを、実行せずに確かめる検(foundation_check — 構成のレビューの B の残り)。
;;;
;;; sim-cluster は sim の土台で走らせるので、本番の土台の入れ忘れ(scheduler・時計・設定の読み)は sim では見つからない。
;;; doeff-effect-analyzer で本体の effect を内側の組 → 本番の土台 → scheduler の順に通し、残る effect を見る。読めない handler・追えない所は
;;; 閉じていると数えない。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.foundation_check [foundation-closure closed? FoundationClosure])
(import tests.fixtures.closure_programs [business job translation-handlers production-handlers forgetful-handlers
                                         python-clock-handlers])


(deftest test-a-body-under-the-full-production-foundation-is-closed
  (val closure (foundation-closure business :foundation-handlers production-handlers :inner #(translation-handlers)))
  (assert (closed? closure) closure))


(deftest test-a-forgotten-clock-is-a-gap
  (val closure (foundation-closure business :foundation-handlers forgetful-handlers :inner #(translation-handlers)))
  (assert (not (closed? closure)))
  (assert (any (gfor g closure.gaps (in "DelayEffect" g))) closure.gaps))


(deftest test-a-forgotten-translation-leaves-the-business-effect
  (val closure (foundation-closure business :foundation-handlers production-handlers))
  (assert (any (gfor g closure.gaps (in "Ping" g))) closure.gaps))


(deftest test-without-the-scheduler-the-spawn-is-a-gap
  (val closure (foundation-closure business :foundation-handlers production-handlers :inner #(translation-handlers)
                                   :outer #()))
  (assert (any (gfor g closure.gaps (in "Spawn" g))) closure.gaps))


(deftest test-a-handler-the-analyzer-cannot-read-is-not-counted-as-closed
  ;; Python で書いた handler の工場(sync-time-handler)は節を読めない — gap を隠しうるので閉じていると数えない(analyzer の足りない所 3)。
  (val closure (foundation-closure business :foundation-handlers python-clock-handlers :inner #(translation-handlers)))
  (assert closure.unknown closure)
  (assert (not (closed? closure))))


(deftest test-a-foundation-taken-as-an-argument-cannot-be-followed
  ;; job の defk が引数で受けた土台で本体を包む形は、analyzer が引数の先を追えない(analyzer の足りない所 4)— 本体を分けて渡す。
  (val closure (foundation-closure job :foundation-handlers production-handlers :inner #(translation-handlers)))
  (assert closure.unresolved closure)
  (assert (not (closed? closure))))
