;;; Executable ADR: 観測断 ≠ 死亡 — sessionhost watchdog の terminal lost 撤去 +
;;; lost→vanished 語彙分割 + exited 後の遅延 result 受理(ACP ADR 0067 R6 の
;;; doeff 側 root fix)。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-009
  :title "観測断 ≠ 死亡: 観測の途絶は観測経路(supply)の命題であり被観測対象の死亡命題ではない — stale-observation watchdog は terminal 化せず observation_gap_at へ記帳して第 2 証拠 arm に裁定を委ね、証拠つき死亡は lost から分割した vanished(retryable=true)で即時終端し、死亡裁定クラス(exited)への遅延 report_result は result-first で受理して done へ上書きする"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy"
          "packages/doeff-agents/conformance (S9 / S19)"]
  :problem
    [(fact
       "2026-07-27 20:10-21:01 JST、sessionhost の store 肥大 wedge(doeff PR #564 で根治)の観測断窓で、stale-observation watchdog が生存確認をしないまま cause=lost{reason=\"no monitor observation for more than 300s\", retryable=true} の terminal を全 session に刻印し、tmux で実作業継続中の agent(wi_ee07c6a71fa945a5)の invocation が下流(ACP engine)で死亡扱いになった。同一署名は 2026-07-11 以降 9 件が系統的に再発している。"
       :evidence "ACP event store sequence 3640543/3640544・3640713/3640714; ~/.local/state/doeff/sessionhost-wedge-sample-20260727.txt; ACP docs/adr/defadr_0067_observation_supply_cut_is_not_death.hy :problem")
     (fact
       "lost category は『watchdog 観測断』(死亡証拠なし)と『tmux session disappeared / pane returned to idle shell』(第 2 証拠つき死亡)を同一語彙(cause=lost, retryable=true)に畳んでおり、構造 field では区別できない。ACP ADR 0067 で engine は cause.category=lost を一律 AgentUnobserved hold(非終端)にしたため、証拠つき死亡まで deadman gate 待ちになり回復が遅い — 語彙分割は ACP 側 tracked gap(defadr_0067 rule R6)。"
       :evidence "ACP src/Acp/App/Agent/Handler/Agentd.hs decodeSnapshotStatus(category==\"lost\" → StatusLost); doeff policy.hy 旧 make-cause \"lost\" 3 箇所")
     (fact
       "false-lost で terminal 化された session へ実 agent が report_result すると『already reached terminal status ... without a result』(-32003)で拒否され、実装結果が result channel ごと失われる。2026-07-27 実測: wi_ee07c6a71fa945a5 の実装結果が回収不能になった。"
       :evidence "host.hy report-result-op 旧 terminal 拒否 arm; ACP 探索ログ #149")
     (fact
       "stale-observation arm の fall-through 化だけでは launch-timeout watchdog に同型の欠陥が残る: 『started_at から launch timeout 秒、観測し続けたのに active を一度も見ていない』という premise は観測断の窓で不可検証になる(active marker は wedge 中に流れ去る)。wedge 回復 cycle で observed_active_at=None の生存 agent を timed_out(retryable=true)へ誤終端し、ACP transient 自動再試行 = 生存 agent との二重実装を解放する。"
       :evidence "policy.hy launch-timeout arm(premise が観測の連続性に依存); ACP defadr_0067 :context 型公理")]
  :context
    [(interpretation
       "型公理(ACP ADR 0067 と同一): 『monitor の観測が途絶えた』は観測経路(supply)についての命題であり、被観測対象(agent)の死亡命題ではない。供給断の窓では全 agent が一斉に vanish に見える(系統的誤判定)ため、fail-safe は supply-cut ≠ death — 観測断は unknown(判定素材不足)であり terminal 化しない。ACP 側は engine の fail-safe(いかなる観測経路故障でも不可観測を死亡と読まない)、本 ADR は観測の質を所有する doeff 側の root fix で、両者は独立に成立する。")
     (interpretation
       "第 2 証拠の定義: (a) tmux 生存 probe の否定応答(has-session が応答して不在 — server 不在も pane の実死亡を意味する)、(b) zombie reaper(pane foreground が idle shell へ戻った — tmux が応答した上での積極観測)、(c) result 到着(work 完了の直接証拠)。probe 自体の失敗(例外・hang)は供給断であり証拠ではない — per-session 隔離で保持し、有界性は engine 側 deadman gate(ACP ADR 0059: invocation age → InvocationStalled → operator gate)が閉じる。doeff は自分で猶予付き terminal 化を持たない(ACP ADR 0067 却下代替案 2 と同じ理由: 観測が構造的に来ない行の遅延終端は同じ誤判定の遅延版)。")
     (interpretation
       "語彙分割の下流互換: ACP decodeSnapshotStatus は cause.category==\"lost\" のみ StatusLost(hold)へ写し、それ以外の exited は StatusExited(terminal)へ落とす。vanished は既存の構造分岐だけで即時終端に到達する — ACP 側のコード変更は不要。retryable=true は維持(死亡が確証された attempt の再試行は二重実装にならない)。lost は doeff の構築語彙から消える(make-cause の凍結表照合が拒否)— 供給断はもう terminal cause として wire に現れない。")
     (interpretation
       "遅延 result 受理の範囲: 救済は死亡裁定クラス(status=exited — vanished / 旧 lost)のみ。judged failure(failed: solicitation 超過・stall・launch timeout)と操作者裁定(cancelled / stopped)は裁定の書き換えになるため従来どおり -32003 で拒否する。exited への受理は ADR 0035(result-first)の完成形: report_result がいつ着地しても result が勝つ — 死亡裁定は『結果を出さずに消えた』の推定であり、schema 適合 result の到着はその推定の反証そのもの。store の terminal 再活性 guard(ADR-DOE-AGENTS-006)は terminal→active のみ禁じており、exited→done の terminal→terminal 上書きはこの唯一の合法経路として本 ADR が所有する。")]
  :decision
    [(rule R1 "stale-observation watchdog は terminal 化しない: 観測断(max(last_observed_at, observation_gap_at) が閾値超過)を検出したら observation_gap_at(additive 列)へ刻印し session_observation_gap event を 1 回記帳して、通常 arm へ fall-through する(検出条件が gap_at 自身で再武装されるため event 率は 1/stale-secs に有界 — PR #564 の journal 肥大を再発させない)。死亡裁定は直後の第 2 証拠 arm(tmux 生存 probe → result-first / zombie reaper)が下す。probe まで不能なら行は running のまま(unknown 保持)。")
     (rule R2 "launch-timeout watchdog の watch 窓は観測断で再スタートする: 基点 = max(started_at, observation_gap_at)。観測断は『観測し続けたのに active を見ていない』premise を void にする — 供給回復後に改めて launch timeout 秒の連続観測で判定する(genuinely stuck な session は回復後に従来どおり reap される = 有界)。")
     (rule R3 "証拠つき死亡は vanished(retryable=true): tmux session disappeared / pane returned to idle shell の 2 箇所は category=vanished。lost は TERMINAL-CAUSE-RETRYABLE 凍結表から除去し、構築経路を 0 にする(make-cause の precondition が表照合で AssertionError)。reason 文言は不変(下流の reason 再分類禁止 — ACP ADR 0042 — を破る材料を作らない)。")
     (rule R4 "遅延 result の受理(救済経路): status=exited かつ result 未永続かつ contract 有りへの report_result は schema 検証の上で受理し、単一 actor op 内で status=done・result_payload 永続・last_validation_error / terminal_cause クリア・finished_at=受理時刻へ上書き、session_late_result_accepted event を記帳し {\"accepted\": true, \"late_result\": true} を返す。RunToCompletion 行は受理後に RPC 層(actor 外)で finalize と同義の substrate cleanup(tmux kill + cleaned_at)を行う。failed / cancelled / stopped / done(payload 無し)は従来どおり -32003。schema 不適合は従来どおり -32002 + session_result_rejected。")]
  :laws
    [(law unobservability-never-terminalizes
       :statement "stale_observation(session) => no_terminal_write and gap_recorded; death_verdict_requires_second_evidence(tmux_probe_negative or zombie_shell or confirmed_absence)"
       :counterexamples
         [(counterexample "2026-07-27 wedge: 観測断窓の回復時に watchdog が tmux 生存 agent(wi_ee07c6a71fa945a5 — 19:27 起動・21:3x 生存実測)へ lost terminal を刻印し、ACP engine が InvocationFailed へ翻訳 — retry-impl が押されていれば生存 agent との二重実装だった")
          (counterexample "gap event を毎 cycle 記帳する(edge-trigger せず)— blocked のまま静止する行が journal を注ぎ agentd.sqlite を 1.5GB へ肥大させた PR #564 incident の再発")])
     (law launch-premise-voided-by-gap
       :statement "launch_timeout_reap(row) => continuous_observation_window(max(started_at, observation_gap_at), launch_timeout_secs); gap_restarts_window"
       :counterexamples
         [(counterexample "wedge 回復 cycle で started_at 基点のまま launch-timeout を発火させる — wedge 直前に running へ手渡された生存 agent(observed_active_at 未打刻)を timed_out(retryable=true)で誤終端し、ACP transient 自動再試行 = 二重実装を解放する(lost 撤去で露出する同型欠陥)")])
     (law death-vocabulary-is-split
       :statement "terminal_cause_category(evidenced_death) = vanished and retryable(vanished) = true; lost is not constructible"
       :counterexamples
         [(counterexample "証拠つき死亡を lost のまま出荷する — ACP ADR 0067 の engine hold が死亡確定 attempt まで deadman gate 待ちにし、回復が invocation deadline まで遅延する")
          (counterexample "観測断を新カテゴリの terminal として出荷する(名前だけ変えた lost)— supply-cut ≠ death の型公理violation そのもの")])
     (law result-arrival-overrides-death-verdict
       :statement "report_result(status=exited, valid_payload, has_contract) => accepted and status=done and cause_cleared; judged_failures_and_operator_verdicts_stay_rejected"
       :counterexamples
         [(counterexample "2026-07-27 wi_ee07c6a71fa945a5: false-lost 後の report_result が -32003 で拒否され、実装結果が result channel ごと喪失した")
          (counterexample "failed(solicitation 超過)や cancelled にも受理を広げる — 裁定済み failure / 操作者裁定の書き換えは別の法であり、本 ADR は死亡裁定クラスのみを所有する")])]
  :enforcement
    [(deftest test-adr-doe-agents-009-lost-is-not-constructible
       ;; R3 の機械面: lost は凍結表から消え、make-cause が拒否する。
       ;; vanished が retryable=true で表に居る。
       (import doeff_agents.sessionhost.policy [TERMINAL-CAUSE-RETRYABLE make-cause])
       (import pytest)
       (assert (not-in "lost" TERMINAL-CAUSE-RETRYABLE))
       (assert (= (get TERMINAL-CAUSE-RETRYABLE "vanished") True))
       (with [(pytest.raises AssertionError)]
         (make-cause "lost" "x" "2026-07-28T00:00:00+00:00")))
     (deftest test-adr-doe-agents-009-gap-field-exists
       ;; R1/R2 の配線ピン: observation_gap_at が SessionRow と store 列に実在する。
       (import doeff_agents.sessionhost.effects [SessionRow])
       (setv row (SessionRow :session-id "adr9" :session-name "adr9" :pane-id "%0"
                             :agent-type "claude" :lifecycle "run_to_completion"
                             :status "running"
                             :started-at "2026-07-28T00:00:00+00:00"))
       (assert (is row.observation-gap-at None)))
     (defsemgrep lost-cause-is-banned
       "doeff-agents-no-lost-terminal-cause"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
         "source" "(setv row (cause-if-absent\n            row (make-cause \"lost\" \"tmux session disappeared\" observed-at)))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
         "source" "(setv row (cause-if-absent\n            row (make-cause \"vanished\" \"tmux session disappeared\" observed-at)))\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_009_observation_supply_cut.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy(stale arm fall-through + launch 窓再スタート + vanished)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy(SessionRow observation-gap-at)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy(additive 列 + COALESCE(excluded, existing))"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy(report-result-op 救済 arm + RPC 層 cleanup)"
          "packages/doeff-agents/conformance/README.md(TerminalCause 表・S9/S19 改訂)"
          "packages/doeff-agents/conformance/test_s19_watchdogs.py / test_s9_out_of_band_kill.py"])
