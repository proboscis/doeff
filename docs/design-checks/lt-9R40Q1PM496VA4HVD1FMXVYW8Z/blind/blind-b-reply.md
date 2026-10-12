## 失敗例: 不具合を直し、その唯一の回帰の検を設計検証の記録の中に置く

ここに書く結果は、作業木 HEAD 54a945270 の写し `/tmp/blind-9r40-B/repo` で取りました。読む作業木には何も書いていません。

### 1. 機能要求と差分

**要求(作った例)**: 「memo の policy を設定 file から渡すと `recompute_cost="EXPENSIVE"` が ValueError になり、呼び手が既定の CHEAP へ落としている。綴りの正規化は `ensure_memo_policy` の 1 か所で行う。設計検証の流れで直し、直した版の検を証拠として残す。」

**差分A(直し)** `packages/doeff-core-effects/doeff_core_effects/memo_policy.py`
```diff
-    return RecomputeCost(value)
+    # 設定 file や環境変数から来る綴り("EXPENSIVE"・" Session ")も受ける。
+    return RecomputeCost(value.strip().lower())
...
-    return Lifecycle(value)
+    return Lifecycle(value.strip().lower())
```

**差分B(検)** 前例の `lt-D4D7…/evidence/scripts/test_handle_scope.revision.py` にならい、回帰の検を記録の中にだけ置きます。package の tests には足しません。
- `docs/design-checks/lt-7MEMQP0C1CY0NRM4N2ZED8K3TA/design.md` の本文に「直した版の検: evidence/scripts/test_memo_policy_normalization.py」と書く。
- `docs/design-checks/lt-7MEMQP0C1CY0NRM4N2ZED8K3TA/evidence/scripts/test_memo_policy_normalization.py` を置く。中身は `ensure_memo_policy` を import し、"EXPENSIVE"・" Session "・mapping で渡す形・未知の綴りの 7 件を確かめる普通の pytest です。

### 2. 本来の所有者と、破られる契約

- **回帰の守り(この振る舞いが公開の契約だという知識)**: 本来は M-roots の日次の母集団(`packages/doeff-core-effects/tests`)が持ちます。それが M-records へ移ります。M-records の契約は「その時点の証拠・着地の後は書き換えない・日次ではない」です。生きた契約の唯一の守りをここに置くと、この契約を破ります。後で守りがずれても、日次では誰も気づきません。
- **「この検の名の file は写しで、走らせる物ではない」という判断**: 本来は M-pop の EXCLUDED が、行ごとに理由をつけて持ちます。接頭辞 `docs/design-checks/` の行を入れたことで、この判断は記録を書く人の「どの dir に置くか」へ移りました。行の理由の「直した版の検の写し」は中身についての主張ですが、どの検もそれを確かめていません。S1 の前提「記録の中の検の名の file は写し」は決まりとして書かれているだけで、強制されていません。
- **R3(ADR-DOE-ENFORCE-001 R8)**: 守るはずの形は「検のつもりの file が、日次のどの母集団にも属さないまま黙って走らない形を赤にする」です。今回の file はまさにその形なのに、緑になります。

### 3. 各検が拒まない理由(どれも実測で緑)

- **`test_every_test_file_belongs_to_a_daily_population`**: file は `test_*.py` なので検の file に数えられます。どの根の下にもありません。ただし `_is_excluded(path, "docs/design-checks/")` が真なので、外れた file として数えられません。
- **`test_exclusion_table_has_no_stale_rows`**: `docs/design-checks/` の行が覆う file が 2 本(この file と D4D7 の 1 本)になるだけで、行は古くなりません。
- **`test_every_test_named_file_is_collected_by_some_collector`**: この検が見るのは名前の pattern だけです。`test_*.py` は python_files の既定に当たるので、集められると判定されます。場所(testpaths)は見ません。

三つとも見ているのは path と名前だけで、「写し」か「唯一の守り」かを区別できません。

### 4. 確かめる手順と結果(実測)

1. `git archive HEAD` を `/tmp/blind-9r40-B/repo` へ展開し、`uv sync --group dev --frozen` を走らせる。
2. 変更を入れる前に三つの検を走らせると、3 passed。
3. 差分A・Bを入れて三つの検を走らせると、3 passed。
4. `/tmp/blind-9r40-B/scripts/where_is_it.py` で M-pop 自身の関数に場所を聞く。
   - 検の file として数える: True
   - どれかの根の下: False
   - 当たる除外の行: `['docs/design-checks/']`
5. 差分Aを元に戻し、後の回帰を模す。
   - 三つの検は 3 passed のまま。
   - 記録の検を直に走らせると、6 failed / 1 passed。
6. 対照として、同じ file を `docs/notes/` に置く。母集団の検は 1 failed になり、「除外の表にも無い検の file が 1 本」と出る。記録の dir に置いたことだけが緑の理由です。
7. `make test-changed TEST_CHANGED_ARGS="--base HEAD"` を 2 回走らせると、どちらも rc=0。記録の検は「変えた検」に選ばれますが、60 秒の上限の内に番が来ず「未測」でした(機体の負荷で x4.1)。未測は登記を止めません。
8. grep で探すと、日次の母集団の中に memo の policy の大文字の綴りを見る検はありません。これは静的に探しただけです。

### 5. 未確認の前提と不足

- **推測(日次の全体は走らせていない)**: 日次の root の段は引数なしの pytest なので testpaths だけを集め、packages の段は `_population_roots` の根だけを訪ねます。したがってこの file は日次で走らない、と判断しました。根拠は設定と関数の出力を読んだことです。
- **未確認**: 負荷の低い時に make test-changed がこの検を時間内に緑で走らせるか。直に走らせた時は約 5 秒で 7 passed でした。
- **見ていない検**: 提示されていない検(`test_sealed_records_are_named.py`・enforcement-ledger・land-queue の tiers など)がこの記録を拒むかは見ておらず、推測もしていません。
- **未確認の前例**: D4D7 の前例で、元の検が package の側にもあるかは確かめていません。
- **同じ穴の別の形(試していない)**: 記録の中の `test_*.hy` や `defadr_*.hy` も同じ行で外れると思われます。`defadr_*.hy` については doeff-adr の配線の確かめが既定の warn で警告を出しうるようですが、日次での振る舞いは測っていません。
- **補足**: scratch の基点の commit は `-c user.name=blind` で作りました(/tmp の中だけで、push していません)。その後 hook に止められたので、`git add` は使っていません。

ファイル:
- `/tmp/blind-9r40-B/repo/docs/design-checks/lt-7MEMQP0C1CY0NRM4N2ZED8K3TA/`
- `/tmp/blind-9r40-B/fix.diff`
- `/tmp/blind-9r40-B/scripts/where_is_it.py`
- `/tmp/blind-9r40-B/test-changed.log`
- `/tmp/blind-9r40-B/test-changed-2.log`

WAIT: work — 親の会話の次の依頼
