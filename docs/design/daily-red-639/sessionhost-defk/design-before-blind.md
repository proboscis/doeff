# 事前の主張(盲検の反例を作らせる前に固定・2026-09-26 JST)

依頼: lt-A5KGD83R9HQJ2K172V61A6VMG9(agora-redesign#639 実装依頼書 3)
完了の範囲: 実装完了まで(本線 a3f34bb68d394c4153c7c7351f07801ebf2827ab に着地済み)。
基準の版: 変更前の本線 7ef0fa772afca54684d5b75b51f286e6e1bb367d(対象の host.hy・store.hy は 0e7a5aa6f6dd559a35448d33c08a97ed9786021d まで同一)。

## 1. 固定した要件(受入条件)

1. `packages/doeff-agents/src/doeff_agents/sessionhost/host.hy` の `store-write-failure-limit` と
   `packages/doeff-agents/src/doeff_agents/sessionhost/store.hy` の `db-immediate-transaction` を deff から defk へ。
   呼び出し側を直す(ADR-DOE-HY-004 R1・R3)。DEFF-ROSTER は増やさない(host.hy 35・store.hy 35)。
2. `db-immediate-transaction` は transaction の本体を受け取る高階の形。本体を 1 度だけ実行し、
   ROLLBACK が元の失敗を隠さない性質(ADR-DOE-AGENTS-004 R15 の法 store-transactions-surface-the-original-failure)を保つ。
3. 受入の検: `docs/adr/defadr_doeff_hy_004_defk_only.hy::test_adr_doe_hy_004_deff_ratchet` の違反から host.hy・store.hy が消える。
   R15 の法 2 本の検と 331af8e2 の SQLITE_FULL の検が緑。

## 2. 責務(module)

| id | 実体 | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- | --- |
| M1 transaction-type | store.hy `db-immediate-transaction`(defk) | 明示の transaction を張る唯一の点 | BEGIN IMMEDIATE・COMMIT・「transaction が残る時だけ ROLLBACK」・元の例外をそのまま出す・巻き戻しの失敗は note | SQLite が SQLITE_FULL 等で自分で巻き戻す事情 | `(db-immediate-transaction conn body)` — conn: sqlite3.Connection、body: Program(transaction の本体)。戻り = body の値。Program でない body は :pre で断る |
| M2 lease-decisions | store.hy `db-acquire-lease-body` / `db-heartbeat-lease-body` / `db-release-lease-body`(defk) | lease の判定(未失効の他人名義を拒否・失効の奪還・自分名義だけ釈放) | lease の行の形・TTL・owner の比べ方 | transaction の開き方 | `(…-body conn owner-pid)` → Program。値 = None / None / bool |
| M3 lease-entry | store.hy `db-acquire-lease` / `db-heartbeat-once` / `db-release-lease`(deff・台帳のまま) | StoreActor の thread の中で transaction の Program を値にする境界 | `run` を撃つ位置 | 本体の判定・transaction の開き方 | 名前・引数・戻り・例外は変更前と同じ(host.hy と検が呼ぶ) |
| M4 store-actor | store.hy `StoreActor` | 単一の書き込み connection の直列化・書き込みの健康の数え | queue・thread・total_changes | SQL の中身 | `.submit actor op`(op = conn を取る callable・戻り = 値) |
| M5 host-readiness | host.hy `store-write-failure-limit`(defk)・`dispatch-method` の daemon.status | 続けた書き込みの失敗の上限で readiness を落とす | 上限の knob(env DOEFF_AGENTD_STORE_WRITE_FAILURE_LIMIT) | 数え方(store_health) | daemon.status の ready / not_ready_reason |
| M6 vocabulary-ratchet | docs/adr/defadr_doeff_hy_004_defk_only.hy | 関数の語彙を defk に寄せる台帳 | DEFF-ROSTER と走査 | — | file ごとの deff の本数 <= 台帳 |

## 3. 変更シナリオと事前の主張

### S1(axis = storage・適用)
- 変更: transaction の開き方を変える(BEGIN IMMEDIATE を BEGIN EXCLUSIVE にする、または外部の読み手の lock で
  SQLITE_BUSY の時に本体を 1 度だけやり直す)。
- 主張: 変わるのは M1 の 1 定義だけ。M2・M3・M4・M5 は変わらない。
- 理由: 明示の transaction を張る所は M1 の 1 点で、本体は開き方を知らない Program として渡る。
- 前提: 本体は「transaction の中で走る」ことだけに依存する。やり直しでは同じ Program をもう一度束ねる
  (defk の Program は束ねるたびに本体を最初から走らせる — 実験で確かめる)。
- 予想する波及: M1 のみ。

### S2(axis = effects・適用)
- 変更: lease の本体が doeff の effect を出す(例: 失効した lease の奪還を構造化 log の effect で記録する・時刻を ClockNow effect で読む)。
- 主張: 変わるのは M2(本体)と M3(run の位置に handler を入れる)。M1 は変わらない — 本体の effect は束ねの位置を通って外の handler へ届き、
  本体の失敗(handler が無い時の UnhandledEffect を含む)は同じ規則で巻き戻る。
- 予想する波及: M2・M3。変わらない: M1・M4・M5。

### S3(axis = concurrency・適用)
- 変更: transaction の操作を新しく足し、StoreActor の thread から呼ぶ(例: blue/green の器の入れ替えで lease を後継へ渡す)。
- 主張: 足すのは M2 に本体 1 つと M3 に入口 1 つ。M1・M4 は変わらない。Program は actor の thread の中で値になり、
  thread の外へ Program のまま出ない。
- 予想する波及: M2・M3(追加)。
- 前提と限界: M4 は op の戻りを値として扱う。op が Program を返した時に M4 が断るかは、この主張の強制の対象として検証する。

### S4(axis = simulation・適用)
- 変更: lease の失効判定を決定的に試す(時刻を ClockNow effect から読み、固定の時刻の handler を差して判定を再現する)。
- 主張: S2 と同じ路。変わるのは M2・M3 で、M1 は変わらない。
- 予想する波及: M2・M3。

### hardware(対象外)
- 理由: store は Python の sqlite3 で 1 つの file を開くだけで、機体の種類で分かれる判断を持たない。volume の容量は storage と R15(b) の健康の数えの範囲。

### distribution(対象外)
- 理由: store は host ごとの 1 file・1 connection(M4)で、複数の機体での共有はしない(器の入れ替えは写しで行う)。
  複数の機体での排他は socket の bind と lease の影(ADR-DOE-AGENTS-004 R10)と ACP の配置の責務で、この変更は触れない。

## 4. 強制方法(事前)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| deff の新設禁止(M6) | 走査の針 | defadr_doeff_hy_004_defk_only.hy::test_adr_doe_hy_004_deff_ratchet | pytest(日次・名指し) | 本数だけを見る |
| 本体を 1 度だけ・transaction の中で走らせる(M1) | defk の :pre `(: body Program)`・振る舞いの検 | store.hy・sessionhost_store_deftests.hy test-lease-transaction-surfaces-disk-full-not-the-rollback | pytest | 呼び手が run を忘れた Program を actor へ渡す誤りは M1 の検では捉えない |
| 元の失敗を隠さない(M1・R15) | 振る舞いの検(SQLITE_FULL の実物) | 同上・test_lease_acquire_and_heartbeat・test_lease_release_owner_idempotent_and_successor | pytest | 「BEGIN / ROLLBACK を書くのは M1 だけ」は静的な規則が無い |
| readiness の上限(M5) | 振る舞いの検 | test_sessionhost_host.py の 2 本 | pytest | — |
