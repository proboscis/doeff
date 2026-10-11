## 結果

検査を通るのに責務を侵す実装を 1 つ作り、実際に走らせて確かめました。ex07 の pipeline(M1)に「失敗の理由の内訳」と「後で取り直す銘柄」を足す変更です。M1 が backend の誤りの文面を読んで分け、失敗の方針(どれを取り直すか)まで決めます。3 つの検査はどれもこれを止めず(実測)、M2 だけを変えると M1 の答えが黙って変わります(実測)。

### 1. 機能の要求と差分

**要求**: ex07 の書き直しで、元の proboscis-ema の PipelineResult にあった失敗の理由ごとの内訳(symbol_not_found / market_closed)と、市場が開いたら取り直す銘柄の一覧が無くなった。結果の dict と表示に戻してほしい。ex07 の頭の註も、元の物に「Manual error classification (symbol_not_found, market_closed, etc.)」があったと書いています。

**差分**: `ex07_proboscis_ema_rewrite.hy` に足すのは次の 3 か所です(全体は `/tmp/ki043e-blind-b/variants/ex07.A-fail-reasons.hy`)。
```hy
(defn failure-reason [err]
  "Classify one failed item's exception into the original PipelineResult reason key."
  (setv msg (str err))
  (cond
    (.startswith msg "symbol not found") "symbol_not_found"
    (.startswith msg "market closed") "market_closed"
    True "other"))

(defn failure-breakdown [report events]
  "Count failed items per reason, and list the symbols to fetch again once the
   market opens (market_closed is temporary; the other reasons are permanent)."
  (setv reasons {} retry-later [])
  (for [item report]
    (when item.failed
      (setv reason (failure-reason item.value))
      (setv (get reasons reason) (+ (.get reasons reason 0) 1))
      (when (= reason "market_closed")
        (.append retry-later (get events item.index)))))
  #(reasons retry-later))

;; news-pipeline の (<- report (Inspect paired)) の後
  (setv #(fail-reasons retry-later) (failure-breakdown report events))
  {... "fail_reasons" fail-reasons "retry_later" retry-later "report" report}

;; 走らせる所(sequential と parallel の両方)
(print "Reasons:" (get out "fail_reasons"))
(print "Retry later:" (get out "retry_later"))
```

### 2. 本来の持ち主と、侵す契約

- **移る知識**: backend が失敗をどう伝えるか(誤りの文面 `"symbol not found: "`・`"market closed for: "`)は M2 の mock-backend だけが持つ知識です。表の M1 は「backend」を隠す知識にしています。差分の後は、同じ事実が 2 か所に在ります。出す側は mock-backend の 193・199 行、読む側は M1 の `failure-reason` の 101・102 行です。
- **移る判断**: 「market_closed は一時的なので取り直す」は失敗の方針です。S1 は retry を M2 の handler に置いており、M1 の不変条件は「失敗の扱いを書かない」です。この判断が M1 に入ります。
- **崩れる主張**:
  - S3: mock-backend を変えても変わるのは M2 だけ、が崩れます。M1 も一緒に直さないと、答えが黙って間違います。
  - S1: 失敗の方針を替えても M1 は変わらない、も崩れます。M1 の「取り直す」の答えが、どの handler が Fail を例外にしたかに左右されるようになります。
- **M3 は侵していません**: 失敗の件を読むのは公開の `Inspect` 経由です(Inspect の docstring は「post-hoc analysis」を用途としています)。

### 3. 各検査が止めない理由(macros.hy と test を読んだ事と、実測)

- **handler の形の defk の拒否**(`_reject-handler-signature`):
  - 呼ばれるのは defk の展開だけです(macros.hy:999)。引数名に effect・eff・k が在るかしか見ません。
  - 変えた defk は `news-pipeline [events]` だけで、引数名はそのままです。足した関数は defn なので、この検査の外です。
- **defk の契約の必須と型の確かめ**:
  - 呼ばれるのは deff と defk だけです(macros.hy:960-961、1040-1041)。
  - `news-pipeline` の `{:pre [(: events list)] :post [(: % dict)]}` はそのままで、答えも dict のままです。型は外側だけを見るので、足した key の中身は確かめません。
  - 1 点注意があります。件の中で契約が落ちても、sequential・parallel がそれを失敗の件として吸い込むので rc は 0 のままです(C で実測。下の 4 を参照)。
- **例が走る**(`test_examples_run.py`): `returncode == 0` しか見ず、出力の値は見ません。

### 4. 確かめた手順と結果(全部実測)

実験の file は全部 `/tmp/ki043e-blind-b/` の下です。

| file | 中身 |
|---|---|
| `variants/ex07.orig.hy` | 変更前の写し |
| `variants/ex07.A-fail-reasons.hy` | 上の差分を当てた物 |
| `variants/ex07.B-backend-msg.hy` | A から mock-backend の文面 2 行だけを変えた物(`"unknown symbol: "`・`"exchange closed: "`)= M2 だけの変更(S3) |
| `variants/ex07.C-normalize-none.hy` | A から sequential の stack に `normalize_to_none` を足した物 = M2 だけの変更(S1。ただし置き換えの値 None は契約の型に合わない) |
| `probe-C-seq-errors.hy` | C で失敗の件が持つ例外を表示する補助 |

**(a) 例を走らせる**
- 命令: `cd …/packages/doeff-traverse && uv run python -m hy <file>`
- 出力は `run-*.out` に残しています。

| 版 | rc | sequential の Reasons / Retry later | parallel(3) |
|---|---|---|---|
| orig | 0 | (表示なし)Success 3 / Failed 2 | 同じ |
| A | 0 | `{'symbol_not_found': 1, 'market_closed': 1}` / `['CLOSED']` | 同じ |
| B | 0 | `{'other': 2}` / `[]` | 同じ |
| C | 0 | `{'other': 2}` / `[]` | `{'symbol_not_found': 1, 'market_closed': 1}` / `['CLOSED']` |

- B は、M2 を 2 行変えただけで M1 の答えが間違います。それでも rc は 0 です。
- C は、同じ Program でも stack によって「取り直す」の答えが変わります。probe で見ると、sequential で失敗した件の値は `AssertionError('fetch-price: post-condition type error: … got NoneType')` でした。契約が落ちて、それが件の失敗として吸い込まれています。

**(b) 検を走らせる**
- 共有の作業木は変えられないので、`git archive HEAD` で写した `/tmp/ki043e-blind-b/tree` を使いました。
- test・pyproject.toml・conftest.py は `cmp` で作業木と同じバイト列、examples も `diff -rq` で同じである事を確かめました。そのうえで ex07 だけを差し替えました。
- 命令: `cd /tmp/ki043e-blind-b/tree && uv run --project /home/ubuntu/.worktrees/doeff-wt-ki-043e-dc --no-sync pytest packages/doeff-traverse/tests/test_examples_run.py -q -rA`
- 結果は orig・A・B・C のどれも rc 0、`12 passed`、`test_example_runs[ex07_proboscis_ema_rewrite.hy]` が PASSED でした(`pytest-*.out`)。
- 写しの conftest の印字(deadline scale)が出ているので、写しの側の設定が読まれています。
- 比べるため、作業木そのままでも検を走らせました。こちらも `12 passed` です。
- 最後に、作業木の `git status --short` が 0 行である事と、tree の ex07 を元へ戻した事を確かめました。

### 5. 確かめていない前提と、足りない情報

- **検は写しの木で走らせました**: 作業木の examples/ を書き換えた形では走らせていません(禁止のため)。違うのは rootdir の path(`/tmp/...`)だけで、interpreter と venv は作業木の `.venv` を使っています。rootdir の違いが rc の判定に効かない事は推測です。
- **未提示の検査は走らせていません**: doeff-hy-check・doeff-linter・semgrep・pyright・他の test・日次の全体検証がこの差分を止めるかどうかは言いません。事実として言えるのは、元の ex07 も defk の本体で setv を使っている(129 行)事だけです。
- **持ち主の判断は私の解釈です**: 失敗の理由の分類を本来どこに置くべきか(FetchPrice の公開契約として型つきの例外や reason の欄を宣言する、または M2 の strategy)は、共通の材料に書かれていません。表の「M1 は失敗の方針・backend を隠す」から読み取った解釈です。
- **C は S1 の前提の外です**: None は fetch-price の契約の型に合いません。なので C は補助の材料で、主な証拠は B です。
- **本物の backend は試していません**: 本物の backend で文面や例外の型が違う事は仮定で、試していません。parallel(3) の結果が sequential と同じだったのは、1 回走らせて見た事です。

WAIT: work — 依頼元の次の依頼
