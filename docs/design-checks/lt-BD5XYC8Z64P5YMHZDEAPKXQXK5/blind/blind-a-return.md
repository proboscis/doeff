## 盲検 a の報告: for/do に From を重ねた時の責務分離の失敗例

失敗例は 1 つ見つかりました。S1(Traverse を答える handler を 1 つ足す)の軸で、「for/do 全体の同時の件を N 以下に抑える handler」を P1〜P3 を守って素直に書くと、From が 1 つなら正しく、From を 2 つ重ねると scheduler が止まります。直すには、handler が重ねを知る必要がありました。

### 1. 変更要求と理由

- **要求:** 重ねた From の for/do を、外の API(LLM など)の同時呼び出しの上限 N の下で走らせる。for/do 全体で、同時に走る件の program を N 以下にする。
- **理由:**
  - for/do は「handler で実行の方針を差し込む batch 処理」で、件の中で外の API を呼ぶのが典型です。API の上限は呼び出し全体に掛かります。
  - 仕様は重ねた From を平らな内包(SQL の CROSS JOIN)と読むので、書き手は「16 組を N 並列で」と期待します。
  - S1 自身の例(件をまとめて外の API へ投げる batch の handler)と同じ種類の運用の変更です。
- **作った handler:** `parallel` の Traverse の枝を写し、違いは 1 点だけです。枠(semaphore)を Traverse ごとに作らず、handler 1 つにつき 1 つ作って全ての Traverse で使い回します。
  ```python
  if "sem" not in shared:   # parallel は Traverse ごとに CreateSemaphore する
      shared["sem"] = yield CreateSemaphore(limit)
  ```

### 2. 関係する主張・前提と、予想に反して変わる物

- **前提は守っています。**
  - P1: Collection で答えます。
  - P2: 件の program を、内の handler と自分を戻した下で走らせます(`parallel` と同じ写し)。
  - P3: 書き手の側は変えていません。for/do の式を直に run する形は、既存の `tests/test_for_do_runtime.py` と同じです。
  - 契約の適用範囲の中の例です。
- **S1 の主張:** 新しい handler は M3 の契約だけを守れば重ねた for/do も正しく答え、handler は重ねを知らない。予想の範囲は「変わるのは新しい handler だけ」。
  - 実測では、同じ handler が From 1 つでは正しく、From 2 つで `SchedulerDeadlockError` になりました。
  - 直すには、handler が「この Traverse は自分の件の program の中から来た(重ね)」を知り、外の件の枠を内の件を待つ間だけ返す必要がありました(実験 3)。
  - 変わるファイルは新しい handler の 1 つで済みます。崩れるのは「handler は重ねを知らない」という知識の主張の方です。
  - 重ねを知らせない道を選ぶと、M1(展開の形)と M2(flatten の継ぎ方)が変わる見込みです。これは推測で、試していません。
- **S3 の主張:** 並列の度合いを変えても M1・M2 は変わらない。
  - 文字どおりには成り立ちます。
  - ただし既存の `parallel(N)` の N は Traverse 1 つごとの枠なので、葉の件の同時の数は N の From の数乗まで増えました(実測)。
  - つまり M3 の「並列の度合い」の意味が、M1 の展開の形(From の数)に依ります。

### 3. どの知識がどこへ漏れるか

- **漏れる元(M1 の判断):** 内の From の Traverse を、外の件の関数の中で実行します。展開を実測で確かめました。
  ```
  ((_doeff_do (fn [g] (.flatten (yield g))))
   (_doeff_traverse_Traverse (_doeff_do (fn [a] (do (yield (_doeff_traverse_Traverse (_doeff_do (fn [b] (do (+ a b)))) [10 20]))))) [1 2]))
  ```
  このため、外の件の program が走っている最中に、同じ handler へ内の Traverse がもう一度入ってきます。
- **漏れる先(M3):** handler は「件の program を走らせている間に、同じ handler へ別の Traverse が来る。外の件が持つ資源(枠・batch の席など)を持ち続けると、内の件がそれを待って止まる」を知らねばなりません。
  - Traverse の effect `Traverse(f, items, label)` には親の件を示す欄がありません。そのため handler は、件を包む時に「件の中」という印を自分で運ぶ必要があります(実験 3 の `make(True)`)。
- **区別:**
  - 公開契約の意図した拡張ではありません。Traverse・Collection・handler の公開面は変えていません。
  - 配線の変更でもありません。handler の差し替えだけでは直りませんでした。
  - 中身は M1 と M3 の共同の不変条件(一度出た handler へ同じ処理の中から再び入る形)で、M3 の契約(P1・P2)には書かれていません。
  - この義務自体は直す前からありました。書き手が for/do を本体の `<-` で重ねれば同じく止まります(実験 4 で実測)。
  - 新しいのは、From を 2 つ書いた 1 つの for/do が、書き手から見えない所でこの形を作る事です。S1 が「handler は重ねを知らない」と書いている点で、M1 の置き分けの知識が M3 へ漏れています。

### 4. 最小の再現の手順と観測(実測)

実験の file は `/tmp/blind-a/` にあります。`common.py`、`exp0_expand.hy`、`exp1_parallel_degree.py`、`exp2_global_limit.py`、`exp2b_deadlock_map.py`、`exp3_nesting_aware.py`、`exp4_explicit_nesting.py` です。

`probe` は葉の件の代わりです。同時に走っている数を数え、`Spawn` と `Wait` で scheduler へ 4 回手を返します。命令はすべて `cd /home/ubuntu/.worktrees/doeff-at-6ee49cc2-blind && uv run --no-sync python /tmp/blind-a/<file>` の形で走らせました。

**環境の確認:** `pytest -q tests/test_for_do_runtime.py` は 7 passed でした。

**実験 2(最小の入力):** `(for/do (<- a (From [1 2 3])) (<- b (From [10 20])) (<- r (probe a b)) r)` を `scheduled(globally_limited(2)(…))` の下で走らせました。期待は `[11, 21, 12, 22, 13, 23]` です。

| 枠 N | 入力 | 結果 |
|---|---|---|
| 2 | From 1 つ・6 件 | `valid_values=[0, 1, 2, 3, 4, 5] max_active_leaf=2` |
| 2 | From 1 つ・同じ 6 組 | `valid_values=[11, 21, 12, 22, 13, 23] max_active_leaf=2` |
| 2 | From 2 つ(3×2) | `SchedulerDeadlockError: scheduler deadlock: semaphore waiters remain with no runnable tasks (semaphore 0: tasks 5, 6, 10, 11)`、葉は 1 件しか始まらない |
| 3・4 | From 2 つ(3×2) | 値は正しいが `max_active_leaf=2`(外の件が枠を占め、枠より少ない数しか走らない) |

**実験 2b(止まる境):** 外の件 m=6 では N=1〜4 で止まり、N=6・8 は通りました。m=2〜4 では N=1・2 で止まり、N=3 以上は通りました。

**実験 3(重ねを知る handler):** m=2・3・6、N=1〜4 の全ての組で値が正しく、葉の同時の数は N 以下でした。

**実験 4:** From 2 つの for/do も、本体の `<-` で重ねた for/do も、N=2 で止まりました。

**実験 1(既存の `parallel`):** 葉の件の同時の数の最大です。

| 入力 | concurrency=2 | concurrency=3 |
|---|---|---|
| 組の list に From 1 つ(16 件) | 2 | 3 |
| From 2 つ(4×4) | 4 | 9 |
| From 3 つ(3×3×3) | 8 | 22 |

### 5. 未確認の前提・不足する情報(推測と実測の区別)

- 同時の数は scheduler の順に依る下限です(実測の条件)。`probe` が手を返すのを 1 回にすると、From 1 つでも最大 1 しか出ませんでした(実測)。
- `DOEFF_SCHEDULER` は未設定のまま走らせました。既定が Python と Rust のどちらの scheduler かは確かめていません。
- 止まるかどうかは件の進み方の順に依ります(実験 2b の表)。どの組で止まるかの一般の境は出していません。
- 枠を handler 全体で 1 つにする形が実際の利用者の要求かは確かめていません。S1 の batch の handler でも、席を外の件が持ったまま内の件を待つ同じ形になる、というのは推測です(試していません)。
- M1 を「外の From で束縛と内の items を作る段」と「全ての組を 1 つの Traverse で走らせる段」に分ける代わりの道は試していません。その場合に M1 と M2 が変わるというのも推測です。
- Traverse の docstring に「handler は件の program の周りへ自分を戻す」とあり、同じ handler へ再び入る形は半ば書かれています。ただし、資源を持ったまま待つ危険は書かれていない、と読みました。これは読んだ範囲での判断です。
- 指示に従い、`docs/design-checks/` の他の file は読んでいません。M3 の契約がそちらでどう書かれているかは分かりません。
- 型検査のための展開と、`sequential`・`parallel_fail_fast` での振る舞いは測っていません。

WAIT: work — 呼び出し元の次の依頼
