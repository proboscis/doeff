;;; Executable ADR: 繰り返し動く処理の loop は、時間でなく出来事で待つ — doeff-events の WaitForEvent で出来事を待ち、
;;; 時間で起きて確かめる形は境界の handler だけに置く。cluster をまたぐ待ちは backend の handler の差し替え(memory /
;;; redis-streams)で答え、Program の側は変わらない(違うのは接続先の値だけ)。
;;;
;;; 出自 = 利用者の決め 2026-10-03 11:4x(Mac の調整役 → cisco-c8 → kn-w37 が記録・逐語は :problem の fact)。
;;; 同じ決めの席の側の記録 = dotfiles の ADR-DOTFILES-027 R-c9c11f05(書く時の助言は code-quality-policy-when-writing-code)。
;;; 待ちの期限と、来るはずの出来事が来ない時の線(業務の期限は出来事にする・基盤の異常は Program に出さない・sim は行き止まりを
;;; 即座に赤にする)の全文は c3-w49 の設計の覚え書き。
;;;
;;; 戻し方: backend は handler を memory の 1 つに戻す。記録を戻すなら、この ADR を足した commit を revert する(ADR の
;;; file 1 つと enforcement 台帳の数が消える)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pathlib [Path])


(defadr ADR-DOE-EVENTS-001
  :title "繰り返し動く処理の loop は WaitForEvent で出来事を待つ。時間は境界の handler だけ。backend は handler の差し替え(memory / redis-streams)で、接続先の値だけが違う"
  :status "accepted"
  :scope ["packages/doeff-events/src/doeff_events"
          "docs/adr/defadr_doeff_events_001_wait_for_events.hy"]
  :problem
    [(fact
       "利用者の決め 2026-10-03 11:4x(逐語 2 通): \"about loop and wait architecture, what if we use doeff-events so we can wait for events instead of time?\" → \"yeah so we want doeff-events handler with cluster-aware backend like redis/rabbitmq etc\""
       :evidence "Mac の調整役(w3J:p17)→ cisco-c8 → kn-w37(agora-redesign #2976 の「待ちの形」の行)")
     (fact
       "backend = Redis Streams(Mac の調整役の戻せる決め)— cursor が在り取りこぼさない・password 無しで動く・1 Pod・手元の 1 台の cluster でも同じ handler。RabbitMQ は候補として残す(製品が user / password を要求する)。"
       :evidence "cisco-c8 の中継(2026-10-03 11:4x)")
     (interpretation
       "時間で起きて確かめる loop は、起きるたびの費用(検の歩数の上限を越える根の類)と、間隔ぶんの遅れを持つ。出来事で待てば、起きるのは何かが変わった時だけで、模擬でも本番でも同じ形で試せる。")]
  :decision
    [(rule R1 "繰り返し動く処理の loop は doeff-events の WaitForEvent で出来事を待つ。時間で起きて確かめる形(WaitUntil・sleep・間隔の設定)を loop に新しく書かない。時間は境界の handler だけに置く。cluster をまたぐ待ちは backend の handler の差し替え(memory / redis-streams)で答え、Program の側は変えない — 違うのは接続先の値だけ(ADR-DOE-CLUSTER-001 R8 の同じ API の決めと揃う)。")]
  :laws
    [(law loops-wait-for-events
       :statement "for_all 繰り返し動く処理の loop l: l が起きるのは WaitForEvent が出来事を返した時だけで、l の中に時間で起きて確かめる待ち(WaitUntil・sleep・間隔の設定)は無い。for_all backend b ∈ {memory・redis-streams}: 同じ Program が b の handler の差し替えだけで動く"
       :counterexamples
         [(counterexample "coordinator の静かな区間を、10 秒の間隔で起きて表を読み直す loop で書く — 起きるたびに費用が掛かり、変化は最大 10 秒遅れて拾われる")
          (counterexample "redis-streams のために、loop の側に接続や cursor の分岐を書く — backend の違いが handler でなく Program に入る")]
       :enforced-by ["test-adr-doe-events-001-public-surface"
                     "dotfiles の助言 code-quality-policy-when-writing-code(ADR-DOTFILES-027 R-c9c11f05)"]
       :wiring "一部配線(2026-10-03)— 検は、決めが頼る doeff-events の公開の口(出来事を出す・待つ・memory の handler)が在る事まで。loop の中の時間の待ちを数える針と、redis-streams の handler は未実装")]
  :enforcement
    [(deftest test-adr-doe-events-001-public-surface
       ;; 針: 決めが頼る doeff-events の公開の口(Publish・WaitForEvent・memory の handler event_handler)が __all__ に在る。
       ;; doeff-events は repo の根の環境に入っていないので、import せず公開の口の file を読む。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root "packages/doeff-events/src/doeff_events/__init__.py") :encoding "utf-8"))
       (for [name ["Publish" "WaitForEvent" "event_handler"]]
         (assert (in (+ "\"" name "\",") text)
                 (+ "doeff-events の公開の口に次が無い(ADR-DOE-EVENTS-001 R1): " name))))]
  :plans ["agora-redesign #2976"])
