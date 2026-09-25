# 盲検 B の返答(未加工)

起動: Agent tool・subagent_type=general-purpose・model=opus(要求 claude-opus-5-5・effort xhigh は起動口に effort の欄が無く指定できず — 観測できたモデルは未確認)
agentId: a1510c7de7d77eb77・起動 2026-09-25T20:51:07Z 頃・所要 1998434 ms・tool_uses 84
fallback の理由: 第 1 候補 gpt-6-astra(low)は codex の実行ファイルがこの pod に無い(`codex --version` → command not found)
入力: /tmp/blind639/input.md(sha256 e3e5a0697f4f5d3a8f2120bf659ea1ac70ca168afaaae59eb7f95776fc4eed30 = blind-input.md と同一)
文脈: 親の会話を fork・resume せず新しい文脈で起動。A の返答は見せていない(A・B は同じ 1 つの返事の中で同時に起動)。

---

反例は 1 つ作りました。提示された検査はすべて変更前と同じ判定で通り、それでも実行時に責務違反が起きることを実測で確かめています。作業樹は読むだけにとどめ、反例は作業樹の外の clone `/tmp/blind639-b/clone`(a3f34bb6 から作成)に入れました。ただし作業樹の `.venv` にキャッシュ file を 1 つ書いてしまったので、5 節の末尾で報告します。

## 1. 機能要求と差分

**要求(入力の S3 の例をそのまま使用)**: 器を入れ替える時、古い host が自分の lease を後継の pid の名義へ原子的に渡す RPC `daemon.handover_lease {successor_pid}` を足す。後継は起動時の `db-acquire-lease` で、自分の名義の未失効の lease を取り直す。

差分の全体は `/tmp/blind639-b/counterexample.diff`(132 行)にあります。違反の中心は次の部分です。

```hy
;; store.hy(db-release-lease の後)
(defk db-handover-lease-body [conn owner-pid successor-pid]   ; M2 の本体。自分名義だけ後継へ upsert、他人名義なら raise
  ...)
(defk actor-handover-lease [actor owner-pid successor-pid]
  {:pre [(: actor StoreActor) (: owner-pid int) (: successor-pid int)]
   :post [(: % NoneType)]}
  "... connection は store の actor だけが持つので、transaction は actor の op の中で組み立てる。"
  (<- (.submit actor (fn [conn]
                       (db-immediate-transaction
                         conn (db-handover-lease-body conn owner-pid successor-pid)))))
  None)
```
```hy
;; host.hy の dispatch-method(deff)。daemon.status の (run (store-write-failure-limit)) と同じ形で書いた
(when (= method "daemon.handover_lease")
  ... (run (actor-handover-lease actor (os.getpid) successor))
  (return {"handed_over" True "successor_pid" successor}))
```
その他の変更は次のとおりです。
- `store.pyi` に `def actor_handover_lease(actor: StoreActor, owner_pid: int, successor_pid: int) -> Program[None]: ...` を追加。
- acquire の本体で、自分の名義の lease なら raise しないように条件を 1 つ追加。
- 実装者が書きそうな検 `test-lease-handover-to-successor` を追加。

`deff` は 1 本も増やしていません。抑制・cast・動的 import も使っていません。

## 2. 本来の所有者と違反する契約

問題の核心は、actor の op が transaction の Program を値にせず、Program のまま返すことです。この Program は actor の connection を抱えたまま actor の thread の外へ出て、呼び手の thread で実行されます。

- **M4(store-actor)が持つ状態**: 単一の connection の直列化が崩れます。RPC の thread が同じ connection で BEGIN IMMEDIATE・upsert・COMMIT を実行します(check_same_thread=False なのでエラーにならない)。
- **M3(lease-entry)が持つ知識**: 「run を実行する位置」の判断が、呼び手(host の `dispatch-method`、検の `<-`)へ移ります。
- **R15(b) と M5 の判断**: 書き込みの失敗が actor の 1 点を通らなくなり、readiness の数えに入りません。
- **M1 の判断**: 「transaction が残る時だけ ROLLBACK」の前提が崩れます。`in_transaction` を別の thread の op と共有するので、他の op が成功として返した書き込みまで巻き戻します。
- **journal の合図(M4)**: 巻き戻された出来事を指したまま残ります。
- **S3 の主張**: 「Program は actor の thread の中で値になる」は成り立ちません。M4 は op が Program を返しても断りません。
- R15 の法 (a) は字面では満たしています(transaction を張るのは `db-immediate-transaction` だけ)。守られないのは法の意図のほうです。

## 3. 各検査が拒否しない理由(すべて実行済み)

| 検査 | 結果 | 理由 |
|---|---|---|
| 台帳の針 | `1 passed` | `\(deff\s` の本数しか見ない。新しい関数は defk なので 35/35 のまま。むしろ M3 と同じ形(`run` を中で行う deff の入口)を新しく足すと 36 > 35 で赤になるため、Program を返す defk の形へ実装者を押す |
| R15 の検 2 file | `62 passed`(追加した検を含む) | どの thread で transaction が走るかを見る検が無い。1 thread の検では最終状態が同じになる |
| semgrep | store.hy 27 規則、host.hy 28 規則、pyi・検の file とも 0 件 | 適用される規則に、actor の op の戻り値や transaction の位置を扱う物が無い |
| doeff-hy-check | 変更前後とも 55 件で同じ集合、新しい行に赤は無い | store.hy の `submit` は注釈が無く型が不明のまま扱われ、`<-` は検められない。stub の `submit` は `Callable[[object], object]` なので Program を返す op も型として正しい。文の位置の検査は束縛された Program を対象外にする |
| code-quality(fast) | `incomplete`(exit 2)、違反 0 | 原因は変更前から在る store.hy:256 の未実行 1 件だけ。着地済みの a3f34bb6 を同じ条件で実行しても同じ判定。追加の通知は store.pyi の登録の案内だけで、fast では止めない |

## 4. 最小の手順と期待結果

検査が通ることの確認:
1. 作業樹の外に `git clone --shared` し、a3f34bb6 を checkout して差分を当てる。
2. 3 節の 5 つの検査を実行する。venv は作業樹のものを使い、`PYTHONPATH=<clone>/packages/doeff-agents/src` を付ける。
3. 注意: 新しい clone では bytecode が無いため、`test_sigterm_…` は host の起動(約 50 秒)が 30 秒の期限を超えて落ちます。これは基準の版でも同じでした。clone の中だけに bytecode を書いて温めた後は、両方とも通りました。

違反の実測は `/tmp/blind639-b/probe/probe_handover.py` と `probe_journal_followup.py` で行いました。実装どおりの置き場(反例)と、M3 と同じく op の中で run する置き場を比べています。

| 観測 | 反例 | op の中で run |
|---|---|---|
| actor の op が返す値 | `Expand`(未実行の Program) | `NoneType` |
| BEGIN・INSERT・COMMIT を実行した thread | 呼び手の thread | actor の thread |
| query_only で `SQLITE_READONLY` を 3 回起こした後の daemon.status | `ready=True`、`consecutive_failures=0` | `ready=False`、`3` |
| 引き渡しの途中に来た別の出来事の記録 | 成功と返った後に ROLLBACK で消える(行 0)。`journal_seq=1`、実際の `MAX(id)=0` | 待たされて後から入る(行 1) |
| その後の本物の出来事への待ち手 | 上限の 2.0 秒まで起きない | 0.2 秒で起きる |
| 途中に来た heartbeat | `cannot start a transaction within a transaction` で失敗 | 成功 |

## 5. 未確認の前提・不足

- **測った物と推測の区別**: 3・4 節の結果はすべて実行して得た物です。ただし割り込みの時点は、検体の中で `store.db_read_lease` を包んで決定的に作りました。本番でこの重なりがどれくらいの頻度で起きるかは測っていません(推測)。
- **失敗の種類**: 実弾の SQLITE_FULL ではなく、決定的に起こせる `PRAGMA query_only` による `SQLITE_READONLY` で確かめました。
- **実行環境**: Python 3.14 の free-threaded 版、SQLite 3.50.4。
- **機能の現実性**: host_slots の blue/green は区画ごとに DB を分けるので、同じ store を引き継ぐ引き渡しは S3 の例に合わせた想定です。transaction を持つ新しい defk の入口を actor 経由で呼べば、どの機能でも同じ形が生じると推測しますが、別の機能では試していません。
- **実行していない物**: 全体の検、code-quality の full(型を含む)、`make lint`、doeff-linter、commit 時の guard。
- **実行の仕方の差**: `uv run` の代わりに作業樹の venv を直接使うか `uv run --no-sync --frozen` を使い、clone を根にして実行しました。作業樹を同期させないためです。
- **入力の誤記**: 入力の基底の SHA `0e7a5aa6dd559a…` は存在しません。実在するのは `0e7a5aa6f6dd559a35448d33c08a97ed9786021d` です。
- **作業樹への書き込み(禁止に反した 1 件)**: bytecode を抑えずに `python -m pytest` を実行した 1 回で、`/home/kento/.worktrees/doeff-wt-639-sessionhost-defk/.venv/lib/python3.14t/site-packages/pytest/__pycache__/__main__.cpython-314.pyc`(444 バイト、21:16:33Z)を作ってしまいました。git の管理外で、`git status` と HEAD は変わっていません。削除の指示が無いので消していません。消すかどうかは決めてください。

関連する file:
- `/tmp/blind639-b/counterexample.diff`
- `/tmp/blind639-b/clone`(反例入り)、`/tmp/blind639-b/clone-base`(比較用)
- `/tmp/blind639-b/probe/`
- 検査の出力: `/tmp/blind639-b/hycheck-base.txt`、`hycheck-cx.txt`、`cq-cx.txt`、`cq-orig.txt`、`semgrep-*.txt`
