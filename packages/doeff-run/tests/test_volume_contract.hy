;;; volume の契約の検 — DoeffRun の volume の効果(VolumeEnsure = 無ければ作る・VolumeStatus = 在るかを問う)の約束を、どの handler の
;;; 下でも同じ筋書きで確かめる(fake・docker・process・k3s)。test_contract.hy と同じ作り: 公開 effect だけを撃ち、失敗の筋書きを先に置く。
;;;
;;; volume を持つ handler(fake・docker)は作る・問う・走らせる側の筋書きを通し、持たない handler(process)は同じ効果を
;;; RunRefused と同じ訳 UNSUPPORTED で断る側の筋書きを通す。volume を消す効果は無い: 消えた volume の筋書きは、解釈器が backend から直に消す
;;; 横の口(Settings.vanish-volume)で作る。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_core_effects [Spawn Wait])
(import doeff_run [RunStart RunStop RunRemove RunAccepted RunRefused RefusalWhy RunLive RunStopped RunGone StopCause
                   VolumeEnsure VolumeStatus VolumeCreated VolumeExisted VolumePresent VolumeAbsent VolumeRefused])
(import tests.workloads [volume-spec])
(import tests.steps [settings begin read-until-live read-to-end kinds-of new-name new-volume-name])

(setv LONG 60.0)
;; どの backend の規則も断る名: path(docker は bind mount に読む)・1 字・空白・, を含む・記号で始まる。
(setv BAD-NAMES ["/srv/data" "x" "has space" "a,b" ".hidden" "-dash"])


(defk ensure-volume [#^ str name]
  {:pre [(: name str)] :post [(: % (| VolumeCreated VolumeExisted VolumeRefused))]}
  "volume を無ければ作る(同じ名の作成を重ねて撃つ別の task の体)。"
  (<- outcome (VolumeEnsure name))
  outcome)


;; --- 失敗の筋書き: volume を持つ handler -------------------------------------------------------------------

(deftest test-a-volume-nobody-made-is-absent-and-a-run-pointing-at-it-never-begins
  ;; 作っていない volume: 問えば無い。それを指す run は受け付けた後で RunLive を経ずに NEVER_BEGAN で終わる(docker は無い named volume を
  ;; 黙って作る — その事故の防ぎ)。走らせ始めは volume を作らない: 止まった後も無いまま。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- asked (VolumeStatus volume))
  (assert (= asked (VolumeAbsent :name volume)) (repr asked))
  (<- handle (begin (volume-spec s.image volume)))
  (<- seen (read-to-end handle LONG))
  (assert (= (kinds-of seen.events) [RunStopped]) (repr seen))
  (assert (and (= seen.state.cause StopCause.NEVER-BEGAN) (is seen.state.exit-code None) (in volume seen.state.detail)) (repr seen.state))
  (<- after (VolumeStatus volume))
  (assert (= after (VolumeAbsent :name volume)) (repr after))
  (assert (not-in volume (s.volumes-now)) (repr (s.volumes-now))))

(deftest test-an-ensured-volume-is-really-there
  ;; 作ったつもりで無い、の防ぎ: 作ったと答えたなら、問えば在り、backend に直に在り、それを指す run が起きる。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- made (VolumeEnsure volume))
  (assert (= made (VolumeCreated :name volume)) (repr made))
  (<- asked (VolumeStatus volume))
  (assert (= asked (VolumePresent :name volume)) (repr asked))
  (assert (in volume (s.volumes-now)) (repr (s.volumes-now)))
  (<- handle (begin (volume-spec s.image volume)))
  (<- live (read-until-live handle))
  (assert (isinstance live.state RunLive) (repr live)))

(deftest test-ensuring-an-existing-volume-leaves-it-as-it-is
  ;; 在る volume への作成は在るまま: 「在った」と答え、作り直さない(中身を失わない)。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- first (VolumeEnsure volume))
  (<- second (VolumeEnsure volume))
  (assert (= first (VolumeCreated :name volume)) (repr first))
  (assert (= second (VolumeExisted :name volume)) (repr second))
  (<- asked (VolumeStatus volume))
  (assert (= asked (VolumePresent :name volume)) (repr asked)))

(deftest test-ensures-of-one-name-at-once-make-one-volume
  ;; 同じ名の作成が重なっても volume は 1 つ: 作ったと答えるのは 1 つだけで、残りは在ったと答える。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- one (Spawn (ensure-volume volume)))
  (<- two (Spawn (ensure-volume volume)))
  (<- three (Spawn (ensure-volume volume)))
  (<- first (Wait one))
  (<- second (Wait two))
  (<- third (Wait three))
  (val answers [first second third])
  (assert (all (gfor answer answers (isinstance answer #(VolumeCreated VolumeExisted)))) (repr answers))
  (assert (= (len (lfor answer answers :if (isinstance answer VolumeCreated) answer)) 1) (repr answers))
  (assert (= (len (lfor name (s.volumes-now) :if (= name volume) name)) 1) (repr (s.volumes-now)))
  (<- asked (VolumeStatus volume))
  (assert (= asked (VolumePresent :name volume)) (repr asked)))

(deftest test-a-vanished-volume-is-not-silently-remade
  ;; 消えた volume を「在るもの」として黙って空で作り直さない: 問えば無く(問いは作らない)、それを指す run は起きず(走らせ始めは作らない)、
  ;; 作る効果は「作った」と答える(「在った」と偽らない — 開き直しの呼び手は先に問う効果で確かめ、無ければ作らずに断る)。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- made (VolumeEnsure volume))
  (assert (isinstance made VolumeCreated) (repr made))
  (s.vanish-volume volume)
  (<- asked (VolumeStatus volume))
  (assert (= asked (VolumeAbsent :name volume)) (repr asked))
  (assert (not-in volume (s.volumes-now)) (repr (s.volumes-now)))
  (<- handle (begin (volume-spec s.image volume)))
  (<- seen (read-to-end handle LONG))
  (assert (and (= (kinds-of seen.events) [RunStopped]) (= seen.state.cause StopCause.NEVER-BEGAN)) (repr seen))
  (<- still (VolumeStatus volume))
  (assert (= still (VolumeAbsent :name volume)) (repr still))
  (<- again (VolumeEnsure volume))
  (assert (= again (VolumeCreated :name volume)) (repr again))
  (<- back (VolumeStatus volume))
  (assert (= back (VolumePresent :name volume)) (repr back)))

(deftest test-a-name-the-backend-rule-refuses-is-refused-before-anything-is-made
  ;; backend の名の規則(docker の named volume の名)に合わない名は、作る前に UNSUPPORTED で断る。問いも同じに断る(そういう名の volume は
  ;; 有り得ない)。断った後に何も作られていない。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (for [bad BAD-NAMES]
    (<- made (VolumeEnsure bad))
    (assert (and (isinstance made VolumeRefused) (= made.why RefusalWhy.UNSUPPORTED) (= made.name bad) made.detail) (repr made))
    (<- asked (VolumeStatus bad))
    (assert (and (isinstance asked VolumeRefused) (= asked.why RefusalWhy.UNSUPPORTED) (= asked.name bad) asked.detail) (repr asked)))
  (assert (= (s.volumes-now) (frozenset)) (repr (s.volumes-now))))

(deftest test-a-volume-outlives-the-run-that-used-it
  ;; 作った volume は run を止めても片付けても残る(worktree は消さない): 止めた後も、RunRemove の後も在り、次の run が同じ volume で起きる。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- made (VolumeEnsure volume))
  (assert (isinstance made VolumeCreated) (repr made))
  (<- handle (begin (volume-spec s.image volume)))
  (<- _ (read-until-live handle))
  (<- stopped (RunStop handle 5.0))
  (assert (isinstance stopped RunStopped) (repr stopped))
  (<- after-stop (VolumeStatus volume))
  (assert (= after-stop (VolumePresent :name volume)) (repr after-stop))
  (<- removed (RunRemove handle))
  (assert (isinstance removed RunGone) (repr removed))
  (<- after-remove (VolumeStatus volume))
  (assert (= after-remove (VolumePresent :name volume)) (repr after-remove))
  (assert (in volume (s.volumes-now)) (repr (s.volumes-now)))
  (<- again (begin (volume-spec s.image volume)))
  (<- live (read-until-live again))
  (assert (isinstance live.state RunLive) (repr live)))


;; --- 失敗の筋書き: volume を持たない handler ---------------------------------------------------------------

(deftest test-a-handler-without-volumes-refuses-to-make-or-ask-and-makes-nothing
  ;; volume を持たない handler(process: 子は host の file system を見る)は、作る効果も問う効果も、黙って成功したことにせず UNSUPPORTED で
  ;; 断る(「無い」と答えると、置き場が消えた事と見分けがつかない)。volume を指す run を断るのは従来どおり。
  {:interpreters ["process"]}
  (<- s (settings))
  (val volume (new-volume-name))
  (<- made (VolumeEnsure volume))
  (assert (and (isinstance made VolumeRefused) (= made.why RefusalWhy.UNSUPPORTED) (= made.name volume) made.detail) (repr made))
  (<- asked (VolumeStatus volume))
  (assert (and (isinstance asked VolumeRefused) (= asked.why RefusalWhy.UNSUPPORTED) (= asked.name volume) asked.detail) (repr asked))
  (<- run (RunStart (new-name) (volume-spec s.image volume)))
  (assert (and (isinstance run RunRefused) (= run.why RefusalWhy.UNSUPPORTED)) (repr run))
  (for [bad BAD-NAMES]
    (<- bad-made (VolumeEnsure bad))
    (assert (and (isinstance bad-made VolumeRefused) (= bad-made.why RefusalWhy.UNSUPPORTED)) (repr bad-made))))
