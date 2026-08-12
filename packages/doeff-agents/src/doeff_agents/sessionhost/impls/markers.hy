;;; F-* marker 物理(ADR-DOE-AGENTS-004 C2) — pane capture のテキスト事実。
;;;
;;; observation 形式の readiness/idle 物理の家(ADR-DOE-AGENTS-008 R1)。
;;; gate 形式(単発判定の pattern 文字列・screen-reader trust prompt)は
;;; doeff-free leaf の ready_physics.hy が家 — codex の両形式の一致は
;;; tests/test_ready_physics_single_home.py の parity 検定が執行する。
;;;
;;; 出自: 退役 Rust 実装 agentd-rust-final:src/main.rs の output_has_*
;;; (main.rs:2969-3229)の verbatim 移植 — 退役 Rust は rollback 専用保存で
;;; あり正しさの基準ではない(ADR-DOE-AGENTS-004 R7/U1)。物理の正当性根拠は
;;; conformance README の F-* 表(CONTRACT FIXED 2026-07-05)と verbatim
;;; capture。以下の docstring の「oracle」表記は移植出典関数名の指示のみ。
;;; marker→検出は impl 所有・分類の順序と意味づけは policy 所有
;;; (PaneObservation は事実だけを運ぶ)。例外: has-claude-trust-dialog のみ
;;; 移植元に非在 — 2026-07-07 の R9 カバレッジ欠落(実物 frame で実証)の修正。
;;;
;;; 検出関数は kind 非依存(codex の `› ` と claude の `❯` を同じ関数が
;;; 見る)なので、ここも共有モジュールとして両 impl から使う。kind 分岐が
;;; 生まれたら(C5 opencode)その kind のモジュールへ物理を移す。
;;; 純テキスト関数のみ — IO ゼロ(substrate-clean 領域)。

(require doeff-hy.macros [deff])

(import re)

(import doeff_agents.sessionhost.effects [PaneObservation])
(import doeff_agents.sessionhost.policy [tail-lower])


;; ACP ADR 0049 R9 改訂(2026-08-07。2026-08-06 実 incident、Fable 週次枠):
;; 所有格〜limit 間へ provider 可変語(AI 名・プラン名・期間名)が挟まる
;; exhausted 告知の族。実物告知は「You've reached your Fable 5 limit.
;; /model to switch models.」のように可変語が差し込まれ、逐語列挙は
;; 2026-07-20(usage limit 形)/ 07-26(monthly spend 形)/ 08-06(Fable 5
;; 形・22 件 run_failed 落ち)と 3 度同型で破れた — 語の追加ではなく、
;; 有界可変挿入だけを許す族照合で根治する。有界の設計:
;; - 動詞は exhausted 側(hit|reached)のみ — approaching 側(used NN% of
;;   your … limit)は対象外のまま(まだ動ける pane を blocked_api にしない)
;; - 挿入は空白区切り 0〜4 語。各語は英数開始・英数と +&- の連なり・
;;   内部ピリオド(バージョン番号 4.5 等)のみ許す = 文末ピリオドで族が
;;   切れ、文境界を越えた誤検知(無限定の緩い照合)を構造的に禁じる
;; 対象は tail-lower 済みテキスト(小文字)。apostrophe は ASCII と U+2019
;; の両実物形を許す。
(setv API-LIMIT-EXHAUSTED-FAMILY-RE
  (re.compile
    (+ "you['’]ve (?:hit|reached) your"
       "(?: [a-z0-9][a-z0-9+&-]*(?:\\.[a-z0-9]+)*){0,4} limit\\b")))


;; composer 領域の走査窓(oracle 移植の tail-20 — ADR-DOE-AGENTS-010 R1)。
(setv COMPOSER-TAIL-LINES 20)

;; 未送信チップの語彙(ADR-DOE-AGENTS-010 R1 の逐語集合 — 単一の家)。
(setv UNSUBMITTED-CHIP-MARKERS
      #("[Pasted text" "[Pasted Content" "[Image #"
        "Press up to edit queued messages"))

;; TUI 自身が描く罫線文字(ADR-DOE-AGENTS-011 R-frame-class-6f3d)。
;; welcome box(╭─╮ / │)と composer 枠(全幅の ─)の両方を含む。
(setv FRAME-BORDER-CHARS "─━═│┃╭╮╰╯┌┐└┘├┤┬┴┼")
;; 罫線と判定する連続長。実測 321 frame(2026-08-11 断面)で、shell echo
;; だけの画面と空画面には 8 連の罫線は 1 件も現れなかった(偽陽性 0)。
(setv FRAME-BORDER-RUN 8)

;; 選択式 dialog の幾何(R9 fast-path の外側も拾う): prompt glyph の直後に
;; 番号つき option。codex の gate 形式(ready_physics CODEX-READY-PATTERN)が
;; `(?!\d+\.[ \t])` で ready から排除しているのと同じ物理を、こちらは
;; 「dialog 形である」の positive 判定として使う。
(setv DIALOG-OPTION-RE (re.compile r"(?m)^[ \t]*[❯›][ \t ]+\d+\.[ \t]"))
;; 確認 dialog の文言(kind 横断・小文字比較)。R9 の 5 種はいずれかを含む —
;; 未知 dialog も同じ確認語彙を使う実測に基づく。
(setv DIALOG-CONFIRM-PHRASES
      #("enter to confirm" "press enter to continue" "enter y/n:"))


;; ---------------------------------------------------------------------------
;; 基本 marker(oracle output_has_* — 窓幅も凍結物理)
;; ---------------------------------------------------------------------------

(deff has-failure-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "hard failure marker(tail 10 行窓、oracle output_has_failure_marker)。"
  (setv text (tail-lower output 10))
  (bool (or (in "fatal error" text)
            (in "unrecoverable error" text)
            (in "agent crashed" text)
            (in "session terminated" text)
            (in "authentication failed" text))))

(deff has-api-limit-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "provider rate-limit / quota marker(tail 30 行窓、oracle
   output_has_api_limit_marker の 9 パターン + issue #557 の追補 +
   ACP ADR 0049 R9 改訂の所有格族照合)。"
  (setv text (tail-lower output 30))
  (bool (or (in "cost limit reached" text)
            (in "rate limit exceeded" text)
            (in "rate limit reached" text)
            (in "quota exceeded" text)
            (in "insufficient quota" text)
            (in "resource exhausted" text)
            (in "/rate-limit-options" text)
            (in "stop and wait for limit to reset" text)
            ;; issue #557 二次補強: 現行 claude TUI の exhausted 側文言
            ;; (2026-07-20 実測、旧 9 パターンのどれにも一致しない)。
            ;; approaching 側(`used NN% of your … limit · resets`)は対象外 —
            ;; まだ動ける pane を blocked_api にしない。
            (in "usage limit reached" text)
            (in "limit reached · resets" text)
            (in "limit reached ∙ resets" text)
            ;; ACP ADR 0049 R9 改訂(2026-08-07): 所有格族(you've
            ;; hit/reached your … limit)は逐語でなく族照合 — 旧逐語 4 語
            ;; (you've hit your limit / you've reached your usage limit /
            ;; you've hit your usage limit / you've hit your monthly spend
            ;; limit)を包含し、provider 可変語の未知形(Fable 5 等)にも
            ;; 有界内で追従する。
            (is-not (.search API-LIMIT-EXHAUSTED-FAMILY-RE text) None)
            ;; credits 枯渇(2026-07-26 実 incident)は所有格族の外の実物
            ;; 文言 — exhausted 側 = blocked_api が正。
            (in "out of usage credits" text))))


;; ---------------------------------------------------------------------------
;; provider 由来の非上限終端(ACP ADR 0049 R9 第 3 改訂 — 2026-08-12)
;; ---------------------------------------------------------------------------
;; R9 は「行動系の終端でも pane の provider marker を先に見る」を定めたが、
;; 見る対象が provider-limit 族ただ 1 つに固定されていた。上限以外の provider
;; 失敗 —— 再認証要求・組織 access 剥奪・文脈枯渇・transport 障害 —— で turn が
;; 終わった attempt には marker が立たず、run_failed / retryable=false へ落ちる。
;; 席の判断ではない失敗が「席が結果を報告しなかった」として恒久 gate になる形で、
;; 上限族に対する #557 / R9 の修理とまったく同じ穴が族の数だけ開いていた。
;;
;; 実測(2026-08-12・agentd.sqlite の terminal × 席 transcript の突合):
;; 所有格族が着地した 2026-08-08 以降の turn-end-without-result 終端 19 件のうち
;; 12 件(63%)が provider 由来 —— 認証 11(再認証要求 4 / 組織 access 剥奪 7)・
;; 文脈枯渇 1。同区間の上限族の取りこぼしは 0 件で、残っているのはこの族である。
;;
;; 設計の規律は上限族と同じ(R9 (i)(ii)): 逐語列挙ではなく有界可変挿入つきの
;; 族照合。逐語追随が 3 度破れた事実(2026-07-20 / 07-26 / 08-06)が根拠で、
;; ここでも実物 frame は同一事象に 2 形あった ——
;; 「Not logged in · Run /login」(status 行の右詰め)と
;; 「⎿ Not logged in · Please run /login」(transcript 行)。挿入は同一物理行の
;; 内側に限る([^\n]{0,40})—— 文境界を越える無限定照合は禁止。
;;
;; ★この族は status を動かさない。has-api-limit-marker は blocked_api という
;; 非終端 status を作る(枠が戻れば動く)が、こちらの族は「この attempt はもう
;; 動けない」であって待てば解けるものではない。ゆえに
;; observed-status-from-markers の凍結分類順には入れず、終端 cause の蒸留
;; (policy の provider-failure 表)にだけ効かせる —— 生きた pane を新しい
;; 非終端 status へ落とす経路を構造的に作らない。

;; 再認証要求の族。実物 4 形(2026-07-24〜2026-08-12 の pane capture):
;;   "Not logged in · Run /login" / "Not logged in · Please run /login" /
;;   "Login expired · Please run /login" / "Please run /login · API Error: 403 …"
;; 状態語(not logged in / login expired)→ 有界挿入 → /login の形と、
;; remediation 単独形(please run /login / run /login)の 2 択で覆う。
(setv PROVIDER-REAUTH-FAMILY-RE
  (re.compile
    (+ "(?:not logged in|login expired|session expired)[^\n]{0,40}/login"
       "|(?:please )?run /login")))

;; 組織側で access を剥がされた族(2026-08-09〜10 実 incident、cryptic-x が
;; 24h で 7 席を恒久停止させた形)。実物:
;;   "Your organization has disabled Claude subscription access for Claude Code
;;    · Use an Anthropic API key or contact your admin to enable access"
;; 「organization has disabled … access」の間だけを有界可変にする。
(setv PROVIDER-ACCESS-REVOKED-FAMILY-RE
  (re.compile "organization has disabled[^\n]{0,40}\\baccess\\b"))

;; 文脈枯渇の族。実物 pane: "Context limit reached · /compact or /clear to
;; continue"、transcript 側の同事象: "Prompt is too long · the request is
;; ~1201884 tokens (limit 1000000)"。状態語のみで可変挿入は観測されていない。
(setv PROVIDER-CONTEXT-EXHAUSTED-FAMILY-RE
  (re.compile "context limit reached|prompt is too long"))

;; transport 障害の族(provider 側 5xx / 応答中断)。実物:
;;   "API Error: 500 Internal server error." / "API Error: 529 Overloaded." /
;;   "API Error: Server error mid-response." / "API Error: Connection lost
;;    mid-response." / "API Error: Connection closed mid-response."
;; 4xx は入れない —— 恒久的な拒否(認証・権限)であって再試行の対象ではなく、
;; 認証形は上の 2 族が先に拾う。
(setv PROVIDER-TRANSPORT-FAILURE-FAMILY-RE
  (re.compile
    (+ "api error:[^\n]{0,60}"
       "(?:\\b5\\d\\d\\b|mid-response|connection (?:lost|closed)"
       "|socket connection was closed|overloaded|internal server error)")))

;; 蒸留の族名(policy の provider-failure 表の join key)。値は wire にも行にも
;; 載る安定 slug —— 新族の追加は「ここに 1 行 + policy の表に 1 行」で閉じる。
(setv PROVIDER-FAILURE-CLASS-REAUTH "reauth-required")
(setv PROVIDER-FAILURE-CLASS-ACCESS-REVOKED "access-revoked")
(setv PROVIDER-FAILURE-CLASS-CONTEXT-EXHAUSTED "context-exhausted")
(setv PROVIDER-FAILURE-CLASS-TRANSPORT "transport-failure")


(deff provider-failure-class [output]
  {:pre [(: output str)] :post [(: % (| str None))]}
  "provider 由来の非上限終端事実(tail 30 行窓 — has-api-limit-marker と同窓)。
   立っていなければ None。上限族はここに含めない —— あちらは非終端 status
   (blocked_api)を作る別の家で、両方立った観測の優先は policy が決める
   (凍結順: api-limit → 本族)。族の順序は『確定した状態』を先に、最も一般の
   transport を最後に置く。"
  (setv text (tail-lower output 30))
  (cond
    (is-not (.search PROVIDER-REAUTH-FAMILY-RE text) None)
      PROVIDER-FAILURE-CLASS-REAUTH
    (is-not (.search PROVIDER-ACCESS-REVOKED-FAMILY-RE text) None)
      PROVIDER-FAILURE-CLASS-ACCESS-REVOKED
    (is-not (.search PROVIDER-CONTEXT-EXHAUSTED-FAMILY-RE text) None)
      PROVIDER-FAILURE-CLASS-CONTEXT-EXHAUSTED
    (is-not (.search PROVIDER-TRANSPORT-FAILURE-FAMILY-RE text) None)
      PROVIDER-FAILURE-CLASS-TRANSPORT
    True None))


(deff has-waiting-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "interactive 待ち marker(raw 一致、oracle output_has_waiting_marker)。"
  (bool (or (in "tell Claude what to do differently" output)
            (in "Type your message" output)
            (in "accept edits" output)
            (in "bypass permissions" output)
            (in "shift+tab to cycle" output)
            (in "Esc to cancel" output)
            (in "to show all projects" output))))

(deff has-idle-prompt [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "REPL idle prompt(codex `› ` / claude 行頭 `❯` 単独 — claude は U+00A0
   区切りなので 2 文字一致 `❯ ` は使わない。oracle
   output_has_agent_idle_prompt)。"
  (bool (or (.startswith output "› ")
            (in "\n› " output)
            (any (gfor line (.splitlines output) (.startswith line "❯"))))))

(deff is-starting-mcp-servers [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "MCP boot 中(oracle output_is_starting_mcp_servers)— boot は work ではない
   (16h-stuck 実障害: boot spinner を active に数えると launch watchdog が死ぬ)。"
  (in "starting mcp servers" (tail-lower output 30)))

(deff has-codex-active-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "codex の active-work marker(oracle output_has_codex_active_marker)。
   `ctrl + t to view transcript` は故意に見ない(collapsed 履歴に残留する)。"
  (if (is-starting-mcp-servers output)
      False
      (do
        (setv text (tail-lower output 30))
        (bool (or (in "working (" text)
                  (in "esc to interrupt" text))))))

(deff is-claude-composer-border [line]
  {:pre [(: line str)] :post [(: % bool)]}
  "claude composer の罫線行(issue #573): 現行 TUI は入力欄を全幅の水平罫線で
   囲む — strip 後が罫線文字だけの行。spinner 探索では空行と同様に skip する
   (skip しないと探索が罫線で止まり、稼働中 pane の active-marker が常に
   false になる — 2026-07-29 実 incident の一次原因)。"
  (setv trimmed (.strip line))
  (bool (and trimmed (all (gfor ch trimmed (in ch "─━═"))))))

(deff has-live-claude-spinner [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の live spinner(oracle output_has_live_claude_spinner_marker +
   issue #573 の罫線跨ぎ): 最終 `❯` 行から上へ、空行と composer 罫線行を
   skip した最初の本文行に `… (`。`❯` が無ければ tail 30 の `… (`。
   本文行は 1 行しか見ない — 履歴に残留した過去 spinner 行は live ではない。"
  (setv lines (.splitlines output))
  (setv prompt-index None)
  (for [[index line] (enumerate lines)]
    (when (.startswith line "❯")
      (setv prompt-index index)))
  (if (is None prompt-index)
      (in "… (" (tail-lower output 30))
      (do
        (setv found False)
        (for [line (reversed (cut lines 0 prompt-index))]
          (setv trimmed (.strip line))
          (when (and trimmed (not (is-claude-composer-border line)))
            (setv found (in "… (" trimmed))
            (break)))
        found)))

(deff has-active-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "kind 横断の active-work marker(oracle output_has_agent_active_marker)。"
  (bool (or (has-codex-active-marker output)
            (has-live-claude-spinner output))))

(deff has-turn-activity [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の turn activity(⏺ / ⎿)。active marker ではない — idle 画面にも
   残留する。latch clear と startup watchdog 解除にのみ使う(oracle
   output_has_claude_turn_activity)。"
  (bool (or (in "⏺" output) (in "⎿" output))))

(deff composer-region [output]
  {:pre [(: output str)] :post [(: % (| str None))]}
  "composer 領域(末尾 20 行のうち最終 prompt 行 ❯ / › とそれ以降)。
   prompt 行が無ければ None(= composer は描かれていない)。
   ADR-DOE-AGENTS-010 R1 の走査規律の単一実装 — has-unsubmitted-paste /
   composer-prompt-text / is-composer-empty はここから領域を得る(領域抽出の
   コピーが増えると『最終 prompt 行より上は対象外』が実装ごとに腐る)。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) COMPOSER-TAIL-LINES)) None))
  (setv last-prompt-index None)
  (for [[index line] (enumerate recent)]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-index index)))
  (if (is None last-prompt-index)
      None
      (.join "\n" (cut recent last-prompt-index None))))

(deff has-unsubmitted-paste [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "未 submit の paste / 添付残留(oracle output_has_unsubmitted_paste_input の
   sent-text=None 面 — monitor 経路はこの面だけを使う。issue #568 /
   ADR-DOE-AGENTS-010 R1 で composer 領域へ拡張): 末尾 20 行の composer 領域
   (最終 prompt 行 ❯ / › とそれ以降の行)に collapsed paste marker・添付
   チップ([Image #N])・queued ヒントが居る。添付チップは prompt 行の外
   (直下の行・行頭空白)に描かれる — prompt 行 1 行だけの走査は空 prompt +
   チップ形(2026-07-28 実 wedge)に盲目だった。最終 prompt 行より上
   (送信済み履歴)は対象外のまま。"
  (setv composer (composer-region output))
  (if (is None composer)
      False
      (bool (any (gfor marker UNSUBMITTED-CHIP-MARKERS (in marker composer))))))

(deff is-composer-clear [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "composer に未送信の内容が座っていない(ADR-DOE-AGENTS-011
   R-paste-ready-a71c): composer 領域が描かれており、未送信チップ
   (UNSUBMITTED-CHIP-MARKERS)が無い。ready gate の必要条件 — 内容が
   座ったままの composer へ prompt を重ね貼りすると Enter がどちらを submit
   するかは未定義で、実測(2026-08-11 の 25 席)では chip が積み上がったまま
   turn が始まらない wedge に落ちた。
   ★『prompt 行に文字が無い』は条件に入れない(実物 frame で棄却): codex の
   ready composer は prompt 行に回転するプレースホルダ(`› Improve
   documentation in @filename` 等)を描く — 空文字を要求すると codex の
   launch が構造的に全滅する。未送信内容の語彙は ADR-DOE-AGENTS-010 R1 の
   チップ集合のみが安定物理。"
  (and (is-not (composer-region output) None)
       (not (has-unsubmitted-paste output))))

(deff is-input-loop-wired [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "入力 loop が配線済み(ADR-DOE-AGENTS-011 R-paste-ready-a71c): idle prompt が
   可視 ∧ MCP boot 中でない。gate 形式(ready_physics CODEX-READY-PATTERN)は
   `\\A(?!.*starting mcp servers)` でこの窓を最初から除いていたが、observation
   形式は idle prompt 単独で ready を主張していた — 『composer は Starting MCP
   servers (N/M) が画面に残る間から描かれるが input loop は未配線で、その窓に
   keys を送ると Enter がロード画面に食われ prompt が入力箱に座ったまま submit
   されない』という同じ物理(verbatim capture tests/data/ready_screens/
   codex_mcp_boot.txt)が観測形式では効いていなかった。両形式はこの述語で
   同じ排除を持つ(ADR-DOE-AGENTS-008 R1 の 2 形式一致)。"
  (and (has-idle-prompt output)
       (not (is-starting-mcp-servers output))))

(deff has-queued-messages [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の queued-messages marker(issue #573): mid-turn に配送された
   message は composer に積まれ、入力行が `❯ Press up to edit queued messages`
   になる。未消費 queue = turn 走行中の明白な busy 証拠 — 中断キー送出の
   安全壁が参照する。判定は has-unsubmitted-paste と同じ最終 prompt 行規律
   (履歴に写った同文言を事実にしない)。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) 20)) None))
  (setv last-prompt-line None)
  (for [line recent]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-line trimmed)))
  (if (is None last-prompt-line)
      False
      (in "Press up to edit queued messages" last-prompt-line)))


;; ---------------------------------------------------------------------------
;; R9 dialog(検出 + 決定的 dismissal キー列 — S18 で verbatim 確定した物理)
;; ---------------------------------------------------------------------------

(deff has-codex-update-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "codex の Update available! dialog(oracle output_has_codex_update_dialog:
   headline でなく menu options で判定 — headline は capture 窓から溢れる)。"
  (setv lower (tail-lower output 10))
  (bool (and (in "update now" lower)
             (in "skip until next version" lower)
             (in "press enter to continue" lower))))

(deff codex-update-selected-option [output]
  {:pre [(: output str)] :post [(: % int)]}
  "update dialog の現在選択(`›` の直後の数字、末尾 10 行、無ければ 1 —
   oracle codex_update_dialog_selected_option + 既定)。"
  (setv selected 1)
  (setv lines (list (reversed (.splitlines output))))
  (for [line (cut lines 0 10)]
    (when (in "›" line)
      (setv after (.strip (get (.split line "›" 1) 1)))
      (when after
        (setv head (get after 0))
        (when (in head "123")
          (setv selected (int head))
          (break)))))
  selected)

(deff codex-update-dismiss-keys [output]
  {:pre [(: output str)] :post [(: % tuple)]}
  "Skip until next version(option 3)への決定的キー列(oracle
   codex_update_dialog_down_steps_to_skip_until_next: (3+3-sel)%3 回の Down
   + Enter)。"
  (setv steps (% (- 6 (codex-update-selected-option output)) 3))
  (tuple (+ (* ["Down"] steps) ["Enter"])))

(deff has-claude-bypass-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の bypass-permissions 確認 dialog(選択 marker が `❯` なので idle
   判定より先に見ること — oracle output_has_claude_bypass_permissions_dialog)。"
  (setv lower (.lower output))
  (bool (and (in "bypass permissions mode" lower)
             (in "no, exit" lower)
             (in "yes, i accept" lower)
             (in "enter to confirm" lower))))

(deff has-claude-fullscreen-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の fullscreen renderer opt-in dialog(oracle
   output_has_claude_fullscreen_renderer_dialog)。"
  (setv lower (.lower output))
  (bool (and (in "try the new fullscreen renderer?" lower)
             (in "yes, try it" lower)
             (in "not now" lower)
             (in "enter to confirm" lower))))

(deff has-claude-trust-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の workspace-trust gate(未 trust の cwd で startup を塞ぐ —
   dismiss しないと wait-for-repl-idle は永久に idle を見ず 120s 上限縮退 →
   prompt が dialog に送出されて死ぬ)。Rust oracle に対応関数は無い:
   2026-07-07 に実物 frame(R9 カバレッジ欠落の実障害)から追加した
   5 つ目の R9 dialog。長文の質問文(Is this a project you created …)は
   pane 幅で reflow するため marker にしない — 折返しは部分文字列一致を
   殺す(幾何学物理)。"
  (setv lower (.lower output))
  (bool (and (in "yes, i trust this folder" lower)
             (in "no, exit" lower)
             (in "enter to confirm" lower))))

(deff has-claude-managed-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "組織 managed-settings 承認 dialog(mid-turn にも出る — monitor loop でも
   発火する唯一の R9 dialog。oracle
   output_has_claude_managed_settings_approval_dialog)。"
  (setv lower (.lower output))
  (bool (and (in "managed settings require approval" lower)
             (in "settings requiring approval" lower))))

(deff detect-dialog [output]
  {:pre [(: output str)] :post [(: % tuple)]}
  "R9 dialog の検出と決定的 dismissal キー列。検査順は oracle
   wait_for_repl_idle と同じ(trust は oracle 非在 — fullscreen の後に挿入):
   codex-update → bypass → fullscreen → trust → managed。
   dismissal(S18 verbatim): update = selected 依存 Down×n + Enter /
   bypass = Down,Enter(既定 No,exit → Yes,I accept)/
   fullscreen = Down,Enter(既定 Yes,try it → Not now)/
   trust = Enter(既定 Yes,I trust this folder — pre-seed
   hasTrustDialogAccepted と同じ意図で doeff 制御の work_dir を信頼)/
   managed = Enter。戻り値: #(dialog-name keys) — 検出なしは #(None #())。"
  (cond
    (has-codex-update-dialog output)
      #("codex-update" (codex-update-dismiss-keys output))
    (has-claude-bypass-dialog output)
      #("bypass" #("Down" "Enter"))
    (has-claude-fullscreen-dialog output)
      #("fullscreen" #("Down" "Enter"))
    (has-claude-trust-dialog output)
      #("trust" #("Enter"))
    (has-claude-managed-dialog output)
      #("managed" #("Enter"))
    True #(None #())))

;; ---------------------------------------------------------------------------
;; 起動画面の分類材料(ADR-DOE-AGENTS-011 — oracle 非在の追加物理)
;; ---------------------------------------------------------------------------

(deff has-frame-border [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "TUI 自身が描いた枠線(罫線文字が FRAME-BORDER-RUN 連続)。
   oracle 非在: 2026-08-11 の実物 frame 321 件から起こした物理。
   目的は『agent が 1 frame も描いていない』の弁別 — 起動 command の echo と
   shell prompt だけの画面(実測 294/321)には罫線が構造的に現れない。"
  (setv run 0)
  (setv found False)
  (for [ch output]
    (if (in ch FRAME-BORDER-CHARS)
        (do
          (setv run (+ run 1))
          (when (>= run FRAME-BORDER-RUN)
            (setv found True)
            (break)))
        (setv run 0)))
  found)

(deff is-dialog-shaped [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "選択式 dialog の幾何(ADR-DOE-AGENTS-011 R-unknown-dialog-loud-4e02):
   番号つき option 行、または確認文言。detect-dialog が None を返した上で
   これが True の画面は『R9 fast-path 集合の外の dialog』— 決定的な
   dismissal キーを持たないので dismiss を推測せず loud に落とす。"
  (setv lower (.lower output))
  (bool (or (is-not (.search DIALOG-OPTION-RE output) None)
            (any (gfor phrase DIALOG-CONFIRM-PHRASES (in phrase lower))))))

(deff has-agent-frame [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "agent 自身が frame を描いたか(ADR-DOE-AGENTS-011 R-frame-class-6f3d)。
   positive 証拠の論理和 — 入力欄 / active / turn / MCP boot 行 / R9 dialog /
   interactive 待ち marker / 罫線。旧実装は『idle prompt が見えない』だけを
   知っていたため、描画ゼロと認識不能画面を同一文言で終端していた。"
  (bool (or (has-idle-prompt output)
            (has-active-marker output)
            (has-turn-activity output)
            (is-starting-mcp-servers output)
            (has-waiting-marker output)
            (is-not (get (detect-dialog output) 0) None)
            ;; dialog の幾何そのものも agent の描画物(codex の login 画面は
            ;; 罫線も既知 marker も持たず ASCII menu だけを描く — verbatim
            ;; capture tests/data/ready_screens/codex_login.txt)。
            (is-dialog-shaped output)
            (has-frame-border output))))

(deff startup-finished [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "launch watchdog の解除信号(oracle output_indicates_startup_finished):
   REPL input box / active / turn-activity のどれかが見え、かつ MCP boot・
   update・bypass・fullscreen・trust dialog 中でない(それらは watchdog が
   刈り続けるべき stuck-in-startup 状態そのもの)。"
  (if (or (is-starting-mcp-servers output)
          (has-codex-update-dialog output)
          (has-claude-bypass-dialog output)
          (has-claude-fullscreen-dialog output)
          (has-claude-trust-dialog output))
      False
      (bool (or (has-codex-active-marker output)
                (has-idle-prompt output)
                (has-turn-activity output)))))


;; ---------------------------------------------------------------------------
;; PaneObservation の組み立て(事実の束 — 分類は policy 所有)
;; ---------------------------------------------------------------------------

(deff classify-output [output]
  {:pre [(: output str)] :post [(: % PaneObservation)]}
  "pane capture → PaneObservation(kind 横断の oracle 検出関数の束)。"
  (setv [dialog dismiss-keys] (detect-dialog output))
  (PaneObservation
    :has-failure-marker (has-failure-marker output)
    :has-api-limit-marker (has-api-limit-marker output)
    ;; ACP ADR 0049 R9 第 3 改訂: 上限以外の provider 終端事実。status には
    ;; 効かせず、終端 cause の蒸留にだけ使う(provider-failure-class 参照)。
    :provider-failure-class (provider-failure-class output)
    :has-waiting-marker (has-waiting-marker output)
    :has-idle-prompt (has-idle-prompt output)
    :has-active-marker (has-active-marker output)
    :has-turn-activity (has-turn-activity output)
    :startup-finished (startup-finished output)
    :has-unsubmitted-paste (has-unsubmitted-paste output)
    :has-queued-messages (has-queued-messages output)
    :dialog dialog
    :dialog-dismiss-keys dismiss-keys
    ;; ADR-DOE-AGENTS-011: 起動段の分類材料も『事実』として同じ束で運ぶ —
    ;; 分類の順序と意味づけは policy 所有(launch-not-ready-class)。
    :has-agent-frame (has-agent-frame output)
    :composer-clear (is-composer-clear output)
    :composer-text (composer-region output)
    :input-loop-wired (is-input-loop-wired output)
    :dialog-shaped (is-dialog-shaped output)))
