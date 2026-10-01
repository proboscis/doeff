;;; job が本番の土台で閉じているかを、実行せずに確かめる検(foundation_check — 構成のレビューの B の残り)。
;;;
;;; sim-cluster は sim の土台で走らせるので、本番の土台の入れ忘れ(scheduler・時計・設定の読み)は sim では見つからない。
;;; doeff-effect-analyzer で job の土台の引数に本番の土台を束ねて読み、Program が自分で並べた handler を通した後に残る effect を見る。
;;; 読めない handler・追えない所は閉じていると数えない。
(require doeff-hy.macros [deftest val])
(import doeff_cluster.foundation.foundation_check [foundation-closure closed?])
(import tests.fixtures.closure_programs [job untranslated-job tried-job untranslated-tried-job production-foundation
                                         clockless-foundation unscheduled-foundation opaque-foundation])


(deftest test-a-job-under-the-full-production-foundation-is-closed
  (val closure (foundation-closure job :foundation production-foundation))
  (assert (closed? closure) closure))


(deftest test-a-forgotten-clock-is-a-gap
  (val closure (foundation-closure job :foundation clockless-foundation))
  (assert (not (closed? closure)))
  (assert (any (gfor g closure.gaps (in "DelayEffect" g))) closure.gaps))


(deftest test-a-forgotten-translation-leaves-the-business-effect
  (val closure (foundation-closure untranslated-job :foundation production-foundation))
  (assert (any (gfor g closure.gaps (in "Ping" g))) closure.gaps))


(deftest test-a-program-carried-by-try-is-counted-where-try-was-performed
  ;; Try は運んだ本体を出した所の handler の下で走らせる(__doeff_runs_carried__)ので、本体の effect は出した所で数える。
  (val closure (foundation-closure tried-job :foundation production-foundation))
  (assert (closed? closure) closure))


(deftest test-a-forgotten-translation-inside-try-is-a-gap
  ;; 反例: 業務の effect が Try の中にしか無くても、翻訳を並べ忘れれば gap(以前は Try の運ぶ本体を数えず、閉じていると読んだ)。
  (val closure (foundation-closure untranslated-tried-job :foundation production-foundation))
  (assert (any (gfor g closure.gaps (in "Ping" g))) closure.gaps))


(deftest test-a-forgotten-scheduler-leaves-the-spawn
  ;; sim の土台は scheduler を含まない形なので、この入れ忘れは sim-cluster では見つからない — ここで見つける。
  (val closure (foundation-closure job :foundation unscheduled-foundation))
  (assert (any (gfor g closure.gaps (in "Spawn" g))) closure.gaps))


(deftest test-a-handler-the-analyzer-cannot-read-is-not-counted-as-closed
  ;; 節も __doeff_handles__ の宣言も持たない handler は gap を隠しうる — 閉じていると数えない。
  (val closure (foundation-closure job :foundation opaque-foundation))
  (assert closure.unknown closure)
  (assert (not (closed? closure))))


(deftest test-an-unbound-foundation-cannot-be-followed
  ;; 土台を束ねずに読むと、job が引数の土台で本体を包む先を追えない — 閉じていると数えない。
  (val closure (foundation-closure job))
  (assert closure.unresolved closure)
  (assert (not (closed? closure))))
