# herdr substrate physics (第二 substrate トライアル実測記録)

観測対象: herdr 0.7.1 / protocol 14 / socket `~/.config/herdr/herdr.sock`。
実測日: 2026-07-06〜07-07。実測手段: socket 直叩き probe + conformance suite
(`DOEFF_SESSIONHOST_BACKEND=herdr`)。

**位置づけ**: `conformance/README.md` は tmux-oracle の凍結契約であり編集しない。
herdr backend で観測した物理・tmux との差分・fixture の backend 対応はすべて
このファイルに記録する。実装は
`src/doeff_agents/sessionhost/substrate_herdr.hy`(Tmux* 6 effect の束縛)。

## transport(newline-JSON over unix socket)

- 1 RPC = 1 接続。request は 1 行 JSON、response も 1 行 JSON 封筒
  (`{"result": ...}` / `{"error": {"code", "message"}}`)。
- **request line 全体に ~1MiB 上限**。実測境界: 1,048,336B OK / 1,049,344B 拒否
  (server 側 "api request line is too large" + BrokenPipe)。
- 大容量 paste は分割送信で byte-exact(5MB を 900KiB チャンクで確認)。
  チャンク判定は**生バイト長ではなく JSON エスケープ後の request line 実バイト長**
  (`\n` → `\\n` の 2 倍膨張で生バイト基準は破綻 — deftest に回帰 pin)。

## agent / pane モデル

- **agent == pane の 2 層構造**(tmux は session > window > pane の 3 層)。
  `agent.start` が pane を作り、`pane.close` すると **agent エントリも消える**
  (実測: close 後の `agent.get` → `agent_not_found`)。
  「session 生存 + 監視 pane 消失」という tmux の分離状態は第一級では存在しない
  → S19c の fault injection は合成が必要(下記)。
- `agent.get` のパラメータは **`{"target": name}`**(`{"name": ...}` は
  `invalid_request: missing field 'target'`)。不在は `agent_not_found`。
- `agent.start` は**名前の一意性をネイティブ強制**: 重複名 →
  `agent_name_taken` = tmux duplicate session 拒否と同 parity。追加ガード不要。
- `pane.report_agent {pane_id, source, agent, state}` は `agent.start` と
  **同じ名前空間**に入る(実測 2026-07-07): 同名 2 エントリで `agent.get` は
  `agent_target_ambiguous`、片方の pane close 後は残る 1 つに解決される。
- `agent.start` の `focus: false` を渡しても応答は `"focused": true` を返す
  ことがある(実害なし、外観のみ)。
- zombie 検知は `pane.process_info` → `foreground_processes[0].argv0` を使う。
  `name` は claude で version 文字列(`2.1.201`)になる実測があるため使わない。
- herdr の agent 状態は **5 値**(idle/working/blocked/**done**/unknown、
  `blocked_api` 区別なし)。Phase 0 では 4 値と記録していたが、
  `herdr agent wait --status` の語彙と実イベントで done を確認
  (実 claude にプロンプト送信 → 0.116s で `pane.agent_status_changed →
  working`、4.6s 後 `→ done`)。ただし外部報告 API
  `pane.report_agent --state` は idle|working|blocked|unknown の 4 値のみ
  (done は herdr 自身の検知専用語彙)。状態分類の委譲は all-or-nothing、
  Phase 2 判断(トライアル対象外)。
- **herdr の状態分類は launch dialog を誤る**: 実物の claude trust dialog を
  表示中の pane を `agent_status=idle` と分類した(本来 blocked であるべき
  画面)。宣言的検知マニフェスト(`~/.local/state/herdr/agent-detection/`)の
  `live_prompt_box` ルールが dialog の選択 UI に誤発火する — 分類権威を
  herdr に委譲できるかの Phase 2 判断で考慮すべき限界データ。

## 幾何学物理(pane 幅 — TmuxNewSession parity、解決済み 2026-07-07)

**pane 幅は marker 物理の第一級変数である。** tmux `new-session` は常に独立
window(フル幅 grid、detached 既定 80×24)を作るが、herdr `agent.start` の
既定配置は「**現在 workspace の active tab への split**」で、pane 幅が既存
pane 数に反比例して劣化する:

- 実測の破壊シナリオ(実 claude E2E、bypass dialog 強制状態): 既存 4 pane の
  tab に split された幅 ~10 桁の pane で bypass-permissions dialog が単語の
  途中で折返され(`"Bypass\n  Permi\n  ssions\n  mode"`)、markers.hy の
  部分文字列一致 oracle(`has-claude-bypass-dialog` の
  `"bypass permissions mode"` 等)が**構造的に全滅** → detect-dialog=None →
  wait-for-repl-idle が 120s 上限まで縮退 → prompt が dialog に paste され
  session 死亡(launch 124.7s、result=null)。
- 修正(substrate 所有の合成): `workspace.create {label: session-name,
  focus: false}` → `agent.start {workspace_id: <新WS>}`(active tab へ split)
  → root shell pane を `pane.close`(残った agent pane が**全幅に展開** —
  実測 101→208 桁)。同一 herdr server・同一 E2E が launch 5.3s / 9.8s PASS。
- cleanup 追加不要: **workspace は最後の pane close で自動消滅**(実測)。
  既存 kill-session(agent pane close)がそのまま workspace ごと片付ける。
- agent.start 失敗時(agent_name_taken 等)は作った workspace を
  `workspace.close` してから元エラーを再送出(リーク防止、dup 拒否 parity
  維持 — deftest green)。
- **socket param quirk**: agent.start の配置指定は `workspace_id`。
  `workspace` キーは**エラーにならず黙って無視され**現在 workspace に配置
  される(実測) — 誤 param が観測不能な配置バグになる。
- 回帰 pin: deftest `test-herdr-new-session-owns-sole-pane-workspace`
  (session pane はその workspace の唯一 pane であること)。

## capture 物理(TmuxCapture parity — 最重要)

tmux oracle は `capture-pane -p -J -S -N`: wrap join に加えて
**行末スペースを保持**する(man tmux の -J 記述)。herdr の対応物理:

- `pane.read` の本文は `result.read.text`。source 名は underscore
  (`recent_unwrapped` — hyphen は socket で拒否。CLI はハイフン)。
- `recent` / `recent_unwrapped` = スクロールバック + 現在画面の tail-N。
  **スクロールバックが空の間は空文字を返す**(0.7.1 quirk)。一度でも
  スクロールすると「全履歴 + 現在画面」の tail-N になり、まだ流れていない
  最新行も即時反映される。→ 空なら `visible` へ fallback。
- `visible` は末尾の空白行をトリムする(tmux は画面高までの空行を含む)。
- **`format: "text"`(既定)は行末スペースをトリムする**(実測 2026-07-07、
  全 source 共通)。→ **S18-herdr launch ハングの根本原因**(下記)。
- **`format: "ansi"` は trailing space を保持する**。grid 再構成の SGR 込み
  (`\x1b[1m› \x1b[0m` — スペースは SGR の内側)、行区切りは `\r\n`。
  wrap join(論理行復元)は ansi 形式でも有効(133 桁論理行で実測)。
  空 quirk も text と同一(無出力 pane で空文字)。OSC は grid に乗らないため
  ansi 出力に現れない(防御的に strip 対象には含める)。
- → substrate は **ansi 読み + エスケープ strip + `\r\n`→`\n` 正規化**で
  tmux parity を復元する(`normalize-ansi-read`)。

## キー物理(TmuxSendKeys parity)

- `pane.send_keys` の受理キー名(総当たり実測): `Enter/enter, Up/down/...` 等
  大文字小文字不問で OK。`backspace` のみ OK(`BSpace/bspace` は
  `unsupported key`)。**`Home`/`End` 非対応**。`ctrl+a / ctrl+e / ctrl+u / f1`
  OK。`C-a / pageup / pagedown / delete / insert` REJECT。
  → 写像: `BSpace`→`backspace`、`Home`→`ctrl+a`、`End`→`ctrl+e`(意図保存)、
  他は素通し。
- キーの生バイト(keydump 実測): down=`\x1b[B`、up=`\x1b[A`(tmux send-keys と
  同一)、**enter=`\n`(LF。tmux は `\r` CR)** — canonical tty では ICRNL で
  等価、S1/S18 で submit 実効確認済み。raw-mode TUI で差が出る可能性には注意。
- `pane.send_text` + `Enter` で shell へのコマンド submit 実効確認済み。
- 非 login shell(`argv=[$SHELL]`)で起動する(tmux は login shell)。zsh の
  `.zshenv`/`.zshrc` は非 login でも読まれるため ZDOTDIR shim PATH は効く
  (`.zprofile` のみ読まれない)。S18 M1 shim 方式で問題なしを実測。

## S18-herdr launch ハングの根本原因(解決済み 2026-07-07)

**症状**: S18 `test_s18_codex_update_dialog_dismissed_at_launch`(herdr)が
`launch_m1` の RPC read で 60s pytest timeout。

**journal 実測タイムライン**(ハング時):
dialog frame 描画 → dismiss キー(Down×2+Enter)が +0.87s で届き matched=true
→ scroll 70 → idle frame `F-idle-codex`(= `"\n› "`)描画済み → **その後 30s
以上 wait-for-repl-idle が idle を認識せず**(キー送出も一切なし)→ fake agent
の prompt await が 30s timeout で park → pytest 60s timeout。

**機序**: codex idle prompt の検出(`impls/markers.hy has-idle-prompt`)は
trailing space 込みの部分文字列一致 `"\n› "`。herdr `format:"text"` は行末
スペースをトリムするため capture には `"\n›"` しか現れず、**構造的に永遠に
マッチしない** → wait-for-repl-idle(上限 120s)が poll し続け、pytest 60s が
先に切れる。tmux が green なのは `capture-pane -J` が trailing space を保持
するから。dialog 検知が動いたのは、dialog 行(`› 1. Update now` 等)では
`›` の後にテキストが続き行末トリムの影響を受けないため。

**修正**(substrate 層、markers の凍結物理は無変更):
`herdr-capture-io` を `format:"ansi"` 読みに変更し `normalize-ansi-read`
(CSI/OSC/ESC strip + `\r\n`→`\n`)で plain 化。trailing space は SGR の内側に
座るため strip 後も保持される。回帰 pin:
- `test-herdr-capture-preserves-trailing-space`(smoke — 修正前 red を確認)
- `test-normalize-ansi-read-preserves-trailing-space`(純関数)

## S19c fault injection の backend 差(解決済み 2026-07-07)

S19c(stale-observation reaper)の帯域外操作は「session 生存判定は true のまま
監視 pane の capture だけ失敗させる」。tmux は `new-window` + `kill-pane` で
作れるが、herdr は agent==pane のため素の `pane.close` では liveness
(`agent.get`)ごと落ち、stale reaper ではなく lost 経路に入ってしまう。

**合成手順**(`harness.break_pane_observation_out_of_band`):
1. `pane.split`(監視 pane の隣に sibling を作る)
2. `pane.report_agent`(session 名を sibling に再報告 — agent.start と同一
   名前空間)
3. `pane.close`(監視 pane を閉じる)
→ `agent.get` は sibling に解決(生存 true)、記録済み pane_id の `pane.read`
は `pane_not_found`(capture fail)→ stale reaper が正しく発火。

## S1 位相ロック(fixture 修正、契約弱化ではない — 2026-07-06)

S1 golden path が herdr でのみ `result_solicitations_used == 1`(pin は 0)。
原因は conformance_agent の report_result リトライが 0.25s グリッドで、
harness の 100ms 時間圧縮 monitor tick と干渉する位相アーティファクト
(monitor interval 300ms / 1000ms(本番既定)では herdr でも solicitation 0 の
完全 green を実測)。herdr は tmux より ~200ms 速く launch が完了するため
位相がずれ、report 着地が turn-end tick に 20ms 負けていた。

**修正**: conformance_agent.py の retry sleep 0.25s→0.05s、attempts 40→200
(総予算 10s 維持 — row 登録まで ~4.3s かかるため予算を縮めると両 backend
とも fail する。実測済みの落とし穴)。修正後 S1 は tmux 8.5s / herdr 8.3s で
両 green。

## 実 agent E2E(fake-agent conformance の外側の実物理、2026-07-07)

fake agent では検証できない実 TUI 物理(実 ANSI 出力の capture 経路、raw-mode
での enter=LF submit、wait-for-repl-idle の実 idle prompt 検出、実 MCP result
channel 配線)を、本物の claude / codex を doeff-sessionhost 経由で走らせて検証:

| Agent | Backend | Result | 所要 | 備考 |
|---|---|---|---|---|
| claude (sonnet) | herdr | **PASS** | 9.5s | report_result payload byte-faithful |
| claude (sonnet) | tmux | **PASS** | 9.1s | ベースライン、herdr と同等速度 |
| codex | herdr | **PASS** | 143.9s | **実 codex の `› `+trailing-space idle prompt を ansi capture 経路で検出** = S18 修正の実物実証 |
| codex | tmux | **PASS** | 153.2s | ベースライン、canonical work_dir |
| claude (sonnet) | herdr | **PASS** | 9.8s | **bypass dialog 強制状態**(抑制フラグ除去)。実物 dialog を R9 fast-path が dismiss。幾何学修正後の substrate |
| codex | herdr | **PASS** | 101.1s | 幾何学修正後の再検証(専用 workspace、全幅 pane)。model 無指定(下記ドライバ注意) |

ドライバ: 旧 Rust-agentd 向け `agentd_real_agent_result_retry_e2e_support.py` の
prompt / session_env / schema を再利用し、daemon を worktree の
`doeff-sessionhost --backend <mux>` に向けた(明示 flag、env knob 不使用)。

ドライバ注意(実測 2026-07-07): codex に model を渡してはいけない。
`build-codex-argv` は model があると `--model` をそのまま載せ、ChatGPT
アカウントの codex は `The 'sonnet' model is not supported...` の 400 を
プロンプト処理時に返して **idle prompt のまま座り込む**(session は
RUNNING のまま → await が 240s で timeout。zombie 判定にはならない —
codex プロセスは生きているため)。model 無指定なら `--model` が付かず正常。

### 実 E2E で発見した backend 非依存の doeff 側問題(herdr 起因ではない)

**codex trust pre-seed の canonicalize 不一致**(tmux / herdr 両方で同一に再現):
- 現行 codex CLI は trust 判定に **canonical cwd**(/tmp → /private/tmp 解決後)
  を使う。一方 sessionhost の codex trust 物理は「work_dir を canonicalize
  しない」を旧 CLI 準拠の oracle として凍結(impls/codex.hy、claude の S12 が
  FsCanonicalPath を使うのと対照的)。
- symlink を含む work_dir(例 `/tmp/...`)を渡すと trust エントリが
  `/tmp/...` で書かれ、codex は `/private/tmp/...` と比較 → 不一致 →
  **trust dialog が出る**(pre-seed 無効化)。
- さらに破壊的縮退が連鎖する: trust dialog は R9 dialog リスト
  (codex-update / bypass / fullscreen / managed)に無く、その選択 marker
  `› 1. Yes, continue` が has-idle-prompt の `"\n› "` に**idle と誤認**される
  → wait-for-repl-idle が prompt を paste → プロンプト文中の数字
  (実測では "E2E" の `2`)が dialog の `2. No, quit` を選択 → codex 終了 →
  zombie 判定で `lost` exited。
- 呼び手側の回避: work_dir は canonical パスで渡す(pytest tmp_path は
  canonical なので旧 E2E は踏まなかった)。
- 根本修正(orch issue 行き): codex-pre-launch の trust 書き込みを
  FsCanonicalPath 経由に揃える + codex trust dialog の R9 追加可否の設計判断。

### 実 launch 障害物 dialog の実物検証(2026-07-07 追補)

ユーザー要求「trust prompt / update prompt 等の実 launch 障害物込みで
doeff 経由テスト」への実測回答。R9 の 4 dialog のうち実物で踏めるのは
bypass のみで、それは green:

- **claude bypass-permissions dialog(R9)— 実物 green**。
  - 抑制フラグの正体: `$CLAUDE_CONFIG_DIR/settings.json` の
    `"skipDangerousModePermissionPrompt": true`(`.claude.json` の
    `bypassPermissionsModeAccepted` では**ない** — 受諾時に claude が
    書き戻すのも settings.json の同キー)。通常 E2E プロファイルには
    これが立っており dialog は出ない(9.5s PASS はこの経路)。
  - フラグを一時除去して強制発生させた実測: dialog 出現(帯域外 herdr
    read で逐語 frame 採取)→ R9 fast-path が Down+Enter で dismiss
    (画面滞在 ~0.6s)→ launch 5.3s、E2E 全体 9.8s PASS。
  - 実物 frame の markers 分類: `detect_dialog=('bypass',('Down','Enter'))`
    / `has_idle_prompt=False`(選択行 `❯ 1. No, exit` は行頭スペース付き
    なので idle 誤認なし)/ `has_waiting_marker=True`。
  - 実物 frame 要点(全幅 208 桁 pane): "WARNING: Claude Code running in
    Bypass Permissions mode ... ❯ 1. No, exit / 2. Yes, I accept /
    Enter to confirm · Esc to cancel"。
- **codex-update dialog(R9)**: 実物は決定的に強制できない(codex 側に
  pending update が必要)→ S18 の逐語 frame 再生が恒久のテスト戦略。
- **claude trust dialog — R9 カバレッジ欠落を発見し、5 つ目の R9 dialog
  として修正済み(2026-07-07)**。
  - 欠落の実証: 実物 capture(cwd=$HOME 未 trust のプロファイルで表示)を
    markers oracle に通すと `detect_dialog=None` / `has_idle_prompt=False`
    (選択行 ` ❯ 1. Yes, I trust this folder` は行頭スペース付き)/
    `has_waiting_marker=True` → 未 dismiss のまま launch は
    wait-for-repl-idle の 120s 上限まで待って縮退し、prompt が dialog に
    送出されて hang/死亡。pre-seed(`hasTrustDialogAccepted`)は通常経路の
    抑止であって防御ではない — CLI はこの種の startup prompt を予測不能に
    出す(それをテストが塞ぐのが本suite の目的)。
  - 修正(markers.hy — 凍結共有物理への追加、両 backend に効く):
    `has-claude-trust-dialog` = lower-case AND
    (`yes, i trust this folder` + `no, exit` + `enter to confirm`)。
    長文の質問文は pane 幅で reflow するため marker にしない(幾何学物理)。
    `detect-dialog` の cond へ fullscreen の後に挿入、`startup-finished`
    の除外リストにも追加(stuck-in-startup)。dismiss = **Enter 単発**
    (既定選択が option 1 = trust 側。bypass の Down,Enter と違う)。
    doeff 制御の work_dir を信頼する = pre-seed と同一ポリシー。
    Rust oracle(main.rs)に対応関数は無い — sessionhost 発の追加。
  - TDD: deftest `test-classify-claude-trust-dialog`(実物 frame 逐語)+
    S18 `test_s18_claude_trust_dialog_accepted_at_launch`(fake、
    `F-dialog-trust` を実物逐語に更新 — 旧 `F-trust-dialog` は旧 CLI 文言で
    未使用だった)を先に red で確認(deftest: dialog=None で assert 失敗 /
    S18: dismissal Enter が永遠に来ずタイムアウト = hang の再現)→ 実装後
    green。bare Enter は固有バイトを持たないため S18 は順序で証明
    (dialog 表示中に `\n` が着く → その後にしか prompt paste が match
    しない)。
  - 実 launch 強制テスト(herdr backend、nameissoap プロファイル): launch の
    pre-seed を stripper スレッドで剥がし続けて実物 trust dialog を強制
    発生 → 帯域外 herdr read で実物 frame を確認(逐語一致)→ fast-path が
    Enter で dismiss → **launch 5.2s / E2E 全体 9.8s PASS**(byte-faithful
    payload)。プロファイル復元済み・workspace リーク無し。
  - 一般化(ユーザー裁定 2026-07-07:「少なくとも launch は黙って永遠に
    待たず、明確なエラーで fail せよ」)→ 下記 fail-closed 契約修正で実装。

### launch fail-closed 契約修正(2026-07-07、oracle からの意図的乖離)

- **旧縮退(oracle verbatim)**: `wait_for_repl_idle` の予算(120s)が尽きたら
  False を返し、呼び手は**構わず prompt を paste**していた。R9 外の未知
  dialog が startup を塞いでいる場合、prompt は dialog に送出され、session
  は誰にも観測されない silent hang になる(trust dialog カバレッジ欠落が
  この縮退で「120s 待ち → hang」に化けた)。
- **新契約**: 予算切れ = fail-closed。prompt は配送しない・session 行は
  永続しない・作った mux session は `kill-session` で掃除・launch RPC は
  画面 tail(最終 capture 15 行)を同梱した typed error を返す。「何が
  startup を塞いだか」がエラーにそのまま写る。
- **予算 knob**: `DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS`(env-only、
  S19 watchdog knob と同じ use-site 読み。未設定は oracle 定数 120s)。
  host が `params["repl_idle_max_wait_seconds"]` に注入(max_running と
  同じパターン)。
- **TDD**: launch deftest ×2(未知 dialog frame → typed error + 配送ゼロ +
  掃除 + 行なし / knob 注入で予算圧縮)を red → green。conformance
  `test_s18_unknown_dialog_fails_launch_closed`(新 frame `F-dialog-unknown`、
  knob=3s)は tmux / herdr 両 backend green(~6s)。cleanup assert は
  backend 中立の `session_exists_out_of_band`(harness 追加)で判定。
- R9 の 5 dialog は従来通り fast-path で dismiss される(fail-closed は
  「R9 が知らないもの」だけに効く)。既知 dialog の増設と fail-closed の
  網は独立に進化できる。
- **codex trust dialog(R9 外)**: 実物 capture 済み。`detect_dialog=None` /
  **`has_idle_prompt=True`**(`\n› 1. Yes, continue` が `"\n› "` を含む)→
  **破壊的**(idle 誤認 → paste → プロンプト中の数字が `2. No, quit` を
  選択して codex 即死)。canonicalize バグ(上記)で踏む。R9 追加可否は
  orch issue `codex-trust-canonicalize` の設計判断。

## conformance 実行結果(herdr backend、2026-07-07 時点)

| Scenario | Result | 備考 |
|---|---|---|
| S1 golden path | PASS | capture ansi 化後も green(位相問題再発なし) |
| S6 stall judge (2) | PASS | |
| S6b judge unavailable (2) | PASS | |
| S18 dialog fast-paths (6) | PASS | trailing-space 修正後。trust dialog + unknown-dialog fail-closed 追加(2026-07-07)で 4→6 本 |
| S19 watchdogs (3) | PASS | S19c は break_pane_observation_out_of_band 経由 |
| herdr deftests (11) | PASS | 純関数 5 + 実 server smoke 6(幾何学 pin 追加後) |
| launch deftests (13) | PASS | fail-closed ×2 追加(11→13 本) |

tmux 全スイート回帰: trust + fail-closed 追加後 **37 passed, 1 skipped**
(397.54s、基準 35 + trust 1 + unknown 1、回帰なし)。herdr 側 14 本
(S18 trust/unknown 込み)も green(125.52s)。

**S6 flake の記録(2026-07-07)**: tmux 全スイート実行中に 1 回、
`test_s6_stall_bounded_judge_exhaustion`(M2 — fail-closed の対象経路外)の
launch RPC が socket read でブロックしたまま pytest-timeout に達した。
S6 単体再実行 2/2 green + 全スイート再実行 37/37 green で再現せず。
S1 flake(位相敏感)と同様、CI で再発するなら要調査(契約弱化はしない)。

herdr deftest `test-herdr-lifecycle-smoke` の flake 修正(2026-07-07):
`pane-current-command` は session 作成直後、shell rc の子プロセス
(mkdir / scutil 等)を一時的に返す(実測 2/3 で flake)。foreground は
「最終的に idle shell」という eventual な物理なので、テスト側を
poll-until-shell(5s 上限)の eventual assert に修正。monitor loop 自体は
周期観測なので一時 foreground を zombie 誤判定しない(修正は test-only)。
修正後 5 連続 11/11 green。

幾何学修正(専用 workspace 化)後の再実行: 12 本 suite ×2 回のうち 1 回目は
S1 が flake(`report_result not accepted: []` — journal に report_result
イベントが 1 件も無い)、単発再実行と 2 回目 suite は 12/12 green
(113.23s)。発生率 1/3。S1 は retry 予算(0.05s×200=10s)の位相敏感の前科が
あるテストで、launch 経路に workspace.create / pane.close の RPC 2 回が
増えた影響の可能性がある — orch 着地時に CI で再発するなら retry 予算の
再検討対象(契約自体の弱化はしない)。workspace churn のリークは無し
(12 テスト後の workspace list はデモ用 1 件のみ)。
