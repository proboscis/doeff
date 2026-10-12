# 設計検証 lt-0P0S7Z32CCYGP5Z2Q7XXMM2XXJ — file の見張りの待ちを既定の executor から外す

## この文書の目的

読み手が、(1) 何をなぜ変えたか、(2) 変更が M-wait の内側に閉じている根拠、(3) 盲検で見つかった失敗例と、その直し方・残した物を、
code を読まずに判断できるようにする。

## 完了の範囲

| 段 | 状態 |
|---|---|
| 実装 1 回目 | 済み — doeff aefc034f(L2223)。agora-controllers f0396912(L6489)で `doeff-min-commit` を上げた |
| 盲検 A・B | 済み(1 回目に対して)— evidence/blind-a.md・blind-b.md・blind-meta.md |
| 盲検 B の失敗例 1 の修正 | 済み — doeff 5344008c6(branch wt/design-check-file-watch。この記録と同じ便で着地させる) |
| 本番への配備 | 依頼済み(lt-JQEMQN8FM213B4QZDXDEBDJYME)。本番での効き目(ターン 32 本越えで不達 0 件)はこの検証の外 — card ki-0544 の条 2 として配り役が数える |

## 問題と要求

```
今(b32605d92)
  走るターン 1 本 ── NextFileChanges の待ち(os_files._NextChanges: asyncio.to_thread)── 既定の executor の thread を 1 本持ち続ける
  新しい HTTP の接続 ── loop.getaddrinfo ─────────────────────────────── 同じ executor(32 本)の後ろに並ぶ
  1 つの turn-host でターン > 32 本 → 接続の timeout(預かり所 2 秒・下書き 0.5 秒)

目標(aefc034f + 5344008c6)
  走るターン 1 本 ── NextFileChanges の待ち(os_files._wait_in_thread)── 待ちごとの専用の daemon thread
  新しい HTTP の接続 ── loop.getaddrinfo ─────────────────────────────── 既定の executor(見張りの数に依らず空き)
```

要求: 見張りの待ちが event loop の既定の executor の thread を持たない事。待ちの法(変化を落とさない・close と待ちを重ねない —
card ki-715d1af556eb)と、失敗した待ちが待つ program へ例外として届く事は変えない。

## 事前の主張(盲検の前に固定)

- 将来の変更要求: 1 つの process が持つ見張りの数が増える(ターンの数の天井を上げる)。既定の executor を使う他の仕事の待ち時間が、
  見張りの数で変わってはならない。
- 変える module: M-wait だけ。変えない module: M-notifier・M-handler・M-loop-executor。
- 局所化できる理由: 待ちの thread をどこで持つかは `_NextChanges` の内側の知識で、効果(`NextFileChanges`)の型と答えの形
  (`FilesChanged`)は変わらない。

## 責務と強制方法

| module | 責務 | 強制 |
|---|---|---|
| M-notifier `_Notifier` | close と待ちを重ねない(最後に出た者が閉じる) | 既存の検 `test_os_watch_never_closes_the_library_while_a_wait_is_inside_it` |
| M-wait `_NextChanges`・`_wait_in_thread`・`_settle` | 待ちごとに専用の daemon thread を 1 本起こし、時間切れは同じ thread で待ち直し、変化か失敗(BaseException を含む)を `call_soon_threadsafe` で future へ渡す。future が done(取り消し)になったら今の待ちの後に止まる | 検 3 本(C1〜C4・C7・C8) |
| M-handler `os_file_watch_handler` | 効果を notifier の操作へ言い換える | 既存の検(変更なし) |
| M-loop-executor | asyncio の既定の executor(外の物・変えない) | — |

表現できない条件: 「既定の executor を使わない」を型や静的規則では縛っていない(`to_thread` を禁じる semgrep の規則は足していない)。
検 C1 が振る舞いで縛る。検は通常の変更時の経路(doeff の pytest の `packages/doeff-events/tests`)から呼ばれる。

## 再現と修正

| 検 | 命令 | 直す前 | 直した後 |
|---|---|---|---|
| C1/C2 executor の空き | `uv run --no-sync pytest -q packages/doeff-events/tests/test_file_watch.py -k default_executor` | os_files = b32605d92 で `(2, False) != (4, True)` の赤(evidence/before-fix-default_executor.log) | 緑(evidence/after-fix-tests.log・12 passed) |
| C3/C4 取り消しの後の thread | `... -k cancelled_wait` | b32605d92 で `(False, 1) != (True, 1)` の赤(evidence/before-fix-cancelled_wait.log) | 緑(同上) |
| C5/C6 本物の watchfiles | `uv run python evidence/probe.py 40` / `60` | b32605d92 で 3 秒越え(evidence/probe-before.log) | 0.046・0.057 秒(evidence/probe-after.log) |
| C7/C8 notifier の panic | `... -k panic`(同名を含む既存の検 1 本も選ばれる) | aefc034f で `TimeoutError`(5 秒で答え無し)の赤(evidence/before-fix-panic.log) | 緑(evidence/after-fix-tests.log) |

### 盲検の結果と扱い

| 所見 | 出所 | 新旧 | 扱い |
|---|---|---|---|
| notifier の待ちが BaseException(pyo3 の PanicException)を出すと thread が死に、待つ task が永遠に止まる | B 失敗例 1(実測) | この変更で生じた(to_thread は BaseException も渡していた) | 直した: `_wait_in_thread` が BaseException を捕まえて future へ渡す。検 C7/C8 を足した(5344008c6) |
| 失敗を task へ渡す振る舞いを縛る検が無い(try/except を消した変異 M5 が 11 本を通る) | B 検の穴 | — | C7 が縛る(try/except を消すと thread が死んで C7 が赤) |
| `loop.is_closed()` の確かめを消した変異 M3 が通る | B 検の穴 | — | 残した: 害は process の終わりだけで、daemon thread のため小さい |
| 取り消された待ちの thread が、取り消しの後に来た変化を受け取って捨てる | A・B(実測) | 直す前から在った(新旧同じ 8/8・3/8) | この依頼の範囲外として残した。依頼元へ報告する |
| 待ちごとの thread が 50 ms ごとに起きるので CPU が見張りの数に比例(400 本で 11.9%) | B 観察(実測) | 新しい性質(旧は 32 本を越えた待ちが動いていなかった) | 受け入れた: turn-host の見張りはターンの数(天井 50 前後)で、50 本で 1.9% |
| C1 は 2 本・4 本の比だけ / 取り消しの検は 5 秒の上限を見ていない | A 検の穴 | — | 残した: 専用の pool に差し替える退行は C1 と C3 の組で捕まる(B の推論)。5 秒は notifier の時間切れの定数のまま |

変更シナリオの `claim`・`expected_scope` は盲検の前の主張のまま。盲検の後も実測の波及は M-wait だけだった(5344008c6 も
`_wait_in_thread` と `_settle` の型だけ)。

## 未接続・未検証

- 本番での効き目は未検証(配備の後)。
- 12:40〜12:41 JST の 31 件(閾の少し下)の元は調べていない。
- 取り消しの後の変化の取りこぼし(直す前から在る)は直していない。
