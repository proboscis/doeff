あなたは責務分離に対する反例を生成してください。
このシステムで合理的に求められうる将来の機能追加・運用変更を1つ挙げ、
それを実装すると複数の責務を持つモジュールの変更が必要になる例を具体化してください。
一般的な設計レビューや承認判定は求めていません。

元の主張の前提を守っているかを示してください。前提を変更する例なら、
契約の適用範囲外であることを明示してください。ファイルが複数変わるだけでは欠陥ではありません。
意図した公開契約の拡張、配線の変更、共同の不変条件と、隠すはずの知識の漏洩を区別してください。
不要に極端な規模、無関係な要求、意図的な破壊は使わないでください。

返答には次を含めてください。
1. 現実的な変更要求と、その要求が必要になる理由。
2. 関係する元の主張・前提と、予想に反して変わる責務・モジュール・契約。
3. どの知識・判断がどこへ漏れるために変更が波及するか。契約拡張ならその区別。
4. 最小の具体的な入力・変更例と再現手順、観測すべき結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
反例が見つからない場合は探索した範囲と不足を述べ、合格・安全とは結論しないでください。


---
以下が固定済みの材料です(実コードと検査は記載の読み取り対象を読んでください。読み取り対象の外は推測しないこと)。

# 盲検の材料(A・B に共通)

対象: doeff の `packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr.hy`(herdr の端末多重化の socket API を
sessionhost の effect に束ねる handler)のうち、pane の前面の command を読む部分。対象の版 = doeff 72da0ff1195a8078909228ac8d1115c28daddeae。

読み取り対象(変更しないこと):
- `/tmp/blind-herdr-argv0/src/` = doeff の 72da0ff1 の写し(`packages/doeff-agents`・`docs/adr`・`.semgrep.yaml`・`tests/semgrep`・`Makefile`・`.pre-commit-config.yaml`)。
- `/tmp/blind-herdr-argv0/herdr-0.9.1/` = herdr v0.9.1 の source の抜粋(`src/api/schema/panes.rs`・`src/app/api/panes.rs`・`src/platform/{mod,linux,macos}.rs`)と公開の API 定義 `herdr-api.schema.json`(protocol 22)。

## 1. 要件(固定)

1. `herdr-pane-current-command-io` は、herdr の答えのどの欄が保証されているかを herdr の契約で確かめ、保証された欄から前面の command を読む。
2. 欄が無い時に `KeyError` にも黙った既定値にもしない。答えが契約の外なら型のある失敗で名指す。
3. `argv0` の無い `process_info` の答えで前面の command が読める単体の検を足す。

herdr の契約(v0.9.1・protocol 22):
- `PaneProcessInfo` の必須欄は `pane_id` だけ。`foreground_processes` は空なら鍵ごと省かれる。
- `PaneProcessInfoProcess` の必須欄は `pid` と `name` だけ。`argv0` / `argv` / `cmdline` / `cwd` は省略・null がありうる。
- Linux の実装は `argv0` を埋めない。macOS の `argv0` は argv[0] の basename から先頭の `-` を 1 つ外したもの(空なら省く)。
- `pane.process_info` が返す error code は `pane_not_found` だけ。

利用状況: sessionhost の監視の周回が、running の行ごとに effect `TmuxPaneCurrentCommand` を出し、前面が idle shell(`policy.hy` の
`IDLE-SHELL-COMMANDS` = zsh・bash・sh・dash・fish・ksh)に戻っていれば、その行を exited(原因 vanished)で終える。substrate は tmux
(`substrate.hy`)と herdr(`substrate_herdr.hy`)の 2 つで、どちらも同じ effect に答える。herdr は Mac と Linux(日次の検査の機体)で動く。

## 2. 責務(module)と公開契約

| id | 実体(path) | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- | --- |
| M1 herdr-rpc | `substrate_herdr.hy` の `herdr-call`・`HerdrApiError` | herdr socket への 1 RPC(1 call = 1 接続)と error 封筒の型づけ | newline-JSON・error 封筒 → `HerdrApiError(code)` | socket の扱い | `(herdr-call socket-path method params)` → result の dict |
| M2 process-info-reader | `substrate_herdr.hy` の `herdr-foreground-command`(defk・純関数)・`HerdrContractError` | `pane.process_info` の答え → 前面の job の先頭の process の command 名 | 保証欄と省略されうる欄・読む順 argv0 → argv の先頭 → name・argv[0] の正規化・省略/空 = None・契約の外 = `HerdrContractError` | herdr の platform ごとの差・省略の規則 | `(herdr-foreground-command result)` → `str | None`、契約の外は `HerdrContractError` |
| M3 current-command-io | `substrate_herdr.hy` の `herdr-pane-current-command-io`(defk) | 1 pane の前面の command の観測 | `pane_not_found` = None・ほかの封筒は送出 | 答えの欄の読み方(M2 に委ねる) | `(herdr-pane-current-command-io socket-path pane-id)` → `str | None` |
| M4 herdr-substrate | `substrate_herdr.hy` の `herdr-substrate` の `TmuxPaneCurrentCommand` の腕 | effect を M3 へ束縛する | effect と IO の対応 | herdr の API | effect の答え = `str | None` |
| M5 zombie-reaper | `policy.hy` の zombie reaper(`IDLE-SHELL-COMMANDS` の近く) | 前面が idle shell に戻った running の行を終える判断 | idle shell の語彙・None は判断しない | substrate の種類 | effect `TmuxPaneCurrentCommand` を読むだけ |
| M6 tmux-substrate | `substrate.hy` の `TmuxPaneCurrentCommand`(`#{pane_current_command}`) | tmux での同じ観測 | tmux の format | — | 同じ effect の答え |

effects: M2 は effect を出さない。M3 は socket IO だけ。M5 は effect を出して store へ書く。寿命: M1 は call ごとに接続を張って閉じる。M2 は状態を持たない。

## 3. 将来の変更についての主張

- S1(hardware): 別 platform の herdr(Windows・WSL・argv0 を埋め始める Linux 版・argv を返さない版)。主張: 答えが契約の中なら M2 で吸収し、M3・M4・M5 は変わらない。正規化の規則を変えるなら M2 の 1 定義だけ。前提: platform の差は欄の有無と値の形にだけ現れる。
- S2(effects): herdr の新版が (a) 前面の command の保証欄を足す、(b) pane 不在に新しい error code を足す。主張: (a) は M2 だけ、(b) は M3 だけ。M1・M5 は変わらない。前提: 封筒の形(result / error)は変わらない。
- S3(simulation): 実 herdr の無い機体で、実機の答えを使って前面の読みと zombie 判定を決定的に試す。主張: 答えを M2 に dict で渡すか偽の socket から M3 に返すだけで再現でき、本体を変えない。M5 は effect の偽の handler で試せる。
- S4(concurrency): 監視の周回が複数の session の前面を並行に読む。主張: M1・M2・M3 は変わらない(M2 は純関数・M1 は call ごとの接続)。
- storage・distribution は対象外(この経路は保存しない・substrate は同じ機体の socket だけを読む)。

## 4. 存在する検査と実行経路

- pytest(名指しの実行と日次): `packages/doeff-agents/tests/test_sessionhost_substrate_herdr.py`(`sessionhost_substrate_herdr_deftests.hy` の deftest を公開する)。
  `test_herdr_foreground_command_follows_the_process_info_contract`(M2 の純関数の表)・`test_herdr_pane_current_command_reads_answers_without_argv0`(偽の herdr socket で M3)・
  `test_herdr_lifecycle_smoke` ほか(実 herdr が無ければ skip)。policy の検は `tests/sessionhost_policy_deftests.hy`(effect の偽の handler)。
- semgrep(pre-commit と `make lint-semgrep`): `.semgrep.yaml`。この file に効く規則は `paths.include` を見ること(例: `doeff-agents-herdr-label-holders-must-not-be-indexed`)。
  規則の検体の検は `tests/semgrep/test_vm_failfast_semgrep_rules.py`。
- 関数の語彙の台帳(defk): `docs/adr/defadr_doeff_hy_004_defk_only.hy`。
- defk の `:pre` / `:post` の型の宣言は実行時に検める。
