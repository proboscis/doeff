;;; 直接束縛 deftest: herdr substrate handler(第二 substrate トライアル)。
;;;
;;; sessionhost_substrate_deftests.hy の鏡映。純関数(キー写像・チャンク分割)は
;;; 決定的に、herdr effect は実 herdr server での smoke で検証する
;;; (herdr 不在・server 停止時は skip)。物理の出典は Phase 0 実測
;;; (conformance/herdr-physics.md)。

(require doeff-hy.macros [deftest defk deff <-])

(import json)
(import os)
(import shutil)
(import socket :as socket-mod)
(import tempfile)
(import time)
(import doeff [EffectBase])

(import doeff_agents.sessionhost.effects [
  tmux-new-session
  tmux-has-session
  tmux-pane-current-command
  tmux-session-pane-ids
  tmux-capture
  tmux-send-keys
  tmux-kill-session])
(import doeff_agents.sessionhost.substrate_herdr [
  DEFAULT-HERDR-SOCKET
  REQUEST-LINE-BYTE-LIMIT
  HerdrApiError
  herdr-substrate
  herdr-call
  herdr-key-name
  herdr-request-line
  chunked-send-texts
  normalize-ansi-read])


(deff herdr-server-available? []
  {:pre [True]
   :post [(: % bool)]}
  "実 herdr server が socket で応答するか(deftest の skip 判定)。"
  (when (not (os.path.exists DEFAULT-HERDR-SOCKET))
    (return False))
  (setv sock (socket-mod.socket socket-mod.AF-UNIX socket-mod.SOCK-STREAM))
  (try
    (.settimeout sock 2.0)
    (.connect sock DEFAULT-HERDR-SOCKET)
    (.sendall sock b"{\"id\":\"probe\",\"method\":\"ping\",\"params\":{}}\n")
    (setv data (.recv sock 65536))
    (in b"pong" data)
    (except [Exception]
      False)
    (finally
      (.close sock))))

(setv HERDR-AVAILABLE (herdr-server-available?))


(deff close-workspaces-with-label [label]
  {:pre [(: label str)]
   :post [(: % "None")]}
  "テスト teardown 専用の帯域外掃除: label が一致する workspace を全部閉じる。
   kill-session 経路が assert 対象そのものである(壊れた実装だと失敗する)
   テストでも、live herdr server に workspace を残さないための test-owned
   経路。best-effort — 掃除の失敗でテスト本体の失敗理由を上書きしない。"
  (try
    (setv listing (herdr-call DEFAULT-HERDR-SOCKET "workspace.list" {}))
    (except [Exception]
      (return None)))  ; server 不達なら掃除対象も無い
  (for [ws (get listing "workspaces")]
    (when (= (.get ws "label") label)
      (try
        (herdr-call DEFAULT-HERDR-SOCKET "workspace.close"
                    {"workspace_id" (get ws "workspace_id")})
        (except [Exception]
          None))))  ; best-effort teardown — 本体の失敗理由を優先
  None)


(deff simulate-agent-name-plate-loss [pane-id]
  {:pre [(: pane-id str)]
   :post [(: % "None")]}
  "実 agent 起動時に herdr の実 agent 検出が起こす名札上書きを、検出と同じ
   API 列(pane.report_agent で authority を取り agent.rename で名札を付替)
   で模擬する。実測 2026-08-01(probe n=3 決定的): pane 内で実 agent を
   起動すると 2 秒以内にこの状態遷移が起き、agent.get {target: session 名}
   は agent_not_found になる。API 列の同型は 2026-08-09 probe で確認
   (/tmp/probe-rename-607f0c.log — herdr-physics.md 追補に記録)。
   rename 先は herdr の agent 名制約(小文字開始・[a-z0-9_-]・32 文字以内、
   実測 2026-08-09 invalid_agent_name)に収める。"
  (herdr-call DEFAULT-HERDR-SOCKET "pane.report_agent"
              {"pane_id" pane-id
               "source" "doeff-deftest-sim"
               "agent" "claude"
               "state" "working"})
  (herdr-call DEFAULT-HERDR-SOCKET "agent.rename"
              {"target" pane-id
               "name" f"det-claude-{(os.getpid)}"})
  None)


;; ---------------------------------------------------------------------------
;; 純関数(実測物理の写像)
;; ---------------------------------------------------------------------------

(deftest test-herdr-key-name-mapping
  ;; 実測: BSpace は backspace のみ受理、Home/End は非対応 → ctrl+a/ctrl+e。
  (assert (= (herdr-key-name "BSpace") "backspace"))
  (assert (= (herdr-key-name "Home") "ctrl+a"))
  (assert (= (herdr-key-name "End") "ctrl+e"))
  ;; whitelist の残り(policy.hy ALLOWED-UNBLOCK-KEY-NAMES)は素通し —
  ;; herdr が大文字小文字不問で受理することを実測済み。
  (for [key ["Up" "Down" "Left" "Right" "Enter" "Escape" "Tab" "Space" "y" "1"]]
    (assert (= (herdr-key-name key) key))))


(deftest test-chunked-send-texts-small-is-single-chunk
  (setv text "hello herdr")
  (assert (= (chunked-send-texts text) [text])))


(deftest test-chunked-send-texts-large-respects-request-line-limit
  ;; 5MB(U1 実測の byte-exact 上限確認と同サイズ)を分割し、
  ;; (1) 復元が byte-exact (2) 各チャンクの request line が上限以下、を確認。
  (setv text (* "x" (* 5 1024 1024)))
  (setv chunks (chunked-send-texts text))
  (assert (> (len chunks) 1))
  (assert (= (.join "" chunks) text))
  (for [chunk chunks]
    (assert (<= (len (herdr-request-line
                       "pane.send_text"
                       {"pane_id" "w000:p000" "text" chunk}))
                REQUEST-LINE-BYTE-LIMIT))))


(deftest test-chunked-send-texts-escape-expansion-counted
  ;; チャンク判定は JSON escape 後の実バイト長 — 改行だらけのテキスト
  ;; (\n → \\n で 2 倍膨張)でも request line が上限を超えないこと。
  ;; 生バイト基準の分割はこのケースで上限を突き破る(回帰 pin)。
  (setv text (* "y\n" (* 1 1024 1024)))  ; 生 2MiB、escape 後 ~3MiB
  (setv chunks (chunked-send-texts text))
  (assert (= (.join "" chunks) text))
  (for [chunk chunks]
    (assert (<= (len (herdr-request-line
                       "pane.send_text"
                       {"pane_id" "w000:p000" "text" chunk}))
                REQUEST-LINE-BYTE-LIMIT))))


(deftest test-normalize-ansi-read-preserves-trailing-space
  ;; pane.read format=ansi の実測形(2026-07-07): SGR 込み grid 再構成、
  ;; 行区切り \r\n、trailing space は SGR の内側(`\x1b[1m› \x1b[0m`)。
  ;; strip 後に "\n› "(trailing space 込み)が残ることが codex idle prompt
  ;; 検出(impls/markers.hy has-idle-prompt)の前提 — S18 ハングの回帰 pin。
  (setv raw "\x1b[0m\x1b[38;5;1mRED\x1b[0m plain\r\n\r\n\x1b[0m\x1b[1m› \x1b[0m")
  (assert (= (normalize-ansi-read raw) "RED plain\n\n› "))
  ;; OSC(BEL / ST 終端)と 2 文字 ESC も剥がす(grid 再構成には現れない
  ;; はずだが防御的に被覆)。
  (assert (= (normalize-ansi-read "\x1b]0;title\x07text\x1b]2;t\x1b\\tail\x1b7")
             "texttail"))
  ;; plain テキストは素通し(冪等)。
  (assert (= (normalize-ansi-read "no escapes here ") "no escapes here ")))


;; ---------------------------------------------------------------------------
;; herdr smoke(実 herdr server — 不在時 skip)
;; ---------------------------------------------------------------------------

(deftest test-herdr-lifecycle-smoke
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-smoke-{(os.getpid)}")
  (try
    ;; new-session → has-session(True)→ capture → current-command →
    ;; kill → has-session(False)— tmux 版 deftest と同じ寿命 parity。
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {"DOEFF_PROBE" "deftest"})))
    (assert (in ":p" pane))
    (<- alive ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-has-session session-name)))
    (assert alive)
    (<- captured ((herdr-substrate DEFAULT-HERDR-SOCKET)
                  (tmux-capture pane 10)))
    (assert (isinstance captured str))
    ;; 素の shell pane の foreground は idle shell(zombie 判定の観測面)。
    ;; ただし作成直後は shell rc の子(mkdir / scutil 等)が一時的に
    ;; foreground に立つ(2026-07-07 実測 flake)ので eventual assert —
    ;; monitor loop も周期観測なので一時 foreground を誤判定しない。
    (setv shells ["zsh" "bash" "sh" "dash" "fish" "ksh"])
    (setv deadline (+ (time.monotonic) 5.0))
    (setv cmd None)
    (setv probing True)
    (while probing
      (<- probed ((herdr-substrate DEFAULT-HERDR-SOCKET)
                  (tmux-pane-current-command pane)))
      (setv cmd probed)
      (if (or (in cmd shells) (> (time.monotonic) deadline))
          (setv probing False)
          (time.sleep 0.1)))
    (assert (in cmd shells))
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-kill-session session-name)))
    (<- gone ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-has-session session-name)))
    (assert (not gone))
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-new-session-rejects-forbidden-env
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; 禁止 env reject は substrate 所有 — herdr 束縛でも同じ guard が効く
  ;; (substrate.hy の ensure-no-forbidden-agent-env を共有)。
  (setv raised None)
  (try
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-new-session "doeff-herdr-forbidden" "/tmp"
                             {"ANTHROPIC_API_KEY" "leak"})))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  (assert (in "Anthropic API keys" (str raised))))


(deftest test-herdr-duplicate-session-rejected
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; tmux new-session の duplicate 拒否 parity。herdr は workspace label の
  ;; 重複をネイティブに拒否しない(実測 2026-08-09: 同 label の
  ;; workspace.create は 2 つ目も成功する)ため、重複判定は doeff 側
  ;; (substrate_herdr の create-then-verify)が所有する。旧実装が依存した
  ;; agent.rename の agent_name_taken は重複検出として成立しない — 既存
  ;; session の名札は実 agent 起動で herdr 検出に上書きされ(実測
  ;; 2026-08-01 n=3)、名札消失後の同名 create が素通りする。
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-dup-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    ;; --- (1) 名札が健在な普通の重複: 拒否 + 敗者 workspace の掃除。
    (setv raised None)
    (try
      (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
             (tmux-new-session session-name d {})))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None))
    (assert (in "duplicate session" (str raised)))
    ;; --- (2) 名札消失後の重複(実 agent 起動後に相当する最悪ケース):
    ;;     agent 名簿にはもう session 名が無い — label アンカーの判定だけが
    ;;     拒否できる(旧実装はここで素通りして二重 session を作った)。
    (simulate-agent-name-plate-loss pane)
    (setv raised2 None)
    (try
      (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
             (tmux-new-session session-name d {})))
      (except [e RuntimeError] (setv raised2 e)))
    (assert (is-not raised2 None))
    (assert (in "duplicate session" (str raised2)))
    ;; --- 敗者は自分の workspace を掃除してから raise する(リーク禁止):
    ;;     label を持つ workspace は勝者の 1 つだけ残る。
    (setv listing (herdr-call DEFAULT-HERDR-SOCKET "workspace.list" {}))
    (setv holders (lfor ws (get listing "workspaces")
                        :if (= (.get ws "label") session-name)
                        (get ws "workspace_id")))
    (assert (= (len holders) 1)
            f"duplicate loser must clean its workspace, label holders: {holders}")
    ;; --- 既存 session は重複拒否の巻き添えにならず生きている。
    (<- alive ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-has-session session-name)))
    (assert alive)
    (finally
      (close-workspaces-with-label session-name)
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-new-session-owns-sole-pane-workspace
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; 幾何学 parity pin(実測 2026-07-07): herdr agent.start の既定配置は
  ;; 「現在 workspace の active tab への split」で、pane 幅が既存 pane 数に
  ;; 反比例して劣化する。幅 ~10 桁の pane では claude bypass-permissions
  ;; dialog が単語の途中で折返され("Bypass\n  Permi\n  ssions\n  mode")、
  ;; markers.hy の部分文字列一致 oracle(has-claude-bypass-dialog 等)が
  ;; 構造的に全滅 → wait-for-repl-idle が 120s 上限まで縮退し session が
  ;; 死ぬ(実 claude E2E で実測)。tmux new-session は常に独立 window
  ;; (フル幅 grid)なので、herdr 束縛は「専用 workspace の唯一 pane」で
  ;; 同じ幾何学を保証する義務を負う。
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-geom-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    (setv ws-id (get (.split pane ":") 0))
    (setv listing (herdr-call DEFAULT-HERDR-SOCKET "pane.list"
                              {"workspace_id" ws-id}))
    (setv panes (get listing "panes"))
    (assert (= (len panes) 1)
            f"session pane must be the sole pane of its workspace (full width), got {(len panes)}: {panes}")
    (assert (= (get (get panes 0) "pane_id") pane))
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-capture-preserves-trailing-space
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; S18-herdr ハングの根本原因 pin: herdr pane.read format=text は行末
  ;; スペースをトリムする(実測 2026-07-07)が、tmux capture-pane -J は保持
  ;; する(man tmux)。codex idle prompt marker は trailing space 込みの
  ;; "\n› " 部分文字列一致(impls/markers.hy has-idle-prompt)なので、
  ;; トリムされると wait-for-repl-idle が構造的に idle を検出できず
  ;; launch が 120s 上限まで poll し続ける。substrate は format=ansi 読み +
  ;; SGR strip で tmux parity(trailing space 保持)を復元する義務を負う。
  ;; printf 後に sleep で前景プロセスを保持するのは、shell prompt が同じ
  ;; 行に続いてスペースが「行中」になり誤 green になるのを防ぐため
  ;; (codex idle frame と同じ「前景が trailing-space 行を描いて待つ」物理)。
  (import time)
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-tspace-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    (time.sleep 1.0)
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-send-keys
             pane
             "printf '\\033[31mRED\\033[0m\\nPROMPTMARK '; sleep 30"
             True True)))
    (setv captured "")
    (setv found False)
    (for [_ (range 10)]
      (time.sleep 0.5)
      (<- got ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-capture pane 50)))
      (setv captured got)
      (when (in "\nPROMPTMARK " captured)
        (setv found True)
        (break)))
    (assert found
            f"trailing space not preserved in capture tail: {(repr (cut captured -80 None))}")
    ;; ansi 読みの strip 義務: 生 CSI/SGR が capture に漏れないこと。
    (assert (not-in "\x1b[" captured))
    (assert (in "RED" captured))
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-workspace-order-key-shortlex
  ;; 同時作成の合意に使う全順序の pin(shortlex: 桁数優先、同桁は ASCII)。
  ;; ⚠ 創出順の主張ではない — workspace_id は創出順に単調でない(実測
  ;; 2026-08-14: w3NZ → w3N0 / w3MZ → w3M0。→ deftest
  ;; test-herdr-new-session-verdict-ignores-id-order)。ここで固定するのは
  ;; 「競合者どうしが同じ勝者を選べる決定的な全順序であること」だけ。素の
  ;; 文字列比較は桁境界で順序が変わる("w1VS" < "w3R")ため桁数優先にする。
  (import doeff_agents.sessionhost.substrate_herdr [herdr-workspace-order-key])
  (assert (< (herdr-workspace-order-key "w3R") (herdr-workspace-order-key "w1VS")))
  (assert (< (herdr-workspace-order-key "w1VS") (herdr-workspace-order-key "w1VT")))
  (assert (< (herdr-workspace-order-key "w1VT") (herdr-workspace-order-key "w1VV")))
  (assert (= (herdr-workspace-order-key "w1VS") (herdr-workspace-order-key "w1VS"))))


(deftest test-herdr-new-session-verdict-ignores-id-order
  ;; 回帰 pin(実測 2026-08-14、稼働 herdr 0.7.5 / protocol 17): workspace_id は
  ;; 創出順に単調ではない。断続的に赤だった全量走行の RPC 記録で、w3NZ の次に
  ;; 作られた workspace が w3N0 を返し(= 後発のほうが shortlex で先)、独立の
  ;; 連続 create 実測でも w3MZ の次が w3M0 だった。したがって
  ;; 「id 最小 = 先行 session」は成立せず、既存 session の検出を id の順序に
  ;; 委ねると重複 session が素通りする(旧実装の実害 = 全量走行で
  ;; test-herdr-duplicate-session-rejected が段 2 で断続的に赤。単独走行では
  ;; 反転を踏まないため緑で、順序前提の破れが見えなかった)。
  ;;
  ;; 分担の pin: 既存 session の検出は **事前照会**(作成前の名簿)が担い、
  ;; 事後照合は「事前照会と作成の間に割り込んだ同時作成」どうしが同じ勝者に
  ;; 合意するためだけに使う。事後照合の順序鍵は合意のための全順序であって、
  ;; 創出順の主張ではない。
  (import doeff_agents.sessionhost.substrate_herdr [herdr-new-session-verdict])
  ;; ① 既存 session あり + 自分の id のほうが小さい(実測の反転形)= 重複
  (assert (= (herdr-new-session-verdict "w3M0" ["w3MZ"] ["w3M0" "w3MZ"]) "duplicate"))
  ;; ② 既存 session あり + 自分の id のほうが大きい = 重複(順序前提でも成立した形)
  (assert (= (herdr-new-session-verdict "w3N8" ["w3N7"] ["w3N7" "w3N8"]) "duplicate"))
  ;; ③ 事前は不在(= 同時作成の競合)。決定的な全順序でちょうど 1 人が勝つ。
  (assert (= (herdr-new-session-verdict "w3N7" [] ["w3N7" "w3N8"]) "ok"))
  (assert (= (herdr-new-session-verdict "w3N8" [] ["w3N7" "w3N8"]) "duplicate"))
  ;; ④ 事前も事後も自分だけ = 成立
  (assert (= (herdr-new-session-verdict "w3N7" [] ["w3N7"]) "ok"))
  ;; ⑤ 作成直後に自分が名簿から消えた = 重複ではない別の失敗として名乗る
  ;;    (旧実装は holders 空で (get holders 0) が IndexError になり、
  ;;     呼び手の RuntimeError 捕捉を素通りしていた。)
  (assert (= (herdr-new-session-verdict "w3N7" [] []) "vanished")))


(deftest test-herdr-identity-survives-agent-name-loss
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; 本 issue(substrate-herdr-session-identity-anchor-r2-607f0c)の回帰ガード:
  ;; 実測 2026-08-01(probe n=3 決定的)で、pane 内で実 agent を起動すると
  ;; 2 秒以内に herdr の実 agent 検出が agent 名札を上書きし、
  ;; agent.get {target: session 名} が解決不能になる。旧実装(PR #569 まで)は
  ;; session 同一性を agent 名で解決していたため、実 agent 起動後に生死確認・
  ;; 帰属観測・kill が全滅した — 当時のテストは agent 起動前までしか見ておらず
  ;; この破れを検出しなかった(見落としの回帰 pin)。ここでは検出と同じ API 列で
  ;; 名札消失を模擬し、模擬後も全経路(has-session / session-pane-ids /
  ;; capture / send / kill)が workspace label アンカーで成立することを直接示す。
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-anchor-{(os.getpid)}")
  (setv marker f"ANCHOR-DEFTEST-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    ;; shell 起動を待ってから名札を消す(fresh pane は prompt 描画前がある)。
    (time.sleep 1.0)
    (simulate-agent-name-plate-loss pane)
    ;; --- 模擬の実効の直接確認: agent 名での解決はもう成立しない。
    ;;     (これが成立しないなら模擬が壊れており、以降の assert は無意味。)
    (setv plate-lost False)
    (try
      (herdr-call DEFAULT-HERDR-SOCKET "agent.get" {"target" session-name})
      (except [e HerdrApiError]
        (setv plate-lost (= e.code "agent_not_found"))))
    (assert plate-lost
            "simulation must remove the agent name plate (agent_not_found)")
    ;; --- 生死確認(has-session 相当)。
    (<- alive ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-has-session session-name)))
    (assert alive "has-session must survive agent name-plate loss")
    ;; --- 帰属観測(ADR-DOE-AGENTS-010 R4 の宛先 pane 解決)。
    (<- pane-ids ((herdr-substrate DEFAULT-HERDR-SOCKET)
                  (tmux-session-pane-ids session-name)))
    (assert (= pane-ids [pane])
            f"session-pane-ids must resolve the pane after name loss: {pane-ids}")
    ;; --- send + capture(pane 宛て経路も名札消失の影響を受けないこと)。
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-send-keys pane f"echo {marker}" True True)))
    (setv found False)
    (for [_ (range 10)]
      (time.sleep 0.5)
      (<- captured ((herdr-substrate DEFAULT-HERDR-SOCKET)
                    (tmux-capture pane 50)))
      (when (in marker captured)
        (setv found True)
        (break)))
    (assert found "send/capture roundtrip must work after name loss")
    ;; --- kill(名前 → workspace 解決)と消滅確認。
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-kill-session session-name)))
    (<- gone ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-has-session session-name)))
    (assert (not gone) "kill-session must terminate the session after name loss")
    (finally
      (close-workspaces-with-label session-name)
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-send-and-capture-roundtrip
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; literal paste + submit → capture に出力が現れる(Enter submit の実効と
  ;; capture の scrollback-empty fallback(visible)の両方を通る)。
  (import time)
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-echo-{(os.getpid)}")
  (setv marker f"HERDR-DEFTEST-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    ;; shell 起動を待ってから送る(fresh pane は prompt 描画前がある)。
    (time.sleep 1.0)
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-send-keys pane f"echo {marker}" True True)))
    (setv found False)
    (for [_ (range 10)]
      (time.sleep 0.5)
      (<- captured ((herdr-substrate DEFAULT-HERDR-SOCKET)
                    (tmux-capture pane 50)))
      (when (in marker captured)
        (setv found True)
        (break)))
    (assert found "echo marker did not appear in herdr capture")
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))
