;;; Executable ADR for the in-process (caller-VM) agent result transport.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-005
  :title "in-process result transport: report_result データチャネルのみ — 端末スクレイプ結果経路の全廃"
  :status "accepted"
  :scope ["doeff-agents" "doeff-agents.handlers.production" "doeff-agents.handlers.effectful"
          "doeff-agents.result-contract" "doeff-agents.inprocess-face"]
  :problem
    [(fact
       "2026-07-10、nakagawa SBI recon readiness で agent は正しい 585 文字の 1 行 JSON blocker をマーカー(DOEFF_AGENT_RESULT_BEGIN/END)付きで 16:35:45 に出力済みだったが、TUI が 80 桁 pane で折り返した結果を verbatim パースする AwaitResult は 'Invalid control character' で確定失敗し、121 回の no-result poll の後 readiness が FAIL した。"
       :evidence "zeus claude-home PVC projects/-tmp-readiness-sbi-recon-ui-mismatch-c92a96e2…/b86fb07b….jsonl; nak-pytest-agent-bfba24c7ab pod log; 折り返し再現スクリプト(同 payload を width 78 で wrap → json.loads 失敗)")
     (fact
       "ADR 0035 R5(e5f2ef5c)は折り返し修復ヒューリスティックを『非可逆で一クラスの値を必ず壊すクローン』として削除し report_result を単一輸送路所有者と宣言したが、同コミットは『doeff-agentic が in-process tmux handler にまだ依存しているため raw pane 抽出は残す』と明記して非準拠面を出荷し続けた。この免除を検出する機械検査(deftest / semgrep / conformance 行)はゼロだった。"
       :evidence "git show e5f2ef5c (commit message); packages/doeff-agents/conformance/README.md(in-process 行なし)")
     (fact
       "利用者(proboscis-ema)は pin bump(edfac2a1, 6e5d2128→8526c105)で修復削除を無自覚に受け取り、レビューでは何も落ちず、実行時にのみ失敗が現れた。呼び出し側の失敗メッセージは outcome.validation-error を含んでおらず、パース失敗という根因は pytest 出力から不可視だった。"
       :evidence "proboscis-ema edfac2a1; nakagawa sbi_recon.hy _recon-retry-message(修正前)")
     (fact
       "同日、Claude Code 2.1.206 が screen-reader バナー文言を '[Accessible screen reader mode: on]' から '[Screen Reader Mode: on via flag]' へ変更し、旧文言を要求する trust ダイアログ自動応答が沈黙、agent が 'Enter y/n:' で永久停止した。端末描画テキストへのキーイングはインスタンスではなくクラスとしてバージョン脆弱である。"
       :evidence "git show c1219b0e; tmux pane capture 2026-07-10 17:2x")
     (fact
       "端末グリッド投影は非単射である: soft-wrap は空白を注入・喪失し、word-boundary wrap の修復は実在した空白を必ず失う。いかなる修復ヒューリスティックも全 payload に対して正しくなりえない。"
       :evidence "git show 5d404aee (agentd byte-faithful data channel, ADR 0035 R1)")]
  :context
    [(interpretation
       "ADR 0035 の法は doeff-agents が出荷するすべてのハンドラ面に及ぶ: 結果は型付きデータチャネルに乗り、端末は観測(status・onboarding・watchdog)専用である。in-process 面は MCP ツールを呼び出し側 VM 内で実行する唯一の経路であり session-host が caller tools をホストするまで退役できない — だからこそ同じチャネルをこの面にも与える。免除して残すのではなく。")
     (interpretation
       "スクレイプを『フォールバック』として残すのは無フォールバックより悪い: 短い payload では成功し長い payload で失敗するため、輸送の正しさが payload 長に依存する非決定的な系になる。法(ADR)・機構(コード)・利用者(pin 消費者)は one set で移行する。")]
  :decision
    [(rule R1 "in-process defhandler(agent-handler-defhandler / tmux-agent-defhandler)経由で launch される result_schema 付き L2 セッションは、必ず in-VM MCP サーバを持つ: domain tools + report_result ツール。spec.mcp_tools が空でも report_result のみのサーバを立てる。sink は launch 前に登録し、結果契約プロンプトは report_result ツールのみを指示する。")
     (rule R2 "in-process AwaitResult が結果を解決する唯一の源は report された sink である(result-first、session 生死に関わらず勝つ)。端末バイト列を結果 payload としてパースすることを禁止する。pane capture は観測(status・ダイアログ・watchdog)専用として残る。")
     (rule R3 "マーカー語彙(DOEFF_AGENT_RESULT_BEGIN/END)とその抽出・修復ヘルパは doeff-agents から削除する。マーカー印字指示・マーカーパースの再導入は本 ADR 違反(semgrep 検出)。")
     (rule R4 "報告なしで terminal を観測したセッションは型付き no-result outcome(EXITED, result=None, validation_error は report_result を名指し, continuable=False)を返す。生存中で未報告なら heartbeat/awaiting-input 観測を返し続ける — solicitation 方針は呼び出し側の所有(ADR-DOE-AGENTS-002 R1 の in-process 対応物)。")
     (rule R5 "TmuxAgentHandler.handle_launch_session は sink 未登録の schema spec に対して fail-fast する(型付き AgentLaunchError)。in-process 面の L1 agent() タスク経路は同期 await が server loop を饑餓させるためデータチャネルをホストできない — schema セッションは L2 LaunchSession 効果経路を使うこと。Mock/Scenario/Daemon ハンドラは各自の輸送を所有し R5 の対象外。")
     (rule R6 "移行: doeff-agents を pin する消費者(doeff-agentic, proboscis-ema)は schema セッションを L2 経路へ移す。マーカー依存のテスト・プロンプト主張は本 ADR による意図的な破壊的変更である。")]
  :laws
    [(law results-never-ride-rendered-terminal
       :statement "result_payload => typed_data_channel(report_result) never terminal_scrape"
       :counterexamples
         [(counterexample "pane capture / pipe-pane transcript から JSON を json.loads する結果経路を追加する")
          (counterexample "『短い結果は折り返さないから安全』としてマーカー経路を限定復活させる")
          (counterexample "折り返し修復ヒューリスティックを再導入する(ADR 0035 R5 の巻き戻し)")])
     (law schema-session-always-has-channel
       :statement "result_schema_set and inprocess_launch => in_vm_mcp_server_with_report_result registered_before_launch"
       :counterexamples
         [(counterexample "mcp_tools が空なのでサーバを立てず、プロンプトだけ report_result を指示する(ツール不在で agent が迷子)")
          (counterexample "sink 未登録のまま launch し、await が budget 満了まで no-result を空回りする")])
     (law capture-is-observation-only
       :statement "capture_pane_or_transcript => status_dialog_watchdog_observation only"
       :counterexamples
         [(counterexample "monitor の last_output に結果マーカーを探す fast-path を足す")])
     (law tui-text-keys-on-stable-wording
       :statement "tui_text_detection => keyed_on_dialog_own_wording never version_dependent_banners"
       :counterexamples
         [(counterexample "trust ダイアログ検知を '[Accessible screen reader mode: on]' バナー文言に依存させる(2.1.206 で沈黙した実績)")])]
  :enforcement
    [(defsemgrep no-terminal-result-marker-vocabulary
       :languages ["generic"]
       :pattern "DOEFF_AGENT_RESULT_BEGIN"
       :message "結果マーカー語彙は ADR-DOE-AGENTS-005 R3 で削除済み。結果は in-VM report_result データチャネル(make_report_result_tool + sink)で運ぶ。端末スクレイプ結果経路の再導入は本 ADR の supersede が先。"
       :bad ["RESULT_BLOCK_BEGIN = \"DOEFF_AGENT_RESULT_BEGIN\""]
       :good ["sink = handler.create_result_sink(session_id)  # ADR-DOE-AGENTS-005 R1"])]
  :plans ["docs/adr/defadr_doeff_agents_005_inprocess_result_transport.hy"
          "packages/doeff-agents/src/doeff_agents/handlers/production.py (marker 語彙・抽出削除, sink-first await, launch fail-fast)"
          "packages/doeff-agents/src/doeff_agents/handlers/effectful.hy (schema セッションは常に in-VM server + report_result)"
          "packages/doeff-agents/tests/test_inprocess_report_result.py (R1-R5 の機械検査)"])
