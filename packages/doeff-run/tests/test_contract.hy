;;; 契約の検 — DoeffRun の公開 effect の約束を、どの handler の下でも同じ筋書きで確かめる(fake・docker・process・k3s)。
;;;
;;; 筋書きは handler を知らない: ScenarioSettings で宣言を読み、公開 effect だけを撃つ。負荷の中身は workloads.hy の約束
;;; (image の中の contract-workload)で選ぶ。失敗の筋書き(止まらない・起動できない・消えた・知らない取っ手)を先に置く。
;;; 新しい handler を足す card は、interpreters.hy に解釈器の名を足し、各 deftest の :interpreters に名を足す。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [Spawn Wait])
(import doeff_time [Delay GetMonotonic])
(import doeff_run [RunHandle RunStart RunStop RunRemove RunStatus RunReadEvents RunVanish
                   RunAccepted RunNameInUse RunStillAlive RunPending RunLive RunStopped RunGone StopCause])
(import tests.workloads [live-spec exits-spec stubborn-spec hog-spec])
(import tests.steps [settings begin read-until-live read-to-end read-until terminal? kinds-of new-name])

(setv LONG 60.0)


(defk stop-after [#^ RunHandle handle #^ float seconds]
  {:pre [(: handle RunHandle) (: seconds float)] :post [(: % (| RunStopped RunGone))]}
  "seconds 秒の後に run を止める(読みの待ちの外から止める別の task)。"
  (<- (Delay seconds))
  (<- stopped (RunStop handle 5.0))
  stopped)

(defk vanish-after [#^ RunHandle handle #^ float seconds]
  {:pre [(: handle RunHandle) (: seconds float)] :post [(: % bool)]}
  "seconds 秒の後に run を世界の外の力で消す(読みの待ちの外から消す別の task)。"
  (<- (Delay seconds))
  (<- vanished (RunVanish handle))
  vanished)


;; --- 失敗の筋書き ------------------------------------------------------------------------------------

(deftest test-an-image-that-cannot-begin-never-becomes-live
  ;; 起動できない image(取り寄せられない・無い): 受け付けた後で RunLive を経ずに、起動しなかった止まり(NEVER_BEGAN)で終わる。
  ;; 終了 code は無い(走っていない)。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.missing-image)))
  (<- seen (read-to-end handle LONG))
  (assert (= (kinds-of seen.events) [RunStopped]) (repr seen))
  (assert (and (isinstance seen.state RunStopped) (= seen.state.cause StopCause.NEVER-BEGAN) (is seen.state.exit-code None)) (repr seen.state)))

(deftest test-a-run-over-its-memory-limit-is-stopped-as-out-of-memory
  ;; 資源の上限(メモリ)を越えて殺された run は、OOM の止まりで終わる(自分で終わった物とも、頼まれた止めとも区別できる)。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (hog-spec s.image)))
  (<- seen (read-to-end handle LONG))
  (assert (= (kinds-of seen.events) [RunLive RunStopped]) (repr seen))
  (assert (= seen.state.cause StopCause.OUT-OF-MEMORY) (repr seen.state))
  (assert (isinstance seen.state.exit-code int) (repr seen.state)))

(deftest test-a-run-that-ignores-the-stop-signal-is-killed-when-the-grace-runs-out
  ;; 止めの合図を無視する run: 猶予(grace-seconds)が尽きるまで待ち、強制で止める。止めの答えは猶予より早くは返らない。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (stubborn-spec s.image)))
  (<- _ (read-until-live handle))
  (<- began (GetMonotonic))
  (<- stopped (RunStop handle 1.0))
  (<- ended (GetMonotonic))
  (assert (and (isinstance stopped RunStopped) (= stopped.cause StopCause.REQUESTED)) (repr stopped))
  (assert (isinstance stopped.exit-code int) (repr stopped))
  (assert (>= (- ended began) (- 1.0 s.slack)) (- ended began)))

(deftest test-a-vanished-run-is-gone-and-its-name-is-free-again
  ;; 取っ手の知らない所で run が消えた(node の死・手で消された): 状態は RunGone で、消えた run は何も残さない(読むと出来事の無い頁と
  ;; RunGone)。止めは冪等に RunGone。
  ;; 名は空き、同じ名で新しく走らせると別の run(取っ手の run-id が違い、古い取っ手は新しい run の状態を答えない)。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val name (new-name))
  (<- first (RunStart name (live-spec s.image)))
  (<- _ (read-until-live first.handle))
  (<- vanished (RunVanish first.handle))
  (assert (is vanished True) (repr vanished))
  (<- seen (read-to-end first.handle LONG))
  (assert (and (= seen.events #()) (isinstance seen.state RunGone)) (repr seen))
  (<- status (RunStatus first.handle))
  (assert (isinstance status RunGone) (repr status))
  (<- stopped (RunStop first.handle 1.0))
  (assert (isinstance stopped RunGone) (repr stopped))
  (<- again (RunStart name (live-spec s.image)))
  (assert (isinstance again RunAccepted) (repr again))
  (assert (!= again.handle.run-id first.handle.run-id) (repr again))
  (<- old (RunStatus first.handle))
  (assert (isinstance old RunGone) (repr old)))

(deftest test-a-read-wakes-when-the-run-vanishes
  ;; 読みが待つ間に run が消える: 読みは上限まで眠らず、消えた出来事 RunGone を返す。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- live (read-until-live handle))
  (<- vanisher (Spawn (vanish-after handle 0.5)))
  (<- began (GetMonotonic))
  (<- page (RunReadEvents handle live.after 30.0))
  (<- ended (GetMonotonic))
  (<- vanished (Wait vanisher))
  (assert (is vanished True) (repr vanished))
  (assert (= (kinds-of page.events) [RunGone]) (repr page))
  (assert (isinstance page.state RunGone) (repr page))
  (assert (< (- ended began) 10.0) (- ended began)))

(deftest test-a-handle-the-handler-does-not-know-is-gone
  ;; 名にも取っ手にも覚えが無い run は「消えた」として答える(呼び手が名を持つ — 誤りと消失は見分けない)。読みは待たずに返る。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val unknown (RunHandle :name "never-started" :run-id "no-such-run"))
  (<- status (RunStatus unknown))
  (assert (isinstance status RunGone) (repr status))
  (<- stopped (RunStop unknown 1.0))
  (assert (isinstance stopped RunGone) (repr stopped))
  (<- removed (RunRemove unknown))
  (assert (isinstance removed RunGone) (repr removed))
  (<- began (GetMonotonic))
  (<- page (RunReadEvents unknown 0 30.0))
  (<- ended (GetMonotonic))
  (assert (and (= page.events #()) (isinstance page.state RunGone) (= page.next-seq 0)) (repr page))
  (assert (< (- ended began) s.slack) (- ended began)))

(deftest test-a-name-in-use-answers-with-the-holders-handle
  ;; 使っている名で走らせ直すと、受け付けずに今の持ち主の取っ手を答える(取っ手を失った呼び手が引き取れる)。止まった run も、
  ;; 片付けるまで名を持ち続ける。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val name (new-name))
  (<- first (RunStart name (live-spec s.image)))
  (assert (isinstance first RunAccepted) (repr first))
  (<- second (RunStart name (live-spec s.image)))
  (assert (and (isinstance second RunNameInUse) (= second.handle first.handle)) (repr second))
  (<- _ (read-until-live first.handle))
  (<- stopped (RunStop first.handle 5.0))
  (assert (isinstance stopped RunStopped) (repr stopped))
  (<- third (RunStart name (live-spec s.image)))
  (assert (and (isinstance third RunNameInUse) (= third.handle first.handle)) (repr third)))

(deftest test-removing-a-live-run-is-refused-and-removing-a-stopped-run-frees-the-name
  ;; 片付け(RunRemove)は止まった run だけ: 走っている run には RunStillAlive(止めてから)。片付けた run は RunGone を最後の出来事に
  ;; 残して消え、名が空く。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (val name (new-name))
  (<- first (RunStart name (live-spec s.image)))
  (<- _ (read-until-live first.handle))
  (<- refused (RunRemove first.handle))
  (assert (and (isinstance refused RunStillAlive) (= refused.handle first.handle)) (repr refused))
  (<- stopped (RunStop first.handle 5.0))
  (assert (isinstance stopped RunStopped) (repr stopped))
  (<- removed (RunRemove first.handle))
  (assert (isinstance removed RunGone) (repr removed))
  (<- status (RunStatus first.handle))
  (assert (isinstance status RunGone) (repr status))
  (<- again (RunStart name (live-spec s.image)))
  (assert (and (isinstance again RunAccepted) (!= again.handle.run-id first.handle.run-id)) (repr again)))


;; --- 状態の出来事 ------------------------------------------------------------------------------------

(deftest test-a-run-that-exits-by-itself-is-live-then-stopped-with-its-exit-code
  ;; 自分で終わる run: 起きた(RunLive)→ 止まった(RunStopped・自分で終わった EXITED と、その終了 code)。状態の答えも同じ値。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (exits-spec s.image 3 0.5)))
  (<- seen (read-to-end handle LONG))
  (assert (= (kinds-of seen.events) [RunLive RunStopped]) (repr seen))
  (assert (and (= seen.state.exit-code 3) (= seen.state.cause StopCause.EXITED)) (repr seen.state))
  (assert (= (. (get seen.events -1) state) seen.state) (repr seen))
  (val stamps (lfor event seen.events event.at))
  (assert (= stamps (sorted stamps)) (repr stamps))
  (<- status (RunStatus handle))
  (assert (= status seen.state) (repr status)))

(deftest test-a-new-run-is-pending-or-live
  ;; 受け付けた直後の状態は、まだ起きていない(RunPending)か、もう起きた(RunLive)— 止まってはいない。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- status (RunStatus handle))
  (assert (isinstance status #(RunPending RunLive)) (repr status)))

(deftest test-events-are-paged-by-seq-without-gaps-or-repeats
  ;; 出来事は 1 から欠けずに番号を持ち、after-seq より後だけを 1 度ずつ返す。終わった run は何度読んでも同じ列と同じ状態を、待たずに返す。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (exits-spec s.image 0 0.2)))
  (<- seen (read-to-end handle LONG))
  (assert (= (lfor event seen.events event.seq) [1 2]) (repr seen))
  (<- began (GetMonotonic))
  (<- whole (RunReadEvents handle 0 30.0))
  (<- rest (RunReadEvents handle 1 30.0))
  (<- none (RunReadEvents handle 2 30.0))
  (<- ended (GetMonotonic))
  (assert (= whole.events seen.events) (repr whole))
  (assert (= (lfor event rest.events event.seq) [2]) (repr rest))
  (assert (and (= none.events #()) (= none.next-seq 2) (= none.state seen.state)) (repr none))
  (assert (< (- ended began) s.slack) (- ended began)))

(deftest test-a-read-comes-back-empty-at-the-deadline
  ;; 新しい出来事が無い間の読みは、待ちの上限(wait-up-to)まで待ってから、空の頁と今の状態を返す。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- live (read-until-live handle))
  (<- began (GetMonotonic))
  (<- page (RunReadEvents handle live.after 2.0))
  (<- ended (GetMonotonic))
  (assert (and (= page.events #()) (= page.next-seq live.after) (isinstance page.state RunLive)) (repr page))
  (assert (< (abs (- (- ended began) 2.0)) s.slack) (- ended began)))

(deftest test-a-read-wakes-at-the-next-event
  ;; 待っている読みは、次の出来事(自分で終わる run の終わり)が起きた時に、上限を待たずに返る。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (exits-spec s.image 0 1.0)))
  (<- live (read-until-live handle))
  (<- began (GetMonotonic))
  (<- page (RunReadEvents handle live.after 30.0))
  (<- ended (GetMonotonic))
  (assert (= (kinds-of page.events) [RunStopped]) (repr page))
  (assert (< (- ended began) 10.0) (- ended began)))

(deftest test-a-read-wakes-when-another-task-stops-the-run
  ;; 読みが待つ間に、別の task が止めを頼む: 読みは上限まで眠らず、止まった刻に止まりの出来事を返す。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- live (read-until-live handle))
  (<- stopper (Spawn (stop-after handle 0.5)))
  (<- began (GetMonotonic))
  (<- page (RunReadEvents handle live.after 30.0))
  (<- ended (GetMonotonic))
  (<- (Wait stopper))
  (assert (= (kinds-of page.events) [RunStopped]) (repr page))
  (assert (= page.state.cause StopCause.REQUESTED) (repr page))
  (assert (< (- ended began) 10.0) (- ended began)))


;; --- 止める ------------------------------------------------------------------------------------------

(deftest test-stop-ends-a-live-run-as-requested
  ;; 走っている run を止める: 止めの答えは止まった状態(頼まれた止め REQUESTED・終了 code つき)で、状態と出来事の列の終わりも同じ値。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- _ (read-until-live handle))
  (<- stopped (RunStop handle 5.0))
  (assert (and (isinstance stopped RunStopped) (= stopped.cause StopCause.REQUESTED) (isinstance stopped.exit-code int)) (repr stopped))
  (<- status (RunStatus handle))
  (assert (= status stopped) (repr status))
  (<- seen (read-to-end handle LONG))
  (assert (= (kinds-of seen.events) [RunLive RunStopped]) (repr seen))
  (assert (= seen.state stopped) (repr seen)))

(deftest test-stop-before-the-run-begins-ends-it-as-requested
  ;; 起きる前に止める: 受け付けた run は頼まれた止め REQUESTED で終わる(走っていなければ終了 code は無い)。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- stopped (RunStop handle 5.0))
  (assert (and (isinstance stopped RunStopped) (= stopped.cause StopCause.REQUESTED)) (repr stopped))
  (<- seen (read-to-end handle LONG))
  (assert (= seen.state stopped) (repr seen)))

(deftest test-stop-is-idempotent-and-keeps-how-the-run-ended
  ;; 止めの 2 度目は、1 度目と同じ状態を答える。自分で終わった run を止めても、終わり方(EXITED)と終了 code は変わらない。
  {:interpreters ["fake" "docker"]}
  (<- s (settings))
  (<- handle (begin (live-spec s.image)))
  (<- _ (read-until-live handle))
  (<- once (RunStop handle 5.0))
  (<- twice (RunStop handle 5.0))
  (assert (= once twice) (repr #(once twice)))
  (<- done (begin (exits-spec s.image 7 0.2)))
  (<- ended (read-to-end done LONG))
  (<- late (RunStop done 5.0))
  (assert (= late ended.state) (repr #(late ended)))
  (assert (and (= late.cause StopCause.EXITED) (= late.exit-code 7)) (repr late)))

