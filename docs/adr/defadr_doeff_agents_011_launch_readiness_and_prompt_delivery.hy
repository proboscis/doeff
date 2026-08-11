;;; Executable ADR: 起動段の「準備完了」は貼り付け可能性で定義し、不成立は
;;; 閉語彙で分類する。未配達 attempt は独立の分類(prompt_undelivered)。
;;;
;;; ACP issue sessionhost-launch-ready-gate-vs-timeout-f0ac0f の根治
;;; (operator 裁定 2026-08-04 = 採択 fix-launch-ready-gate / 2026-08-12 =
;;; sessionhost 生死判定便〔issue 55b1bd〕の第二根治「unsubmitted-prompt 救済」を
;;; 本件へ合流。決裁書 decision-acp-sessionhost-liveness-55b1bd-2026-08-12.html
;;; 項目 second-rootcause 案 A)。
;;;
;;; 結合核: ACP core-04(liveness closure)の入口側閉包 +
;;; core-13(invocation execution model)の attempt 境界 —
;;; 法・反例・テスト・実装の一括出荷が必須の車線(frontier + human review)。
;;; 方向宣言 launch-ready-paste-consumption-f0ac0f(確定・operator 裁定)。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-011
  :title "launch ready gate は『入力欄が描かれた』ではなく『この composer が我々の貼り付けを消費し、消去できる』で定義する(入力 loop 配線 ∧ 未送信内容なし ∧ 有界 probe の消費と消去)。不成立は閉語彙 class で自己記述し、証拠 frame を有界複数保持する。prompt が一度も届かなかった attempt は timed_out ではなく prompt_undelivered(独立の分類・retryable=true)"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/markers.hy"
          "packages/doeff-agents/tests/sessionhost_launch_deftests.hy"]
  :problem
    [(fact
       "launch ready gate は 120s の予算切れを単一の固定文言で終端しており、何が見えていたのかを分類として残さない。文言は『startup is blocked by an unrecognized screen (a dialog outside the R9 fast-path set?)』と毎回断定するが、2026-08-11 断面の実物 frame 341 件を分類すると、認識不能画面(A)は 0 件・描画ゼロ(B)24 件・shell echo だけ(B')314 件・最終 frame が既に ready(予算競合)3 件だった。断定は全件で誤りで、steward の診断を毎回誤らせていた。"
       :evidence "agentd.sqlite 直読(terminal_cause_json like '%launch ready gate%' の output_snippet を impls/markers.hy の検出関数へ通した分類)+ ACP issue sessionhost-launch-ready-gate-vs-timeout-f0ac0f の実装依頼書 v1+reqa3c6cd70")
     (fact
       "『何も描画されていない』と『shell echo だけ在る』が事後に区別できない。旧実装は capture の素の末尾 15 行を証拠に残すため、shell echo は画面上部にあり tail は空行だけになる — 実測 24 件の証拠が『改行 14 個』で、空画面と同一の形をしていた。保持数も 1 件で、局面(起動直後 / 予算切れ間際)の弁別ができない。"
       :evidence "同断面の output_snippet 生バイト(len=14 の全件が改行のみ)")
     (fact
       "『timeout』は 1 つでなく 3 種で、category=timed_out で数えると混入する: launch ready gate(341)/ never reached active state within 60s(36)/ launch pipeline did not complete within 180s(4)。発注席は実際に汚染を出した(07-26 を 2→6、07-27 を 16→25 と誤増)。3 種の弁別知識はコードのどこにも無く、人の頭の中にしか無かった。"
       :evidence "同断面の terminal_cause_json.$.reason 別集計 + 実装依頼書の受入条件 (e) 逐語")
     (fact
       "idle prompt を見て即 paste した prompt が composer に collapsed chip として座り、turn が一度も始まらないまま 5 回の Enter 再送を使い切って死ぬ席が実在する(2026-08-11 の 25 件)。25 件全数で turn-activity marker は不在、12 件は chip が 3〜10 個 = 1 回の bracketed paste が断片化して着弾している。再送回数の延長線に救済は無い(1〜4 回で救われた席は 522/522 生還、5 回使い切りは 38 件中 25 件が死亡 = 二峰性)。"
       :evidence "agentd.sqlite の last_validation_error like 'unsubmitted-prompt:%' 全 25 件 + session_unsubmitted_paste_resubmitted event 数別の生還率(1 回 415 / 2 回 59 / 3 回 32 / 4 回 16 件が全生還、5 回 38 件のうち 25 件が unsubmitted 終端)")
     (fact
       "observed_active_at(launch watchdog の解除信号)は startup-finished から立ち、startup-finished は idle prompt の可視だけで真になる。ゆえに『入力欄が描かれた』という同じ 1 frame が ready gate を通し、watchdog を解除し、observed_active_at を刻む — 25 件全数が observed_active_at を持ちながら turn は一度も始まっていない。『活動を観測した』と読める列名が『入力欄を観測した』の意味しか持たないことが、起動段の失敗を働いた席と同じ見た目にしていた。"
       :evidence "同 25 件の observed_active_at(started_at +14〜130s に全件着弾)と死亡 frame の turn-activity marker 不在の突合")
     (fact
       "gate 形式(ready_physics CODEX-READY-PATTERN)は MCP boot 窓を先読み否定で最初から ready から除いていたのに、observation 形式(sessionhost の ready gate)は idle prompt 単独で ready を主張していた。『composer は Starting MCP servers (N/M) が残る間から描かれるが input loop は未配線で、その窓に keys を送ると Enter がロード画面に食われ prompt が入力箱に座ったまま submit されない』という同一物理が、2 形式のうち片方だけで効いていた(ADR-DOE-AGENTS-008 R1 の 2 形式一致の穴)。"
       :evidence "verbatim capture packages/doeff-agents/tests/data/ready_screens/codex_mcp_boot.txt を classify-output へ通すと has-idle-prompt=True(旧 gate は通す)")]
  :context
    [(interpretation
       "準備完了の型: readiness は『画面に何が描かれたか』ではなく『この pane の入力 reader が我々の byte を消費しているか』の命題である。描画と入力 loop の配線は別事象で、TUI は前者を先に見せる。唯一の positive 証拠は、我々が送った短い probe が composer に現れ、消去キーで消えることを観測すること — 描画の proxy(banner が消えた・罫線が出た・静止した)はどれも消費を証明しない。probe は同じ予算の内側で行い、起動段の壁時計を伸ばさない(ACP ADR 0042 R8 の順序不変量 ready gate 120s < orphan grace 150s < scheduler launch deadline 180s を保つ)。")
     (interpretation
       "『composer が空』の定義は未送信チップの不在に限る。prompt 行の本文が空であることを条件に足してはならない — codex の ready composer は prompt 行に回転するプレースホルダ(`› Improve documentation in @filename` 等)を描くため、空文字を要求すると codex の launch が構造的に全滅する(verbatim capture codex_ready.txt で実証)。安定物理は ADR-DOE-AGENTS-010 R1 のチップ語彙のみ。")
     (interpretation
       "分類の型: 不成立は必ず閉語彙の class で自己記述する。固定文言による原因の断定は、観測していない状態を主張する行為であり(実測 0 件の『unrecognized screen』を 341 件に貼っていた)、下流の診断を系統的に誤らせる。分類は事実束(PaneObservation)からの導出で、marker→事実は impl 所有・順序と意味づけは policy 所有(ADR-DOE-AGENTS-004 C1/C2 の分割を跨がない)。分類の入力は capture 全体であり保持 tail ではない — tail だけを見ると shell echo が窓の外へ出て『描画ゼロ』と誤分類する。")
     (interpretation
       "補償の型は変えない: 配送後の未送信検知と有界 Enter 再送は ADR-DOE-AGENTS-010 R2 の level-triggered arm が所有し続ける(launch 側の同期 confirm を伸ばす形はハザード 4 の盲窓拡大として 010 が意図的に退けた)。本 ADR が足すのは配送の『前』の必要条件であり、gate の予算内で完結する。ゆえに 010 R2 との関係は競合ではなく前後段 — gate が強くなった分だけ R2 の arm は backstop になる。")
     (interpretation
       "未配達の型: prompt が一度も届かなかった attempt は『走ったが時間切れ』とは別の事実である。同じ category(timed_out)に混ぜている間、下流(ACP)は 1 turn も走っていない attempt を『走った』と数えないことを機械で表現できず、集計も 2 つの経路(起動段 gate / 配送後の budget 超過)に割れる。retryable=true は維持する — 別の機械・別の負荷での再試行は二重実装にならない(同一 issue の再宣言で成功する実例が在る)。ただし provider 上限告知の画面だけは rate_limited へ蒸留する(失敗の所有者が provider 側であり、未配達で括ると ACP ADR 0049 の failover が引き取れず同じ枠切れへ再試行を積む)。")
     (interpretation
       "弁別知識の家: 終端 reason の種別(3 timeout + 未配達 + awaiting)を数える知識は、読み手(sensor / 集計 / 事後分析)がそれぞれ正規表現を書き直す形では腐る。分類は産出側の単一関数が持ち、reason には閉語彙の class token を載せる。既存の観測面が依存している逐語(`did not become ready` / `launch ready gate`)は接頭辞として保存する — argus sensor と conformance S18/S22 がその substring で起動段の失敗を拾っているため、文言の全面改稿は計器を黙らせる。")]
  :decision
    [(rule R-paste-ready-a71c "ready gate の必要条件は 3 つ: (1) 入力 loop 配線(idle prompt 可視 ∧ MCP boot 中でない — markers.hy is-input-loop-wired)、(2) composer に未送信内容なし(is-composer-clear = composer 領域が描かれている ∧ ADR-DOE-AGENTS-010 R1 のチップ不在。prompt 行の本文の空は条件にしない)、(3) 貼り付け可能性(READY-PROBE-TEXT を bracketed paste し、composer 領域にそれが現れる〔literal 形 / collapsed chip 形の両方を消費の証拠とする〕ことと、READY-PROBE-CLEAR-KEY を probe 文字数ぶん送って composer が空へ戻ることを観測する)。3 条件は同一の max-wait 予算の内側で判定し、予算を延長しない。")
     (rule R-frame-class-6f3d "起動段の不成立は閉語彙 LAUNCH-NOT-READY-CLASSES(no-output / no-agent-frame / provider-limit-screen / dialog-not-dismissed / unknown-dialog / unrecognized-screen / mcp-boot-window / composer-occupied / paste-not-consumed / composer-not-clearable / deadline-race)のいずれかで自己記述する。予算切れ側の分類は policy の launch-not-ready-class が PaneObservation + capture 全体から導出する単一の家で、reason は launch-not-ready-reason が組む(`launch ready gate [<class>]: <agent> REPL did not become ready within <budget>s; the prompt was never delivered`)。原因を断定する固定文言(『unrecognized screen』等)を返す形は禁止。")
     (rule R-evidence-frames-9c17 "失敗時の証拠 frame は有界複数保持(READY-GATE-FRAME-RETENTION=3・1 枚 READY-GATE-FRAME-LINES=15 行)。保持は先頭(起動直後)を固定し最新側を入れ替え、各 frame に gate 開始からの経過秒を付す。tail を取る前に末尾の空行を落とす(素の tail は shell echo だけの画面を『改行だけ』に潰し、描画ゼロと区別不能にする)。予算切れ後に追加 capture して 1 枚だけ残す形は禁止 — gate が見た画面を残す。")
     (rule R-unknown-dialog-loud-4e02 "R9 fast-path の外の dialog 形(markers.hy is-dialog-shaped: prompt glyph 直後の番号つき option / 確認文言)を観測したら、dismissal キーを推測せず即 loud に落ちる(class=unknown-dialog)。判定を idle prompt の有無で緩めてはならない — codex の trust dialog は選択 marker が `›` なので idle prompt 判定を通過する。起動直後に provider 上限告知(api-limit marker)を観測した場合は class=provider-limit-screen かつ category=rate_limited(policy の launch-not-ready-category が唯一の家)。")
     (rule R-undelivered-first-class-b5e8 "TerminalCause category に prompt_undelivered(retryable=true)を追加し、prompt が一度も届かなかった終端はこれを使う: (a) 起動段 gate の不成立(provider-limit-screen を除く全 class)、(b) ADR-DOE-AGENTS-010 R2 の paste 再送 budget 超過(同 R2 の `cause timed_out` はここで改訂 — api-limit latch 済みなら rate_limited のまま)。launch は配送が済むまで running を書かない(未配達の attempt を『走った』と数えない)。未知 category に対する下流の既定は CommandNonZeroExit + causeRetryable なので、旧 engine に対しても無害。")
     (rule R-timeout-kind-single-home-3c5f "sessionhost が産む終端 reason の種別分類は policy の SESSIONHOST-TIMEOUT-KINDS / sessionhost-timeout-kind が単一の家(launch-ready-gate / launch-never-active / launch-pipeline-incomplete / unsubmitted-prompt / awaiting-response)。読み手はこの分類を再実装しない。既存観測面が依存する逐語(`did not become ready` / `launch ready gate` / `unsubmitted-prompt:` / `awaiting-response timeout:`)は接頭辞として保存する。産出 site が増えるときは同時にこの表へ登録する — 語彙外(unclassified)は gate error。")]
  :laws
    [(law launch-ready-requires-consumed-paste
       :statement "ready(pane) => input_loop_wired(pane) and composer_clear(pane) and probe_consumed(pane) and probe_cleared(pane); drawn_composer_alone_is_not_ready; probe_within_same_budget"
       :counterexamples
         [(counterexample "2026-08-11 の 25 席: idle prompt の 1 frame で ready と裁定し 1238 行の prompt を貼った — composer は chip として受け取り(12 席は 3〜10 個に断片化)、Enter は断片の隙間に食われ turn は一度も始まらず、5 回の再送を使い切って unsubmitted-prompt で死んだ")
          (counterexample "MCP boot 窓(`Starting MCP servers (1/2)` + `› `)を ready とする — gate 形式は同じ物理を最初から除いていたのに観測形式だけが通し、Enter がロード画面に食われた(verbatim capture codex_mcp_boot.txt)")
          (counterexample "『prompt 行に文字が無い』を空 composer の条件に足す — codex の ready composer は回転プレースホルダを描くため codex の launch が全滅する(verbatim capture codex_ready.txt)")
          (counterexample "probe の消費待ちを予算の外に置く(gate の壁時計を伸ばす)— ACP ADR 0042 R8 の順序不変量(ready gate < orphan grace 150s < launch deadline 180s)が崩れ、正常な cold start が orphan 誤分類される")])
     (law launch-not-ready-is-classified
       :statement "not_ready(pane) => failure_class in LAUNCH_NOT_READY_CLASSES and evidence_frames(1 < n <= 3) and classification_input_is_full_capture; fixed_prose_diagnosis_is_forbidden"
       :counterexamples
         [(counterexample "旧実装の固定文言『startup is blocked by an unrecognized screen (a dialog outside the R9 fast-path set?)』— 実測 341 件のうち該当 0 件(描画ゼロ 24 / shell echo だけ 314 / 予算競合 3)。観測していない状態を毎回断定していた")
          (counterexample "分類の入力を保持 tail(15 行)にする — shell echo は画面上部に在るため窓から出て『描画ゼロ』と誤分類される(実測 314 件が丸ごと誤る)")
          (counterexample "証拠を最終 1 枚だけ残す — 起動直後に何が見えていたかが消え、予算切れ間際の 1 枚では (A)/(B) の弁別に足りない(実測: 空行だけの tail が 24 件)")])
     (law undelivered-attempt-is-its-own-class
       :statement "prompt_never_delivered(attempt) => category = prompt_undelivered and retryable = true and not counted_as_ran; provider_limit_screen => category = rate_limited"
       :counterexamples
         [(counterexample "未配達を timed_out に混ぜる — 下流(ACP)が『走ったが時間切れ』と『1 turn も走っていない』を区別できず、受入条件 (d)『prompt 未達で終端した session を走ったと数えない』を機械で表現できない。集計も起動段 gate と配送後 budget 超過の 2 経路に割れる")
          (counterexample "provider 上限告知の画面を prompt_undelivered で括る — ACP ADR 0049 の failover が引き取れず、同じ枠切れへ再試行を積む(実測: 稼働席の画面 283 件中 16 件がこの告知形)")
          (counterexample "未配達を retryable=false(deterministic)にする — 負荷起因の起動段失敗は別断面で成功するため、再宣言できる仕事を人間 gate へ滞留させる")])
     (law timeout-kind-has-one-home
       :statement "terminal_reason(sessionhost) => classifiable_by(sessionhost_timeout_kind) and prefix_verbatim_preserved; readers_must_not_reimplement_the_split"
       :counterexamples
         [(counterexample "category=timed_out だけで日次を数える — 3 種(launch ready gate 341 / never-active 36 / pipeline-incomplete 4)が混入する。発注席は実際に 07-26 を 2→6、07-27 を 16→25 と誤増させた")
          (counterexample "reason の散文を全面改稿して `did not become ready` / `launch ready gate` の逐語を落とす — argus sensor(scripts/sensor_sessionhost_health.py の launch-ready-gate-failure)と conformance S18/S22 が同時に黙る")])]
  :enforcement
    [(deftest test-adr-doe-agents-011-gate-vocabulary-and-knobs-frozen
       ;; R-paste-ready-a71c / R-frame-class-6f3d / R-evidence-frames-9c17 /
       ;; R-undelivered-first-class-b5e8 の機械面: 閉語彙・probe 物理・保持数・
       ;; category 表が宣言どおり実在する。
       (import doeff_agents.sessionhost.effects :as effects-mod)
       (import doeff_agents.sessionhost.policy :as policy-mod)
       (assert (= effects-mod.READY-PROBE-TEXT "doeff-ready-probe"))
       (assert (= effects-mod.READY-PROBE-CLEAR-KEY "BSpace"))
       (assert (= effects-mod.READY-GATE-FRAME-RETENTION 3))
       (assert (= effects-mod.READY-GATE-FRAME-LINES 15))
       ;; 予算は knob 語彙の家のまま(ADR-DOE-AGENTS-008 R2)
       (assert (= effects-mod.REPL-IDLE-MAX-WAIT-SECONDS 120))
       (assert (= policy-mod.LAUNCH-NOT-READY-CLASSES
                  #("no-output" "no-agent-frame" "provider-limit-screen"
                    "dialog-not-dismissed" "unknown-dialog" "unrecognized-screen"
                    "mcp-boot-window" "composer-occupied" "paste-not-consumed"
                    "composer-not-clearable" "deadline-race")))
       ;; 未配達は独立 category・retryable=true
       (assert (= (get policy-mod.TERMINAL-CAUSE-RETRYABLE "prompt_undelivered") True))
       ;; provider 上限だけ rate_limited へ蒸留(単一の家)
       (assert (= (policy-mod.launch-not-ready-category "provider-limit-screen")
                  "rate_limited"))
       (assert (= (policy-mod.launch-not-ready-category "no-agent-frame")
                  "prompt_undelivered"))
       (assert (= (policy-mod.launch-not-ready-category "paste-not-consumed")
                  "prompt_undelivered")))
     (deftest test-adr-doe-agents-011-classification-covers-measured-shapes
       ;; R-frame-class-6f3d の機械面: 2026-08-11 断面に実在した画面の形が、
       ;; それぞれ別の class になる(0 標本の区分を「実装済み」と呼ばないため、
       ;; live に無い形〔unknown-dialog〕は合成標本で撃つ)。
       (import doeff_agents.sessionhost.impls.markers [classify-output])
       (import doeff_agents.sessionhost.policy [launch-not-ready-class])
       (setv shapes
             {"no-output" ""
              ;; 実測 314 件の形(起動 command の echo + zsh の告知)
              "no-agent-frame"
                (+ "The default interactive shell is now zsh.\n"
                   "CA-1:inv_wi_x s22625$ claude --dangerously-skip-permissions "
                   "--model claude-opus-5")
              ;; verbatim capture codex_mcp_boot.txt と同じ形
              "mcp-boot-window" "Starting MCP servers (1/2)\n› "
              "composer-occupied" "› [Pasted text #1 +1238 lines]"
              ;; 合成標本(live 367 件には 0 件)
              "unknown-dialog"
                (+ "Do you trust the files in this folder?\n"
                   "› 1. Yes, proceed\n  2. No, exit\n"
                   "Enter to confirm · Esc to cancel")
              ;; 稼働席の画面 283 件中 16 件に実在した告知形
              "provider-limit-screen"
                (+ "What do you want to do?\n"
                   "› 1. Stop and wait for limit to reset\n"
                   "  2. Ask your admin for more usage\nEnter to confirm")
              ;; 実測 3 件(最終 frame では ready = 予算と描画の競合)
              "deadline-race" "────────────────\n❯ \n────────────────"})
       (for [[expected frame] (.items shapes)]
         (setv obs (classify-output frame))
         (setv actual (launch-not-ready-class obs frame))
         (assert (= actual expected)
                 f"frame の分類が {expected} でなく {actual}")))
     (deftest test-adr-doe-agents-011-timeout-kind-is-total
       ;; R-timeout-kind-single-home-3c5f の機械面: 産出 site の実物 reason が
       ;; 1 つも unclassified に落ちない + 既存観測面が依存する逐語が残る。
       (import doeff_agents.sessionhost.policy [LAUNCH-NOT-READY-CLASSES
                                                launch-not-ready-reason
                                                sessionhost-timeout-kind])
       ;; (i) 起動段 gate: 全 class の reason が launch-ready-gate へ落ちる
       (for [cls LAUNCH-NOT-READY-CLASSES]
         (setv reason (launch-not-ready-reason "claude" 120 cls))
         (assert (= (sessionhost-timeout-kind reason) "launch-ready-gate") reason)
         (assert (in f"[{cls}]" reason))
         ;; argus sensor / conformance が拾う逐語を保存している
         (assert (in "did not become ready" reason))
         (assert (in "the prompt was never delivered" reason)))
       ;; (ii)(iii) monitor 側 watchdog の逐語(policy.hy の 2 site)
       (assert (= (sessionhost-timeout-kind
                    "launch timeout: never reached active state within 60s (stuck in startup — likely a hung MCP server)")
                  "launch-never-active"))
       (assert (= (sessionhost-timeout-kind
                    "launch timeout: launch pipeline did not complete within 180s (BOOTING row left behind — launcher died mid-launch?)")
                  "launch-pipeline-incomplete"))
       ;; ADR-DOE-AGENTS-010 R2/R3 の 2 site
       (assert (= (sessionhost-timeout-kind
                    "unsubmitted-prompt: composer still holds an unsubmitted prompt/attachment after 5 Enter resubmit(s)")
                  "unsubmitted-prompt"))
       (assert (= (sessionhost-timeout-kind
                    "awaiting-response timeout: prompt/solicitation was delivered but no work evidence appeared within 600s (turn never started)")
                  "awaiting-response"))
       ;; 語彙外は unclassified(gate error の材料 — 黙って通さない)
       (assert (= (sessionhost-timeout-kind "some other terminal reason")
                  "unclassified")))
     (deftest test-adr-doe-agents-011-retired-fixed-prose-cannot-return
       ;; R-frame-class-6f3d の tripwire: 実測 341 件で 0 件だった断定文言と、
       ;; 分類を持たない旧 gate(bool を返す wait-for-repl-idle)への回帰を禁止。
       (import pathlib [Path])
       (import doeff_agents.sessionhost.launch :as launch-mod)
       (setv source (.read-text (Path (. launch-mod __file__)) :encoding "UTF-8"))
       ;; 検査対象はコード行のみ — 註釈は退役の経緯として逐語を引用して
       ;; よい(なぜ消えたかを追える価値がある)。産出される文言だけを禁じる。
       (setv code (.join "\n" (lfor line (.splitlines source)
                                   :if (not (.startswith (.lstrip line) ";;"))
                                   line)))
       (assert (not-in "unrecognized screen (a dialog outside" code)
               "退役した断定文言が launch.hy のコード行に戻っている(実測 341 件で該当 0 件)")
       (assert (in "wait-for-launch-ready" source))
       (assert (not-in "(wait-for-repl-idle " source)
               "分類を持たない旧 gate 呼び出しが残っている")
       ;; 予算切れ後の追加 capture で 1 枚だけ残す旧形へ戻っていない
       (assert (in "format-evidence-frames gate.frames" source)))])
