;;; Executable ADR: 未送信 prompt の検知と有界補償 + 終端 session の単一掃き取り
;;; (issue #568 根治 — #582/#583 の残 2 穴〔救援キー送出・報告督促の宛先無検証〕
;;; と併設裁定〔片付けの単一掃き取り化〕を同便で法制化する)。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-010
  :title "未送信 prompt は composer 領域全体で検知し、補償は有界 budget + 超過で loud typed terminal(無限 blocked の構造的禁止)。awaiting_response latch は期限つき(観測断で窓再スタート)。monitor の送出は pane の session 帰属を毎 cycle 検証した宛先にのみ行う。substrate cleanup(tmux kill + cleaned_at)は終端行全体を拾う単一の掃き取りが所有し、終端経路ごとの片付け命令は禁止する"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/markers.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy"
          "packages/doeff-agents/src/doeff_agents/tmux.py"
          "packages/doeff-agents/conformance/README.md"]
  :problem
    [(fact
       "2026-07-28、ACP invocation inv_wi_dbaf5df9a812dba_a1 の launch prompt が composer に [Image #150] 添付チップとして未送信のまま残留し、turn が一度も開始されないまま session が blocked で 2 時間沈黙した。has-unsubmitted-paste(markers.hy)と _output_has_unsubmitted_paste_input(tmux.py)は最終 prompt 行 1 行しか見ず、prompt 行の外(直下の行)に描かれる添付チップに盲目 — 17 回の Enter 再送はすべて無効で、pane が語彙外形へ変形すると補償器は完全に沈黙した。"
       :evidence "proboscis/doeff issue #568 本文(agentd DB agent_session_events session_id agent_inv_wi_dbaf5df9a812dba_a1・pane capture /tmp/wedged-attend-pane-full.txt)")
     (fact
       "2026-08-06、同型の未送信 prompt 席が ACP runner 7 席を全飽和させ(14.7〜44.7h 占有・再送 32 回 / 13 回で沈黙・7 席とも会話記録ゼロ)、後続 66 件が最長 7 日停止した。3 つの終端機構(起動時間切れ・turn 終了検出・静止時間切れ)は『入力欄が表示されている』という健全な席と同じ見た目で同時に無効化される — 時間では構造的に解けない。"
       :evidence "agora artifact analysis-acp-responsibility-sessionhost-steward-2026-08-06.html(7 席全数実測 + 母集団 6,771 行)・ACP 決裁行 sandbox:responsibility:sessionhost-steward gen14 の operator 裁定 2026-08-06")
     (fact
       "awaiting_response=1 は stall watchdog と turn-end 検出を丸ごと無効化する(#582 穴 c)。latch の解除条件は正の作業証拠の観測のみで期限が無く、『促したのに永久に無応答』の経路では『無限待ちは禁止』の宣言(policy.hy R5/R7)が失効する。"
       :evidence "proboscis/doeff issue #582/#583(2026-08-01 ep9: status=blocked・awaiting_response=1・budget 使い切りのまま恒久静止)— #568 へ統合(統合コメント 2026-08-06)")
     (fact
       "monitor の救援キー送出(send-unblock-keys)と報告督促の配達(deliver-message)は row.pane_id へ盲目的に送り、宛先 pane の session 帰属を誰も検証しない(#582 穴 a/b)。実測では救援キーが会話を裏画面へ退避させ(報告道具の喪失)、督促文が新規 bg session を 1 つ産んだ(誤着弾 1/240)。pane 番号は再利用される(台帳実測 1,483 衝突)。"
       :evidence "proboscis/doeff issue #582(analysis-acp-invariant-attention-verdict-stale-2026-08-01-ep9.html 付録 F・2026-08-01 11:12Z の秒単位一致)")
     (fact
       "substrate cleanup(tmux-kill-session + cleaned_at)は finalize の done/failed 観測経路にしか付いておらず、5 つの終端経路のうち 4 つ(起動時間切れ・boot 残置・旧観測断・素シェル復帰)は行を終端にして (return row) で抜けるため片付けが一度も呼ばれない。実測: 残骸 26 台・6.155 GiB(刻印率 done 100% / failed 83.5% / exited 2.9% / stopped 1.8%)。"
       :evidence "analysis-acp-responsibility-sessionhost-steward-2026-08-06.html 付録『片付けが呼ばれない理由』(残骸 26 台と終わり方の 1:1 照合)")]
  :context
    [(interpretation
       "検知の型: 未送信の証拠は『prompt 行』ではなく『composer 領域』(最終 prompt 行とそれ以降の行)に描かれる。添付チップ [Image #N] は prompt 行の直下・行頭空白で描かれ、prompt 行 1 行の走査は構造的に盲目。逆に、最終 prompt 行より上は送信済み履歴であり対象外(履歴チップを未送信と誤検知しない)。")
     (interpretation
       "補償の型(fail-fast): 補償器(Enter 再送)は必ず有界 budget を宣言し、超過は loud typed terminal failure(retryable=true — 未送信 prompt は輸送系の transient であり、attempt の再試行は二重実装にならない)。沈黙 blocked は禁止。【2026-08-12 関係登記(ADR-DOE-AGENTS-011)】起動段の ready gate が『貼り付け可能性(probe の消費と消去)』を必要条件に持つようになったため、この arm は配送前の門を通り抜けた形に対する backstop の位置になる — 本則(launch 側の同期 confirm を伸ばさない)は不変で、011 が足したのは配送の前段であり confirm の延長ではない。行動系終端は provider-limit 観測を先に見る(ACP ADR 0049 R9 と同じ蒸留 — attempt 中に blocked_api を latch 済みなら rate_limited)。")
     (interpretation
       "awaiting latch の型: 『agent への prompt が owed』は期限つきの命題である。正の作業証拠(active marker / turn activity)が期限内に来なければ typed terminal — これは issue #568 期待動作 (c)(送信確認は turn 開始の正の証拠を要求する)の level-triggered 実装で、launch 側の同期 confirm を伸ばす形(ハザード 4 の盲窓拡大)を意図的に退けた。期限の基点は観測断で再スタートする(ADR-DOE-AGENTS-009 R2 と同型 — 観測できなかった窓は『観測し続けたのに証拠が無い』premise を void にする)。")
     (interpretation
       "宛先の型: pane_id は再利用される番号であり、session 帰属の検証なしにキー・メッセージを送ることは『他人の席への送出』を構造的に許す。帰属検証は送出 site ごとの guard ではなく、観測入口の単一 arm(毎 cycle)で行う — その cycle の全下流送出(Enter 再送・dialog dismiss・救援キー・督促配達)が同じ検証済み宛先に束ねられる。帰属の不一致は tmux が応答した上での積極証拠 = vanished(ADR-DOE-AGENTS-009 の第 2 証拠クラス)。")
     (interpretation
       "片付けの型(mechanism vs policy): 終端経路ごとに片付け命令を足す形は、次の経路でまた漏れる(実測: 5 経路中 4 経路が漏れ)。片付けは『終端状態に達した行をすべて拾う単一の掃き取り』= 終端という end-state を観測する宣言的 reconciler が所有する。launch ready-gate の inline cleanup(launch.hy)は launch pipeline 所有の先行最適化として残る — その失敗(cleaned_at NULL 残置)も掃き取りが修復する。掃き取りは既存残骸(26 台)も同じ機構で回収する — 人手の一括掃除は不要。")
     (interpretation
       "掃き取りの安全条項: (a) 刈り取り免除(ADR-DOE-AGENTS-007 安全条項 1)を継承する — 対象は run_to_completion かつ非 adopted の終端行のみ。(b) session 名は呼び手採番で時間軸上再利用され得る — active 行が同名を主張しているときは kill せず cleaned_at のみ刻む(古い残骸の名で生きている新席を殺さない)。(c) cleaned_at の意味は『掃き取りが残骸なしを確認した』— tmux session が既に無い行にも刻む(対象集合が有界に収束し、毎 cycle の再走査を許す)。")]
  :decision
    [(rule R1 "未送信検知は composer 領域全体を見る: has-unsubmitted-paste(markers.hy)・unsubmitted-paste-input?(substrate.hy)・_output_has_unsubmitted_paste_input(tmux.py)は、末尾窓の最終 prompt 行(❯ / › / input:)とそれ以降の行を走査し、[Pasted text / [Pasted Content / [Image #N チップ / Press up to edit queued messages を未送信 marker として検出する。最終 prompt 行より上(送信済み履歴)は対象外のまま。")
     (rule R2 "paste 再送補償器は有界: SessionRow.paste_resubmit_attempts(durable counter)と knob paste-resubmit-limit(凍結既定 5、env DOEFF_AGENTD_PASTE_RESUBMIT_LIMIT)を持ち、budget 内は Enter 再送 + session_unsubmitted_paste_resubmitted event、超過は typed terminal failure(status=failed・reason 接頭 `unsubmitted-prompt:`・cause prompt_undelivered retryable=true、api-limit latch 済みなら rate_limited)。【2026-08-12 改訂(ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8)】超過時の cause category は timed_out から prompt_undelivered へ改める — この arm が終端させる行は turn が一度も始まっていない(実測 25 件全数で turn-activity marker 不在)ため、起動段 gate の未配達と同じ命題であり、2 つの category に割れていると未配達の集計が割れる。retryable=true と api-limit 先読みの蒸留は不変。この category を変える改訂は ADR-DOE-AGENTS-011 と一括で出荷する。")
     (rule R3 "awaiting_response latch は期限つき: SessionRow.awaiting_response_since(solicitation 再武装で更新・正の作業証拠で latch と同時に clear)と knob awaiting-response-timeout-seconds(凍結既定 600、env DOEFF_AGENTD_AWAITING_RESPONSE_TIMEOUT_SECS)を持つ。期限の基点 = max(awaiting_response_since | started_at, observation_gap_at)。超過は typed terminal failure(status=failed・reason 接頭 `awaiting-response timeout:`・cause timed_out retryable=true、api-limit latch 済みなら rate_limited)。これにより『無限 blocked』は構造的に不能になる(#582 穴 c の根治)。【2026-08-09 関係登記(ACP ADR 3d0ff3 wait-bound-declared-ownership)】この timeout は『返事待ち』2 層構成の第一層(所有)である — ACP 帳簿側の blocked-input-dwell bound(既定 1800s)は sessionhost の裁定が観測経路で届かない場合の backstop であり、順序不変量 = 本 knob の実効値 < ACP backstop bound。本 knob の凍結既定(600)を変える変更は ACP ADR 3d0ff3 の登記値(SESSIONHOST-FIRST-LAYER-TIMEOUT-SECONDS)の改訂と一括出荷する。")
     (rule R4 "宛先 pane の帰属検証: monitor は毎 cycle、tmux 生存確認の直後に TmuxSessionPaneIds(tmux list-panes -s / herdr は agent の pane 解決 — 両 substrate が同じ契約を実装する)で row.session_name の所有 pane 集合を観測し、row.pane_id が属さなければ exited + cause vanished(reason に pane と session を明記)で即時終端する。以降の全送出(Enter 再送・dialog dismiss・救援キー・督促配達)はこの検証を通過した宛先にのみ行われる(#582 穴 a/b の根治)。付随契約改訂: 『session 生存のまま pane が帯域外 kill』の形(旧 conformance S19c が供給断のシミュレーションに使っていた)は、substrate が応答した上での帰属喪失 = 第 2 証拠つき死亡へ昇格する — 供給断(ADR-DOE-AGENTS-009 R1 の hold + gap 刻印)は probe/capture が応答なしで失敗する形に限られ、その検証の家は hy deftest gate に移る。")
     (rule R-blocked-needs-no-work-evidence-4f1c "観測 status の waiting 腕は作業証拠との連言である: observed-status-from-markers(policy.hy)の凍結分類順は failure → api-limit → (waiting ∧ ¬active) → running であり、live active marker(claude spinner / codex working 行)が見えている観測を blocked と分類してはならない。理由 = has-waiting-marker の判定材料 7 語のうち accept edits / bypass permissions / shift+tab to cycle は現行 claude TUI の permission-mode インジケータ、すなわち作業中も常時描画される常設フッターであり(しかも他 marker が tail 30 行窓なのに waiting だけ capture 全文一致)、単独では『入力待ち』を意味しない。連言に has-turn-activity(⏺ / ⎿)は含めない — あれは idle 画面にも残留する痕跡であって live の作業証拠ではなく(markers.hy の逐語: latch clear と startup watchdog 解除の用途に限る)、含めると一度でも作業した claude 席は二度と blocked にならず本当に固まった席が不可視化する。marker 検出(impl 所有)は不変 — 変わるのは分類(policy 所有)のみで、PaneObservation は両事実を今までどおり並べて運ぶ。挙動契約の正本(conformance README の F-* 表 F-waiting 行)を同便で改訂する。【R3 との関係】第一層 600s は『促したのに正の作業証拠が来ない』を測り、本 rule は『作業証拠が来ているのに blocked と名乗る』を禁じる — 同じ作業証拠の 2 つの用法であり、第二層(ACP backstop)が読む status の意味をこの rule が保証する。")
     (rule R5 "substrate cleanup は単一の掃き取りが所有する: monitor-cycle は per-session loop の後に、終端 status かつ cleaned_at IS NULL かつ run_to_completion かつ非 adopted の全行(SessionStoreListCleanupPending)を掃き取る — tmux session が生きていれば kill(session_cleaned event)、active 行が同名を主張していれば kill せず、いずれも cleaned_at を刻む。finalize 内の inline cleanup は撤去し、monitor の終端 arm に片付け命令を足すことを禁止する(semgrep doeff-agents-terminal-cleanup-single-sweep)。launch ready-gate の inline cleanup(launch.hy)は launch pipeline 所有として残り、その失敗は掃き取りが修復する。")]
  :laws
    [(law composer-region-owns-unsubmitted-vocabulary
       :statement "unsubmitted_detection(output) scans composer_region(last_prompt_line ..); attachment_chips_below_prompt_are_unsubmitted; history_above_last_prompt_is_not"
       :counterexamples
         [(counterexample "2026-07-28 実 wedge: 空の ❯ prompt + 直下の `  [Image #150]` チップを『送信済み』と誤答 — 17 回の再送が全て無効のまま 2h 沈黙(prompt 行 1 行だけを見る旧実装)")
          (counterexample "最終 prompt 行より上の履歴チップ(`❯ [Image #3]` の送信済み行)まで unsubmitted と読む — 健全な席に Enter を注ぎ続ける逆向きの誤検知")])
     (law unsubmitted-prompt-compensation-is-bounded
       :statement "resubmit_compensation(row) => budgeted(paste_resubmit_limit) and exhaustion_is_loud_typed_terminal(retryable=true); silent_blocked_is_forbidden"
       :counterexamples
         [(counterexample "2026-08-06 runner 全飽和: 再送 32 回 / 13 回で沈黙し、以後 2 日間無音のまま blocked — 補償器に回数上限も失敗時終端も無く、7 席が 66 件を 7 日停止させた")])
     (law awaiting-latch-has-deadline
       :statement "awaiting_response(row) => bounded_wait(base=max(awaiting_response_since|started_at, observation_gap_at), awaiting_response_timeout) ; no_work_evidence_within_deadline => typed_terminal"
       :counterexamples
         [(counterexample "#582 ep9(2026-08-01): awaiting_response=1 が stall watchdog と turn-end 検出を無効化し、budget 使い切りの死席が finished_at=None のまま恒久静止 — 監督は 1 秒ごとに観測を続けたのに終端へ導く経路がゼロ")
          (counterexample "観測断の窓を期限に数える — 供給断で証拠が構造的に来ない行を timed_out で誤終端する(ADR-DOE-AGENTS-009 の型公理 violation。窓は gap で再スタートする)")])
     (law blocked-status-requires-absence-of-work-evidence
       :statement "observed_status(obs) = blocked => waiting_marker(obs) ∧ ¬active_marker(obs) — 常設 UI 文字列は『入力待ち』の証拠ではない。かつ ¬(turn_activity ∈ 連言): 残渣 marker を作業証拠に数えると blocked が到達不能になる"
       :counterexamples
         [(counterexample "2026-08-06..12 の壁帯 218 席(agentd.sqlite 直読・寿命 1860..2000s で cancelled): 142 席(65%)は死の 10 秒前まで画面が変化していた — 8〜9 万トークンを走らせている最中の席が『指示が届かなかった席』として片付けられた。真に静止していたのは 51 席のみ。逐語の現物 = session agent_inv_wi_5f3fa0e22242b74d_a1 の死亡時 capture『✶ Whatchamacalliting… (31m 5s · ↓ 91.4k tokens)』+ 常設フッター『⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt』")
          (counterexample "連言に has-turn-activity(⏺ / ⎿)を入れる: 一度でも作業した claude 席は idle 画面でも痕跡が残るため二度と blocked にならず、2026-08-05..06 の凍結 7 席(22h27m ゼロ生産・composer 空)が status 上『running』として不可視になる — 過剰修正は上流 backstop の前提そのものを壊す")])
     (law delivery-target-must-be-owned
       :statement "monitor_send(keys|message, pane) requires verified(pane belongs_to row.session_name, this cycle); ownership_mismatch_is_evidenced_vanish"
       :counterexamples
         [(counterexample "#582 穴 a(2026-08-01 11:12:17Z): 凍結画面への救援キー送出が会話を裏画面へ退避させ(claude daemon run --origin transient の起動と秒単位一致)、報告道具(--strict-mcp-config の doeff_result)を喪失させた")
          (counterexample "#582 穴 b: 督促文が背景化後の『describe a task for a new session』入り口に着弾し新規 bg session を 1 つ産んだ(誤着弾 1/240)— 死んだ席が 60 分『生きた鼓動』を出す事故に加担")])
     (law terminal-cleanup-single-sweep
       :statement "substrate_cleanup(tmux_kill + cleaned_at) is owned by one sweep over all terminal ∧ uncleaned ∧ run_to_completion ∧ ¬adopted rows; per-arm cleanup commands are banned; sweep_must_not_kill(name claimed by active row)"
       :counterexamples
         [(counterexample "片付けを done/failed 観測経路にだけ内蔵する(旧 finalize)— 5 終端経路中 4 経路が漏れ、残骸 26 台・6.155 GiB が堆積した(刻印率が経路ごとに 100% と 0% に割れる実測)")
          (counterexample "漏れた経路(起動時間切れ)に片付け命令を 1 本足す — 次の経路(素シェル復帰・新設 arm)でまた漏れる。operator 裁定(2026-08-06)が明示的に却下した形")
          (counterexample "呼び手採番の session 名が再利用されたとき、古い残骸行の名で kill を撃つ — 同名で生きている新席を殺す(active 行が同名を主張する間は cleaned_at のみ刻む)")])]
  :enforcement
    [(deftest test-adr-doe-agents-010-knobs-and-fields-frozen
       ;; R2/R3 の機械面: budget knob と durable field が凍結既定値で実在する。
       (import doeff_agents.sessionhost.effects [MonitorKnobs SessionRow])
       (setv knobs (MonitorKnobs))
       (assert (= knobs.paste-resubmit-limit 5))
       (assert (= knobs.awaiting-response-timeout-seconds 600))
       (setv row (SessionRow :session-id "adr10" :session-name "adr10" :pane-id "%0"
                             :agent-type "claude" :lifecycle "run_to_completion"
                             :status "running"
                             :started-at "2026-08-06T00:00:00+00:00"))
       (assert (= row.paste-resubmit-attempts 0))
       (assert (is row.awaiting-response-since None)))
     (deftest test-adr-doe-agents-010-cleanup-single-home
       ;; R5 の機械面: policy.hy の tmux-kill-session 呼び出し site は掃き取り
       ;; 関数(sweep)内の 1 箇所のみ — finalize / 終端 arm への回帰を禁止する。
       (import pathlib [Path])
       (import re)
       (import doeff_agents.sessionhost.policy :as policy-mod)
       (setv source (.read-text (Path (. policy-mod __file__)) :encoding "UTF-8"))
       (setv call-sites (.count source "(tmux-kill-session"))
       (assert (= call-sites 1)
               f"policy.hy must contain exactly one (tmux-kill-session call site (the sweep), got {call-sites}")
       ;; 各トップレベル定義ブロックに区切り、kill site が掃き取り関数の
       ;; ブロックに居ることを確認する(finalize 内なら fail)。
       (setv blocks (re.split r"(?m)^\(" source))
       (setv owner-blocks (lfor block blocks
                                :if (in "(tmux-kill-session" block)
                                block))
       (assert (= (len owner-blocks) 1))
       (setv owner (get owner-blocks 0))
       (assert (.startswith owner "defk cleanup-terminal-session-once")
               "tmux-kill-session must live inside the sweep (cleanup-terminal-session-once)")
       (assert (not-in "(tmux-kill-session" (get (re.split r"(?m)^\(defk cleanup" source) 0))
               "no tmux-kill-session call before the sweep definition (e.g. inside finalize)"))
     (deftest test-adr-doe-agents-010-composer-region-vocabulary
       ;; R1 の機械面: 実 wedge の pane 形([Image #N] チップが prompt 行の外)を
       ;; 3 実装(markers.hy / substrate.hy / tmux.py)すべてが unsubmitted と読む。
       (import doeff_agents.sessionhost.impls.markers [has-unsubmitted-paste])
       (import doeff_agents.sessionhost.substrate [unsubmitted-paste-input?])
       (import doeff_agents.tmux [_output-has-unsubmitted-paste-input])
       (setv wedge "❯\n  [Image #150]\n\n  ⏵⏵ bypass permissions on (shift+tab to cycle)")
       (assert (has-unsubmitted-paste wedge))
       (assert (unsubmitted-paste-input? wedge None))
       (assert (_output-has-unsubmitted-paste-input wedge))
       ;; 送信済み履歴のチップ(最終 prompt 行より上)は対象外のまま。
       (setv history "❯ [Image #3]\n⏺ done\n❯")
       (assert (not (has-unsubmitted-paste history)))
       (assert (not (unsubmitted-paste-input? history None)))
       (assert (not (_output-has-unsubmitted-paste-input history))))
     (deftest test-adr-doe-agents-010-blocked-needs-no-work-evidence
       ;; R-blocked-needs-no-work-evidence-4f1c の機械面(法の連言そのもの)。
       ;; 分類は純関数なので PaneObservation を直接組んで固定する — 実物 pane
       ;; からの marker 検出は impls 側 deftest
       ;; (test-classify-claude-working-pane-carries-both-marker-facts)が
       ;; 逐語で固定し、cycle 込みの挙動は policy 側 deftest
       ;; (test-working-pane-with-waiting-footer-stays-running)が固定する。
       (import doeff_agents.sessionhost.effects [PaneObservation])
       (import doeff_agents.sessionhost.policy [observed-status-from-markers])
       ;; 常設フッター(waiting)だけ = 従来どおり blocked。
       (assert (= "blocked"
                  (observed-status-from-markers
                    (PaneObservation :has-waiting-marker True))))
       ;; 働いている pane(waiting ∧ active)は blocked にしない — 壁帯 142 席。
       (assert (= "running"
                  (observed-status-from-markers
                    (PaneObservation :has-waiting-marker True
                                     :has-active-marker True))))
       ;; 残渣 marker(turn-activity)は連言に入らない — 過剰修正の禁止。
       (assert (= "blocked"
                  (observed-status-from-markers
                    (PaneObservation :has-waiting-marker True
                                     :has-turn-activity True))))
       ;; 上位の分類順は不変(failure / api-limit は active があっても勝つ)。
       (assert (= "failed"
                  (observed-status-from-markers
                    (PaneObservation :has-failure-marker True
                                     :has-active-marker True))))
       (assert (= "blocked_api"
                  (observed-status-from-markers
                    (PaneObservation :has-api-limit-marker True
                                     :has-active-marker True)))))
     (defsemgrep per-arm-cleanup-is-banned
       "doeff-agents-terminal-cleanup-single-sweep"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
         "source" "(when (and (is-run-to-completion row.lifecycle)\n           (in observed-status #{\"done\" \"failed\"}))\n  (<- still-alive (tmux-has-session row.session-name))\n  (when still-alive\n    (<- _ (tmux-kill-session row.session-name))\n    (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
         "source" "(defk cleanup-terminal-session-once [row active-names]\n  (<- alive (tmux-has-session row.session-name))\n  (when (and alive (not-in row.session-name active-names))\n    (<- _ (tmux-kill-session row.session-name)))\n  (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))\n  row)\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_010_unsubmitted_prompt_terminal_sweep.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/markers.hy(R1 composer 領域)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy(R1 confirm ループ面 + TmuxPaneSessionName 実 IO)"
          "packages/doeff-agents/src/doeff_agents/tmux.py(R1 legacy transport 面)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy(R2/R3 knob・field + TmuxPaneSessionName / SessionStoreListCleanupPending)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy(R2 有界再送 + R3 期限 arm + R4 帰属検証 arm + R5 sweep・finalize inline cleanup 撤去)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy(additive 列 2 本 + cleanup-pending 一覧 + latch clear の since 同時 clear)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy(env knob 2 本)"
          ".semgrep.yaml(doeff-agents-terminal-cleanup-single-sweep)"
          "packages/doeff-agents/conformance/README.md(knob 表・cleanup 契約の改訂)"])
