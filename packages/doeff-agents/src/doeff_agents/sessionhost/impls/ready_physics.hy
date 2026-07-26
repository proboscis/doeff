;;; readiness gate 物理の家(ADR-DOE-AGENTS-008 R1)— doeff-free leaf。
;;;
;;; ここは gate 形式(単発判定: pattern 文字列と純テキスト述語)の唯一の
;;; 定義箇所。observation 形式(has-idle-prompt / dialog 検出)は同 package の
;;; markers.hy が家で、codex の両形式の一致は
;;; tests/test_ready_physics_single_home.py の parity 検定が執行する。
;;;
;;; adapters / session.py(命令型 transport)はここから import する。この
;;; モジュールは doeff も doeff-hy も import しない(hy 言語ランタイムのみ):
;;; package root は『命令型 session transport API は doeff VM を import せず
;;; に動く』を明文で保っており、effects/policy を引き込む markers.hy を
;;; adapters から直接 import するとその性質が壊れるため、gate 形式だけを
;;; この leaf に分離している。
;;;
;;; 出自: 退役 Rust 実装(packages/doeff-agentd — rollback 専用保存であり
;;; 正しさの基準ではない: ADR-DOE-AGENTS-004 R7/U1)からの移植 + verbatim
;;; capture(tests/data/ready_screens/、codex 0.144.4 / Claude Code 2.1.209)。


;; claude(screen-reader mode)の readiness = permission-mode footer の可視。
;; 命令型 stack の adapter は常に --ax-screen-reader + 非既定 permission mode
;; で起動するため、ready REPL は入力 prompt 直上に
;; "<mode> on (shift+tab to cycle)" を描く(2.1.209 verbatim capture)。
;; 古典的な U+276F composer marker は screen-reader mode では決して描画されず、
;; dialog 画面(trust y/n / bypass accept / theme)はこの footer を含まない。
;; screen-reader banner を鍵にしないこと: 文言が version 依存
;; (has-claude-screen-reader-trust-prompt の註を参照)。
;; 通常 TUI mode(sessionhost impls/claude_code.hy の起動系)の idle 物理は
;; markers.hy has-idle-prompt(行頭 `❯`)— mode が違えば事実も違う。両 mode
;; の事実はこの家と markers.hy に 1 定義ずつ並ぶ(ADR-008 context 3)。
(setv CLAUDE-SCREEN-READER-READY-PATTERN "shift\\+tab to cycle")

;; codex の readiness = idle composer の可視(U+203A prompt marker 行 + 数字
;; menu でない本文)かつ MCP-boot status 行の不在。verbatim capture
;; (tests/data/ready_screens/)と退役 Rust 移植物理から:
;; - login 画面(CODEX_HOME に auth 無し)は menu を ASCII ">" で描き
;;   U+203A を使わない — timeout すべきで match してはならない;
;; - trust / update dialog は "<U+203A> <digit>. <option>" の選択 marker を
;;   描く — (?!\d+\.[ \t]) 先読みで排除(update dialog の既定 option に
;;   Enter すると global npm upgrade が走る);
;; - composer は "Starting MCP servers (N/M)" が画面に残る間から描かれるが
;;   input loop は未配線 — その窓に keys を送ると Enter がロード画面に
;;   食われ、prompt が入力箱に座ったまま submit されない。\A(?!...) が
;;   この窓を排除する(MCP startup が落ち着くと codex はこの status 行を
;;   置き換える)。
(setv CODEX-READY-PATTERN
      "(?ims)\\A(?!.*starting mcp servers).*^\\u203a[ \\t]+(?!\\d+\\.[ \\t])\\S")


(defn has-claude-screen-reader-trust-prompt [output]
  "claude(screen-reader mode)の workspace-trust prompt の可視。
   screen-reader banner を鍵にしないこと: 文言は Claude Code の version 依存
   (~2.1.204 は「[Accessible screen reader mode: on]」、2.1.206 からは
   「[Screen Reader Mode: on via flag]」)で、腐った banner 一致が trust
   prompt を未 dismiss のまま残し、agent が「Enter y/n:」で呼び手の await
   予算切れまでハングした(2026-07-10)。下の textual y/n prompt 行は trust
   dialog の screen-reader 描画にのみ現れる version-stable な信号。"
  (and (in "Quick safety check: Is this a project you created or one you trust?"
           output)
       (in "Please answer y or n." output)
       (in "Enter y/n:" output)))
