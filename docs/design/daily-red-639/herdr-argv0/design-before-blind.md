# 事前の主張(盲検の反例を作らせる前に固定・2026-09-26 JST)

依頼: lt-8BDJEXK2W793EAYX11SYS9JQXN(agora-redesign#639 実装依頼書 H)
完了の範囲: 実装完了まで(本線 72da0ff1195a8078909228ac8d1115c28daddeae に着地済み・L393)。
基準の版: 変更前の本線 294aac38e5708f400f0fba951a608ca3bf7a8e4a。
変更の commit: 6fdccb9e(検の先行)・72da0ff1(修正)。

## 1. 固定した要件(受入条件)

1. `packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr.hy` の `herdr-pane-current-command-io` は、
   herdr の答えのどの欄が保証されているかを herdr の契約(source か API の定義)で確かめ、保証された欄から前面の
   command を読む。`argv0` が保証されない欄なら、保証された欄から導く。
2. 欄が無い時に `KeyError` にも黙った既定値にもしない。答えが契約の外なら型のある失敗で名指す。
   docstring の「name は version 文字列のことがある — argv0 を使う」の実測は残し、理由を更新する。
3. `argv0` の無い `process_info` の答えで前面の command が読める単体の検を足す。直す前の形で赤になることを示す。
4. 受入: 3 の検が緑・直す前の形で赤。`test_sessionhost_substrate_herdr.py` の全体が Mac と Linux の機体(zeus)で緑。

確かめた herdr の契約(herdr v0.9.1・protocol 22・`docs/next/api/herdr-api.schema.json` と `src/api/schema/panes.rs`):
- `PaneProcessInfo` の必須欄は `pane_id` だけ。`foreground_processes` は空なら鍵ごと省かれる(`skip_serializing_if = "Vec::is_empty"`)。
- `PaneProcessInfoProcess` の必須欄は `pid` と `name` だけ。`argv0` / `argv` / `cmdline` / `cwd` は省略・null がありうる。
- Linux の実装は `argv0` を埋めない(`src/platform/linux.rs` の `argv0: None`)。macOS の `argv0` は argv[0] の basename から先頭の `-` を 1 つ外したもの(空なら省く)。
- `pane.process_info` が返す error code は `pane_not_found` だけ(`src/app/api/panes.rs`)。

## 2. 責務(module)

| id | 実体 | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- | --- |
| M1 herdr-rpc | `herdr-call`・`HerdrApiError` | herdr socket への 1 RPC(1 call = 1 接続)と error 封筒の型づけ | newline-JSON・request line の形・error 封筒 → `HerdrApiError(code)` | socket の扱い | `(herdr-call socket-path method params)` → result の dict。封筒は `HerdrApiError` |
| M2 process-info-reader | `herdr-foreground-command`(defk・純関数)・`HerdrContractError` | `pane.process_info` の答え → 前面の job の先頭の process の command 名 | 保証欄(pid・name)と省略されうる欄・読む順 argv0 → argv の先頭 → name・argv[0] の正規化の規則・省略/空 = 前面が不明 = None・契約の外 = `HerdrContractError` | herdr の platform ごとの差(Linux は argv0 無し・macOS は有り)・serde の省略の規則 | `(herdr-foreground-command result)` → `str | None`、契約の外は `HerdrContractError` |
| M3 current-command-io | `herdr-pane-current-command-io`(defk) | 1 pane の前面の command の観測(RPC を 1 回撃ち、不在を値に落とす) | `pane_not_found` = pane 不在 = None(tmux の display-message 失敗と同じ)・ほかの封筒は送出 | 答えの欄の読み方(M2 に委ねる) | `(herdr-pane-current-command-io socket-path pane-id)` → `str | None` |
| M4 herdr-substrate | `herdr-substrate` の `TmuxPaneCurrentCommand` の腕 | effect `TmuxPaneCurrentCommand` を M3 へ束縛する | effect と IO の対応 | herdr の API | effect の答え = `str | None` |
| M5 zombie-reaper | `policy.hy` の zombie reaper(`IDLE-SHELL-COMMANDS`) | 前面が idle shell に戻った running の行を exited/vanished で終える判断 | idle shell の語彙 `#{zsh bash sh dash fish ksh}`・None は判断しない | substrate(tmux か herdr か) | `tmux-pane-current-command` effect を読むだけ |
| M6 tmux-substrate | `substrate.hy` の `TmuxPaneCurrentCommand`(`#{pane_current_command}`) | tmux での同じ観測 | tmux の format | — | 同じ effect の答え |

effects: M2 は effect を出さない(純関数・defk の Program)。M3 は socket IO(`herdr-call`)だけ。M5 は effect `TmuxPaneCurrentCommand` を出し、store の effect で行を書く。
寿命: M1 は call ごとに接続を張って閉じる。M2 は状態を持たない。M3 は call ごとに 1 RPC。停止・再開・キャンセルの持ち主は M5 の監視の周回(この変更では触らない)。

## 3. 変更シナリオと事前の主張

### S1(axis = hardware・適用)— 機体・platform の差
- 変更: 別の platform の herdr(例: Windows 版・WSL・将来の Linux 版が `argv0` を埋め始める・`argv` を返さない版)で動かす。
- 主張: 答えが herdr の契約の中にある限り、M2 の読みで吸収し、M3・M4・M5 は変わらない。正規化の規則を変える必要が出たら(例: Windows の `zsh.exe`)、変わるのは M2 の 1 定義だけ。
- 前提: platform の差は `pane.process_info` の欄の有無と値の形にだけ現れ、error 封筒や RPC の形は変えない。
- 予想する波及: M2 のみ(契約の中なら 0 箇所)。

### S2(axis = effects・適用)— herdr の API の答えが変わる
- 変更: herdr の新版が (a) 前面の command の保証欄を足す(例: `command`)、または (b) pane 不在に新しい error code を足す(例: `pane_exited`)。
- 主張: (a) は M2 だけ。(b) は M3 だけ。M5 は変わらない。M1 は変わらない。
- 前提: RPC の封筒の形(result / error)は変わらない。
- 予想する波及: (a) M2・(b) M3。

### S3(axis = simulation・適用)— 決定的な再現
- 変更: 実 herdr の無い機体(cluster の pod・CI)で、実機で採った答えを使って前面の読みと zombie 判定を決定的に試す。
- 主張: 記録した答えは M2 に dict のまま渡すか、偽の socket から M3 に返すだけで再現でき、M1 と M2 の本体を変えない。M5 の判断は effect `TmuxPaneCurrentCommand` の偽の handler で試せる(既存の policy の検と同じ形)。
- 予想する波及: 検の側だけ(本体 0 箇所)。

### S4(axis = concurrency・適用)— 並行の観測
- 変更: 監視の周回が複数の session の前面を並行に読む(thread で同時に M3 を呼ぶ)。
- 主張: M2 は状態を持たない純関数、M1 は call ごとに接続を張るので、M1・M2・M3 は変わらない。
- 予想する波及: 0 箇所(呼び手の並行化だけ)。

### storage(対象外)
- 理由: 前面の command はその周回で観測して M5 が判断に使うだけで、この経路は保存しない(行の状態の保存は M5 と store の責務で、この変更は触れない)。

### distribution(対象外)
- 理由: herdr は機体ごとの端末の多重化で、substrate は同じ機体の socket(`~/.config/herdr/herdr.sock`)を読む。機体をまたぐ配置は ACP の責務で、substrate が遠くの herdr を読む要求は無い。遠くを読むことになっても変わるのは M1 の接続先だけで、M2 は dict の上の純関数のまま。

## 4. 強制方法(事前)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| M2 が保証欄から読み、契約の外を名指す | 振る舞いの検(契約の中 6 形・契約の外 8 形)・`:pre (: result dict)`・`:post (: % (| str None))` | `tests/sessionhost_substrate_herdr_deftests.hy::test-herdr-foreground-command-follows-the-process-info-contract` | pytest(名指し・日次) | herdr の schema と検の表を突き合わせる自動の検は無い(schema の版が上がっても検は気づかない) |
| M3 の不在の写像(pane_not_found だけ None・ほかは送出)と、zeus の実物で読める | 偽の herdr socket の検 | `test-herdr-pane-current-command-reads-answers-without-argv0` | pytest | 偽の socket は herdr の実物ではない(実物は smoke が見る) |
| 実 herdr での寿命(M1〜M4) | smoke の検(実 herdr が無ければ skip) | `test-herdr-lifecycle-smoke` ほか | pytest(Mac・zeus) | herdr の無い機体では skip |
| 契約の外は `HerdrApiError` と別の型 | 型(`HerdrContractError` は `HerdrApiError` の子ではない) | `substrate_herdr.hy` | 検が `except [HerdrContractError]` で受ける | 呼び手が `RuntimeError` で広く受ければ区別は消える |
| `pane.process_info` の答えを読むのは M2 だけ | **静的な規則は無い**(慣習) | — | — | 別の関数が `foreground_processes` や `argv0` を直に読んでも、どの検査も拒否しない |
| 関数は defk | ADR-DOE-HY-004 の台帳 | `docs/adr/defadr_doeff_hy_004_defk_only.hy` | pytest | 本数だけを見る |
