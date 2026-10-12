## 結論

失敗例を 1 つ見つけ、sim で実測しました。きっかけの変更は「ある系が宣言した組を、別に宣言する系(別の defsystem)の送り手も名乗る」です。組 g の値をどの系が持つかを、どの module も記録していません。そのため declare-writer は「spec が違えば書き直す」と黙って決めます。結果、カードが直した不具合(送り手ごとに値が割れ、置く順で同時数が変わる)が、「宣言の順で同時数が変わる」形で戻ります。

## 1. 変更要求と、それが要る理由

- **変更要求:** 系 alpha が `:task-limits {"llm" {"scope" "cluster" "limit" 1}}` で宣言した組 llm を、別に宣言・配備する系 beta の job の送り手も名乗り、同じ外の資源の枠を共有したい。
- **理由:**
  - 組の目的は、複数の送り手が共有する外の資源の枠を 1 つの値で数えることです(S1・R5)。送り手が 1 つの defsystem に収まるとは限りません。
  - 宣言の道具は系ごとに宣言し、他の系の資源には触りません(`declare.hy:129`「一覧に無い Service には触らない」「系に無くなった組の資源は消さない」)。1 つの coordinator に複数の系が載る使い方は、今の作りの内にあります。
  - beta の書き手は、自分の sim(S5)と初回の配備で断られないように、組を自分の `:task-limits` にも写します。写さないと 400 で断られます(下の観測 4)。

## 2. 元の主張・前提と、予想に反して変わる物

**前提は守っています。**
- 送り手は PUT /detached(submit-detached-task)を通します。
- 宣言は apply-declaration で書きます(sim では、本番と同じ group-limit-write を通る Redeclare)。
- coordinator は 1 つです。
- §4 の前提が縛るのは coordinator の数だけで、系の数は縛っていません。

**崩れる主張:**
- **R1・§3「値と範囲の持ち主は宣言 1 つ」:** 資源 GroupLimit の鍵は組の名だけで、coordinator 全体で 1 つの名前空間です。一方、宣言の単位は系ごとです。持ち主を表す欄は、資源 GroupLimit にも System にもありません。
- **S1「送り手を足しても変わるのは送り手だけ」:** 宣言した系の外の送り手を足すと、値が 2 つになるか、他の系の宣言の順に依存します。
- **S2「値の変更は literal 1 つ」:** 写した数だけ literal があり、片方だけ宣言し直すと値が往復します。
- **S5「sim の検が本番の道を覆う」:** beta の sim は、alpha の値を写さないと beta の送り手を通せません。さらに、組だけの宣言は sim では書けるのに、本番の declare では落ちます(観測 5)。

**持ち主を 1 つに保ったまま支えるには、予想(送り手だけ)を越えて次が変わります。**

| 責務 | 変える理由(該当箇所) |
|---|---|
| group-declaration | 「持つ」と「使うだけ」を書き分けるか、組だけの宣言を許す必要がある。今の defsystem は job が 0 行だと断り(`system_form.hy:252-253`)、受ける鍵と欄を自前の閉じた集合で持つ(39-42 行) |
| limit-rules | 持ち主を spec に置くなら、閉じた集合 GROUP-LIMIT-KEYS と group-limit-of / group-limit-spec を変える |
| declare-writer | `group-limit-write`(`declaration_requests.hy:102-113`)は「spec が違えば読んだ版で書き直す」だけで、持ち主を比べる判断が無い。`apply-declaration` は `rows[0]` を無条件に読む(`declare.hy:138`)。同じ手順の写しが `sim/local.hy:2555-` にある |
| coordinator-resource | GroupLimit の作り・書き直し(`resource_policy.hy:873-875`・`910-914`)に、持ち主の記録と照合を足す。保存の state_json・durable_kv も変わる |
| state-view(任意) | /state で誰の値かを見せる |

## 3. どの知識がどこへ漏れるか

漏れる知識は「組 g の値をどの系が持つか」です。これを持つ module が無いので、判断が doeff の外(使い手の repo の書き手と、配備の手順)へ漏れます。

- **(a) beta が写す場合:**
  - alpha の値が beta の literal に複製され、持ち主が 2 つになります。
  - どちらが勝つかは、group-limit-write の「今の spec と違えば書き直す」が黙って決めます。
  - 版つきの書き(409)が止めるのは、読んでから書くまでの間に入った他人の書きだけです。直前に読むので常に最新の版を持ち、別の持ち主の値は止められません。
- **(b) beta が写さない場合:** beta の送り手が受け入れられるかが「alpha が先に宣言されていること」に依存します。その順の知識が、beta の配備手順と beta の sim に漏れます。

**4 つの区別:**
- **配線の変更:** beta の送り手が `TaskLimit(group="llm")` を名乗ること自体は S1 の想定どおりで、欠陥ではありません。
- **意図した公開契約の拡張:** 資源 GroupLimit に持ち主の欄を足す、defsystem に「使うだけ」の書き方を足す、という直しはこれに当たります。それで複数の module が変わるのは拡張として予想できるので、欠陥の主張はここに置いていません。
- **共同の不変条件:** 「各組を宣言する系は 1 つ(写すなら全部の値が一致)」を、どの module も強制していません。system-of は 1 つの系の中の重なりしか見ず(`service_build.hy:67`)、資源の口は形しか検めません。
- **隠すはずの知識の漏洩:** 上の (a)(b) です。契約を変えない今の版のままで起きます。

**付随の所見(コードを読んで確認・実行はしていない):** §3 は宣言の形の規則を「group-limit-refusal 1 つ」としています。しかし doeff-hy の `system_form.hy` が、欄の閉じた集合・範囲の名・1 以上・「/」の禁止を自前で持っています(39-42 行・146-165 行)。持ち主の欄を足す直しは、doeff-hy にも及びます。

## 4. 最小の再現と観測

最小の変更は、組の値だけが違う 2 つの系です。どちらも system-of を通り、誰も断りません。
```hy
(val ALPHA (system-of "alpha" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 1))))
(val BETA  (system-of "beta"  #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 3))))
```

**手順:**
1. sim-cluster で ALPHA を宣言する。
2. 筋書きの中で `(Redeclare BETA)` を出す。
3. 組 llm を名乗る 30 秒の task を 3 本送る。
4. 仮想の 5 秒後に GET /state を読む。
5. 宣言の順を逆(BETA → ALPHA)にして同じことをする。

命令(木の根で): `uv run pytest packages/doeff-cluster/tests/test_blind_a_shared_group.hy -s -q` → 5 passed。検は「失敗が起きること」を確かめる形で書いてあります。

**観測(実測):**

| # | 状況 | 結果 |
|---|---|---|
| 1 | alpha → beta の順 | llm は limit 3・置いている 3・待ち 0。alpha の宣言 1 を越えて 3 本が同時に走る |
| 2 | beta → alpha の順 | limit 1・置いている 1・待ち 2 |
| 3 | `group-limit-write`(alpha の spec を版 7 で読んだ後に beta の値を書く) | `{"resourceVersion": 7, "spec": {"scope": "cluster", "limit": 3}}`。断らずに書き直しの本文を返す |
| 4 | beta が組を写さず、beta だけで sim を回す | 400「limits の組 llm は系の宣言に無い …(宣言の在る組: [])」 |
| 5 | 持ち主を 1 つにする回避として、組だけの宣言を作る | 本番の `apply-declaration`(HTTP は代役で答えた)は GroupLimit を書いた後に `IndexError: list index out of range`。defsystem は展開の時に「job の行が 1 つも無い」で断る。sim の apply-declaration は rows が空でも通る(既存の検の SYSTEM も job 0 行で sim を通っている)ので、ここで sim と本番が分かれる |

観測 1 と 2 では、送る task も coordinator も同じで、宣言の順だけで同時数が 3 と 1 に分かれます。どの段も 4xx も警告も出しません。/state の groupLimits にも値と版が出るだけで、誰が書いたかは出ません。

## 5. 未確認の前提と不足

- **実測した物:** 観測 1〜5。sim は本物の coordinator の受け口・置き方と、本番と同じ group-limit-write を通っています。観測 5 の本番の apply-declaration は、HTTP を代役の handler で答えさせました。
- **推測・未確認:**
  - 上に載る系(agora)で、同じ組の送り手が 2 つ以上の系にまたがるかは確かめていません。agora-controllers は読んでよい範囲の外なので読んでいません。複数の系を同じ coordinator へ宣言する運用は、apply-declaration の註と、配備の頼みの書式に「宣言する系」の欄があることからの推測です。
  - 子 B が「組は 1 つの系にだけ書く」と決めるかは分かりません。そう決めても、(b) の順の依存と beta の sim の問題は残ります。
  - R1 の「系の宣言 1 つ」が「1 つの coordinator に載る系は 1 つ」の意味なら、この例は前提の外になります。ただ §4 の前提が縛るのは coordinator の数だけです。
  - 本物の cluster(実際の HTTP・永続化・再起動の後)では確かめていません。coordinator の出来事の記録(X-Actor)から後で書き手を追えるかも見ていません。
  - 全数の検は走らせていません。
- **読んだ範囲の逸脱:** 検の書き方を知るために、表に無い `tests/test_declare_apply.hy`(頭の 60 行)・`tests/detached_rig.hy`(頭)・`shared/intent/cluster_control.hy`(Redeclare の註)・`tests/test_retirement_notice.hy`(grep した 2 行)を読みました。設計の主張の判断には使っていません。`docs/design-checks` の下の他の file は読んでいません。

## 置いた物

- 足した検: `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-b0e8/packages/doeff-cluster/tests/test_blind_a_shared_group.hy`
- 記録: `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-b0e8/blind-artifacts/`
  - `experiment.diff`(実験の差分)と `commands.txt`(走らせた命令)
  - `run3-all.txt`:全体を通した出力(5 passed)
  - `run2-writer.txt`:観測 3 だけの再実行
  - `run1.txt`:1 本が赤。検の書き誤りで、defk の答えを `<-` で受けていなかったため
- 共有の checkout は変えていません。commit・push もしていません。

WAIT: work — 頼んだ側(親の会話)の次の依頼
