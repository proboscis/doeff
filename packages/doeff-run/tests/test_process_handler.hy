;;; process の handler だけの検 — 契約の検(test_contract.hy)が撃たない、process の backend の約束(失敗ケース先頭)。
;;;
;;; process の handler は image を「実行 file の入った dir」に読み(host の表で image の名 → dir)、dir を子の PATH の先頭に置く。
;;; run の中の file system と資源の上限を持たないので、volume と上限は守れない設定として断る。命令が起こせない物は受け付けた後に
;;; 起きなかった止まり(NEVER-BEGAN)で届く。解釈器は interpreters.hy の process。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_core_effects [Spawn Wait])
(import doeff_time [GetMonotonic])
(import doeff_run [RunSpec RunStart RunStop RunAccepted RunRefused RefusalWhy RunStopped VolumeMount StopCause])
(import tests.workloads [stubborn-spec])
(import tests.steps [settings begin read-until-live read-to-end kinds-of new-name])

(setv LONG 60.0)


(defk stop-with [handle grace]
  {:pre [] :post [(: % RunStopped)]}
  "別の task から run を止める(止めを同時に 2 つ頼む筋書きの部品)。"
  (<- stopped (RunStop handle grace))
  stopped)


;; --- 起こせない物 ----------------------------------------------------------------------------------------

(deftest test-a-volume-is-refused-because-a-process-has-no-file-system-of-its-own
  ;; run の中の path に volume を置けない(process の子は host の file system を見る)。黙って無視せず走らせる前に断る。名は空いたまま。
  {:interpreters ["process"]}
  (<- s (settings))
  (val name (new-name))
  (<- outcome (RunStart name (RunSpec :image s.image :command #("contract-workload" "live")
                                      :volumes #((VolumeMount :source "data" :target "/data")))))
  (assert (and (isinstance outcome RunRefused) (= outcome.why RefusalWhy.UNSUPPORTED) (in "volume" outcome.detail)) (repr outcome))
  (<- accepted (RunStart name (RunSpec :image s.image :command #("contract-workload" "live"))))
  (assert (isinstance accepted RunAccepted) (repr accepted)))

(deftest test-a-command-the-image-does-not-have-never-begins
  ;; image の dir にも PATH にも無い命令: 受け付けた後で RunLive を経ずに、起きなかった止まり(NEVER-BEGAN)で終わる。理由の 1 行は
  ;; 命令の名を含む。
  {:interpreters ["process"]}
  (<- s (settings))
  (<- handle (begin (RunSpec :image s.image :command #("no-such-workload-anywhere"))))
  (<- seen (read-to-end handle LONG))
  (assert (= (kinds-of seen.events) [RunStopped]) (repr seen))
  (assert (and (= seen.state.cause StopCause.NEVER-BEGAN) (is seen.state.exit-code None) (in "no-such-workload-anywhere" seen.state.detail))
          (repr seen.state)))

(deftest test-a-run-without-a-command-never-begins
  ;; process には image の既定の命令が無い: 命令の無い run は起こせず、起きなかった止まりで終わる(docker は image の既定の命令を持つ)。
  {:interpreters ["process"]}
  (<- s (settings))
  (<- handle (begin (RunSpec :image s.image)))
  (<- seen (read-to-end handle LONG))
  (assert (and (= (kinds-of seen.events) [RunStopped]) (= seen.state.cause StopCause.NEVER-BEGAN) seen.state.detail) (repr seen)))

(deftest test-an-env-path-replaces-the-image-directory-in-the-path
  ;; spec の env は子の環境変数に載り、PATH を渡せば image の dir を載せた既定の PATH を置き換える(docker で env の PATH が image の PATH を
  ;; 置き換えるのと同じ)。image の dir が PATH に無ければ、その dir の命令は見つからない。
  {:interpreters ["process"]}
  (<- s (settings))
  (<- handle (begin (RunSpec :image s.image :command #("contract-workload" "live") :env {"PATH" "/nonexistent-dir"})))
  (<- seen (read-to-end handle LONG))
  (assert (and (= (kinds-of seen.events) [RunStopped]) (= seen.state.cause StopCause.NEVER-BEGAN)) (repr seen)))


;; --- 子の走り方 ------------------------------------------------------------------------------------------

(deftest test-the-env-reaches-the-run
  ;; env の値は子の環境変数で渡る(子が環境変数を読んで終了 code にする)。終了 code は自分で終わった EXITED で。
  {:interpreters ["process"]}
  (<- s (settings))
  (<- handle (begin (RunSpec :image s.image :command #("sh" "-c" "exit \"$EXIT_WITH\"") :env {"EXIT_WITH" "5"})))
  (<- seen (read-to-end handle LONG))
  (assert (and (isinstance seen.state RunStopped) (= seen.state.exit-code 5) (= seen.state.cause StopCause.EXITED)) (repr seen)))

(deftest test-a-run-killed-by-a-signal-from-outside-ends-with-the-shell-exit-code
  ;; 止めの頼みでなく signal で殺された run の終了 code は、shell と docker と同じ 128 + signal の番号(process の戻り値の負の値のままにしない)。
  ;; 頼まれた止めではないので、止まった訳は EXITED。
  {:interpreters ["process"]}
  (<- s (settings))
  (<- handle (begin (RunSpec :image s.image :command #("sh" "-c" "kill -9 $$"))))
  (<- seen (read-to-end handle LONG))
  (assert (and (isinstance seen.state RunStopped) (= seen.state.exit-code 137) (= seen.state.cause StopCause.EXITED)) (repr seen)))

(deftest test-two-stops-at-once-wait-for-the-same-stop
  ;; 止めを同時に 2 つ頼むと、2 つ目は始まった止めの終わりを待ち、同じ答えを返す(猶予は最初の頼みの物 — 2 つ目の長い猶予まで待たない)。
  {:interpreters ["process"]}
  (<- s (settings))
  (<- handle (begin (stubborn-spec s.image)))
  (<- _ (read-until-live handle))
  (<- began (GetMonotonic))
  (<- first (Spawn (stop-with handle 1.0)))
  (<- second (Spawn (stop-with handle 30.0)))
  (<- one (Wait first))
  (<- other (Wait second))
  (<- ended (GetMonotonic))
  (assert (and (isinstance one RunStopped) (= one.cause StopCause.REQUESTED) (= one other)) (repr #(one other)))
  (assert (and (>= (- ended began) (- 1.0 s.slack)) (< (- ended began) 10.0)) (- ended began)))
