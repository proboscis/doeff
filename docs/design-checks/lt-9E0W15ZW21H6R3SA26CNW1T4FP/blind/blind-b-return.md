## 盲検 B2: 提示された検査を通るのに、組の上限の値の一部が送り手へ戻る実装

組の同時数の上限の値を、送り手がもう一度持てるようにしてしまう実装を 1 つ作りました。提示された pytest と semgrep は実測で通りました。doeff-linter は変更の前も後も同じ 1 行で赤のままで、この差分による増加はありません。code-quality は 3 組に分けると通りましたが、13 file を一度に渡すと 60 秒の上限で incomplete です。責務の違反は、自分で足した検 4 本で振る舞いとして確かめました。

### 1. 機能要求と差分

**要求(現実にありそうな形)**: 「重い task(大きなモデルの読み込みなど)は、1 本で機体の枠を 2 本分使う。組の宣言は Airflow の pool の形なので、Airflow の `pool_slots` と同じく、task が組の枠を何本分使うか(`slots`)を名乗れるようにする。既定は 1 で、今の送り手は変わらない。」

**差分**: 13 file・+70/−24 行。全文は `/home/ubuntu/.worktrees/doeff-wt-blind-b2-ki-b0e8/blind-artifacts/slots-implementation.patch` で、木には当ててあります。`priority` と同じ運び方で、組の名乗りの兄弟の欄 `slots` を足しました。`TaskLimit` の型と `TASK-LIMIT-KEYS` には触れていません。

- **送り手の側**
  - `SubmitDetached.slots`(既定 1)を足した。
  - `submit-detached-task :slots` を足し、`task-slots-refusal`(1 以上の整数)で検める。
  - `task-placing-json` が本文の欄 `"slots"` を綴る。
  - 本番の handler(`detached.hy`)と sim の宿(`local.hy`)も同じ欄を運ぶ。
- **coordinator の側**
  - `TaskBody.slots` と `TaskRecord.slots` を足した。保存の読みは、欄が無ければ 1。
  - 受け口 2 か所に、`task-slots-refusal` と新しい `slots-over-declared-refusal`(slots が宣言の値を越えると 400)を足した。
  - 置き方の判断を次のように変えた(本体はこの 2 か所):

```hy
;; hold-limits
(:= after (| after {slot (+ (.get after slot 0) task.slots)}))
;; task-misfit(以前は (>= held limit))
(when (> (+ (.get held slot 0) task.slots) declared.limit) (:= full True))
```

### 2. 本来の持ち主と、侵す契約

- **持ち主**: 組の値と範囲は group-declaration(`System.task-limits` / `GroupLimit`)だけが持ちます。比べる値は宣言の limit で、送り手(sender)が名乗るのは `group` と `part` だけです。
- **移る知識と判断**: 「その組の task が同時に何本走るか」は、実質 宣言の値 ÷ slots になります。その分母を、送り手(とその設定・環境)が持つようになります。`task-misfit` の「満ちているか」の判断も、送り手の数 `task.slots` に依るようになります。
- **侵す契約**:
  - §3「値と範囲の持ち主は group-declaration だけ…送り手は値を持てない」と R2。文字どおりの強制(型に欄が無い・欄の集合が閉じている)は無傷で、値は兄弟の欄から入ります。
  - §1 の失敗ケース「同じ本数でも置いた順で同時数が変わる」が戻ります(実測は 4 を参照)。
  - S1 の主張「送り手が増えても、宣言の値 1 つで数えられる」が崩れます。
  - S2 の主張「値の変更は宣言の literal だけ」が崩れます。宣言を上げても、slots を送る送り手の同時数は半分のままです。
  - R4 と state-view: `/state` の held の `count`(置いている数)の意味が、黙って「枠の和」に変わります。
- **断っておくこと**: slots は 1 以上なので、置かれる task の本数が宣言の値を越えることはありません。違反は「越える」ことではなく、「効く値が送り手と送った順で割れる」ことです。

### 3. 各検査が拒まない理由

- **pytest(提示の 3 file)**
  - 理由: どの検も `slots` を送りません。既定 1 では置き方が前と同じ式になります。値を送る本文を断る検は、limits の object の中の `limit` / `scope` の名しか見ていません。保存の旧い行の判定(`old-limits-reason`)も同じ 2 つの名だけを見ます。
  - 実測: **24 passed**(`10-after-pytest-presented.txt`)。変更の前は初回が Hy の組み立ての期限超過で 6 errors でしたが、これは環境の遅さによるもので、再実行で 24 passed でした(`00`・`01`)。
- **semgrep**
  - 理由: この変更専用の規則はありません。足した語(slots)は業務の語ではなく、層を越える import も足していません。
  - 実測: 38 規則 × 13 file で 0 findings・exit 0(`20-semgrep.txt`)。
- **doeff-linter(包みの script)**
  - 理由: 新しい定義は既存と同じ書き方(deff / defk の :pre・:post・:tags)です。
  - 実測: 変更の前も後も **exit 1 で、出力は同じ 1 行** — `DOEFF172 cluster_policy.hy 基点 31 → 今 32`。checks の文書の注にある、変更前からの越えです(`30`・`31`)。
  - linter の binary を直に回しても、前後とも 70 warning・0 error で、警告の鍵の集合の diff は空でした(`32`〜`36`)。つまり「この差分で増えた物は無い」までが実測で、包みの命令は緑ではありません。
- **code-quality の module の契約**
  - 理由: 足した import は 2 種だけで、どちらも allow の中です。
    - cluster_policy と detached_policy が detached_rules から `task-slots-refusal` を読む。
    - detached_policy が cluster_policy から `slots-over-declared-refusal` を読む。
  - 実測: fast の段に 13 file を一度に渡すと、60 秒の上限を越えて **incomplete**(検査は未実行)でした(`40`)。2・9・2 file の 3 組に分けると、どれも `status passed`・findings は空でした(`41`〜`43`)。

### 4. 検査通過と違反を同じ設定で確かめる手順

木の根(`/home/ubuntu/.worktrees/doeff-wt-blind-b2-ki-b0e8`)で、差分を当てた状態のまま実行します。

1. 提示の pytest の命令 → 24 passed(実測)。
2. `uv run pytest packages/doeff-cluster/tests/test_blind_b2_slots_owner.hy -q` → **4 passed**(実測・`12-…txt`)。各 assert が違反そのものを主張しています。
   - 宣言 heavy = worker の範囲・値 2 の 1 つで、次の 2 つの本文を送る:
     - `{"limits":[{"group":"heavy","limit":1}]}` → 400
     - `{"limits":[{"group":"heavy"}],"slots":2}` → **200**
   - 同じ 3 本(A・A・B、B は slots 2)を送る:
     - A・A・B の順 → **2 本**置かれる
     - B・A・A の順 → **1 本**しか置かれない
   - 宣言を 2 → 4 に上げると、A の送り手は 4 本、B の送り手は **2 本**しか置かれない。B を 2 本置いた状態で、`/state` の held の count は **4**。
   - sim(本物の coordinator と worker): `submit-detached-task :slots 2` の b-1 が置かれ、slots なしの a-1 は宣言の値 2 なのに queued のまま待つ。groupLimits は `held [{"worker":"w1","count":2}]・queued 1`。

### 5. 未確認の前提と不足する情報

- **型の検査**: pyright と Hy の型の検査は提示されていないので回していません。`.pyi` は手で直しましたが、通るかは不明です。
- **全数の検**: 回していません。SubmitDetached の形や保存の JSON に触れる他の検が壊れるかは不明です。
- **`/state` の task の行**: task ごとの行に slots が出ないこと(運用者が分母を見られないこと)は推測で、replies は確かめていません。
- **送り手の設定**: 上に載る送り手が slots を環境変数などで持つ形は推測です。doeff の差分が足したのは口だけです。
- **commit の hook での code-quality**: hook は file を一度に渡すので、この機体の負荷(x3.4 前後)では incomplete になり得ます。これは環境に依ることで、検査の結論ではありません。
- **code-quality の起動の仕方**: 1 回目だけ、共有の checkout の venv の binary を直に呼びました。残りの 2 回は `uv run --no-sync --project` 経由です。
- **設計の判断**: 「task ごとの重さは正当な拡張だ」と設計の持ち主が判断する余地はあります。違反とした根拠は、§1・§3・S1・S2 の文言と、上の実測した振る舞いです。

共有のソースは変えていません。commit・push・他の会話への連絡もしていません。

ファイルはすべて `/home/ubuntu/.worktrees/doeff-wt-blind-b2-ki-b0e8/blind-artifacts/` に置きました。
- 差分: `slots-implementation.patch`
- 検の写し: `test_blind_b2_slots_owner.hy`(本体は `packages/doeff-cluster/tests/test_blind_b2_slots_owner.hy`)
- 走らせた命令の一覧: `commands.txt`
- 出力: `00`〜`43` の各 file
