## 結論

主張 S2 は「新しい消費者は handler を 1 つ・stack に 1 要素足すだけでよい」と言っています。実際には、公開の契約に書かれていない共同の不変条件に頼っています。条件は「`ItemOutcome` の受け手は知らせを外へ渡す(Pass / reperform)。受けて止めてよい受け手は 1 つだけで、受け手の中で一番外に置く」です。

同梱の `progress_bars` 自身がこの条件を守っていません(`Resume(k, None)` で答えて止める)。そのため、S2 に設計者が挙げた例そのもの(段ごとに失敗した件と理由を記録する handler)を ex08 の stack に足すと、並び次第で次のどちらかになります。

- 既存の bar が 1 本も開かなくなる。
- 新しい記録係が何も受けない。

どちらの場合もエラーも警告も出ず、Traverse の答え(Collection)も変わりません。

## 1. 変更要求と、それが必要になる理由

- **要求:** ex08 / README の形 `[... (progress-bars tqdm) (parallel 10)]` で bar を出している pipeline に、「段ごとに失敗した件と理由を記録する handler」を足し、bar も残す。
- **理由:** bar は件の数しか出さず、どの件がなぜ落ちたかは残りません。この変更の動機は「失敗する件のある pipeline」なので、件 7 が落ちたら原因を残したい、という要求が自然に続きます。bar と記録係が同時に stack に居るのは普通の運用です。
- 同じ類の要求として「tqdm に加えて WebUI にも進捗を出す」(`progress_bars` を 2 つ置く)も確かめました。

## 2. 関係する主張・前提と、予想に反して変わる物

**関係する主張(S2):** 変わるのは M2 だけで、M1・M3・M4 は変わらない。前提は「消費者が注記で `ItemOutcome` を名指す」です。

**前提はすべて守っています(契約の適用範囲の中):**
- 記録係は `(effect: ItemOutcome, k)`、Hy では `defhandler` の節 `(ItemOutcome [...])` で名指しています。
- Traverse の handler の内側に置いています。
- Traverse の handler は sequential・parallel・parallel_fail_fast の 3 つです。
- M3 の規則(持ち越し・Skip・例外)は写していません。使うのは公開の `label`・`outcome`・`item.index`・`item.history[-1].detail` だけです。

**予想に反して変わる物:**
- 新しい記録係が正しく働くかどうかは、次の 2 つで決まります。
  - 既存の別の消費者 `progress_bars`(bar の表示という別の責務)が、内部で節を Resume で終えていること。
  - stack の並び。
- 「並び 2 通り × 節の終わり方 2 通り」のうち、働くのは「Pass で終える記録係を `progress_bars` の内側に置く」1 通りだけです。
- 並びに依らず働かせるには、次のどちらかが要ります。どちらも「1 要素足すだけ」を越え、失敗の記録のために bar の表示の module か、知らせの届け方(M3)を変えることになります。
  - (a) `progress_bars`(既存の M2)を「渡す」形に変え、M4 の契約(`ItemOutcome` の docstring・README)に「受け手は渡す」を足す。
  - (b) M3 の 3 つの handler が、名指す handler ごとに別々に知らせを届ける(broadcast)ように変える。

## 3. どの知識・判断がどこへ漏れるか(区別)

**漏れる知識:**
- (a) `progress_bars` が知らせを受けて止めるという内部の選択。docstring には「数えて total で閉じる」とあるだけで、外へ渡さないことは書かれていません。それでも、ほかの消費者を書く人と stack を組む人は、これを知らないと正しく置けません。
- (b) 「誰が知らせを受けるか」の判断。責務表では M3 の「知らせる時と場所」ですが、M3 が決めるのは「内側の handler の並びの下で 1 回出す」までです。実際に届く先は、各消費者の節の終わり方と stack の並びで決まります。M3 の判断が M2 と配線に割れて出ています。

**区別:**
- **公開契約の拡張ではない:** 必要な情報は今の `ItemOutcome` に全部在ります。働く形では `('first', 2, 'failed', 'three')` が取れました(実測)。
- **配線の変更だけでもない:** 並びを替えても、Resume で終える受け手が 2 つ居れば、どの並びでも片方が何も受けません(下の表の 2・3 行目と、`progress_bars` を 2 つ置いた行)。
- **契約に無い共同の不変条件:** 「受け手は渡す」という規則は、検の補助 `_Reports` の docstring(「外へ渡す(外の progress_bars にも届き…)」)にしか在りません。`ItemOutcome` の docstring・README・`progress_bars` の docstring・CHANGELOG には在りません(grep で確認)。既存の検は、この規則を守る 1 通りの並びしか見ていません。
- **M3 の規則の M2 への写しは起きていない:** S2 の副主張「M3 の規則を M2 へ写さない」は、この例では保たれています。
- **検出できない:** pyright と analyzer の検(`test_doeff_traverse_item_outcomes_stay_inside_the_handler`)は 3 つの Traverse の handler しか読みません。実行時も黙ったままです。

## 4. 最小の変更例・再現手順・観測(すべて実測)

最小の変更は、ex08 の stack に次の 1 要素を足すことです。

```hy
(defhandler failure-ledger [rows]
  (ItemOutcome [outcome item label]
    (when (= outcome "failed") (.append rows #(label item.index (str item.value))))
    (resume None)))
;; [try_handler flaky-backend fail_handler (failure-ledger rows) (tqdm-progress) (parallel 10)]
```

再現の手順:
```
cd /home/ubuntu/.worktrees/doeff-blind-3293bc4cc
uv run python /tmp/kie3a9-blind-a/exp_consumers.py
uv run python -m hy /tmp/kie3a9-blind-a/exp_ex08_ledger.hy
```

**Python の組み合わせ表:** 検と同じ 6 件・2 段の pipeline(件 3 が失敗、件 5 が Skip)。sequential と parallel(2) で結果は同じでした。

| stack(内側が先) | bar | 記録 |
|---|---|---|
| `[progress_bars]` | first 6/6・second 6/6、どちらも閉じる | — |
| `[ledger(resume), progress_bars]` | 1 本も開かない | 2 行 |
| `[progress_bars, ledger(resume)]` | 6/6 で閉じる ×2 | 空 |
| `[ledger(pass), progress_bars]` | 6/6 で閉じる ×2 | 2 行(働くのはこれだけ) |
| `[progress_bars, ledger(pass)]` | 6/6 で閉じる ×2 | 空 |
| `[progress_bars(tqdm), progress_bars(webui)]` | 1 本目は閉じる、2 本目は開かない | — |

- parallel_fail_fast(2) で Skip が 1 件、`[progress_bars, ledger(resume)]` の時: bar は 3/3 で閉じ、記録は空。
- **Hy(ex08 の形・20 件・件 7 が落ちる):** parallel(10) と sequential の両方で同じ結果でした。
  - 記録係を内側に足す: bars は `[]`(1 本も開かない)、記録は `[('stage1: compute', 6, 'backend down for 7')]`。
  - 外側に足す: bars は 20/20 で閉じる ×2、記録は `[]`。
  - 答えはすべての場合で valid 19・failed [6] と同じ。

**対照:**
- `progress_bars` を Pass で終える形にした写しでは、渡す記録係はどちらの並びでも両方が受けました。
- ただし、Resume で終える記録係を内側に置くと、bar はやはり開きません。規則は全部の受け手に要ります。
- Hy で `(reperform effect)` で終える記録係は、内側なら両方が受け、外側なら記録が空でした。

## 5. 未確認の前提・不足する情報(推測と実測の区別)

**実測:** 4. の内容はすべて実測です(版 3293bc4cc、作業木は変えておらず `git status` は空)。

**推測・未確認:**
- 設計者が「受け手は 1 つ」を意図していたかは分かりません。意図していたなら、S2 の前提に「足す消費者は既存の消費者の内側に置き、外へ渡す」が抜けていることになります。
- 本番(agora-controllers)の stack に今 `ItemOutcome` の受け手がいくつ在るかは調べていません(全数検査をしない指示のため)。変更が新しいので、今は 0〜1 つだろうと推測しています。
- 直し方 (b)(M3 の broadcast)は試していません。
- この規則を静的に強制するには、doeff の再開の宣言 `__doeff_resumption__`(REQUIRED / ABSENT_AS_ONLY / NEVER)に「渡すだけ」の種類が無いので、doeff-core-effects と doeff-hy の `clause_endings` の拡張が要りそうです(読んだだけの推測)。
- WithObserve の observer を受け手にする形には、知らせは届かないと読みました(`report_under` は handler だけで observer を含まない)。実験はしておらず、前提の外です。

**ほかに探った候補(どれも実験はしていません):**
- 入れ子の for/do で内側の Traverse ごとに bar が開く問題、記録を走りの間で比べる時に親子の関係が要る問題。どちらも親の情報を足す契約の拡張(M3 ×3 と M4)で、知識の漏洩ではありません。
- 件ごとの所要時間を取る。始まりの知らせが要る契約の拡張です。
- Traverse の handler の外に、全 job 共通の集計を置く。前提に反し、契約の範囲外です。
- sequential が知らせる時だけ長さの無い iterable を最後まで読む。文書化されています。M1 が読む順に頼っているなら M1 側の問題です。
- doeff-cluster の effect-recorder は全部の効果を受けるので、`ItemOutcome` を受けると記録が止まったり再生が食い違ったりする恐れがあります。ただし記録係は翻訳の handler より外(R5)なので、Traverse の handler が翻訳の側に在れば届きません。本番での並びは未確認です。
- len と反復の数が違う Sized(pandas の DataFrame など)では、sequential と parallel で total が食い違う。M1 の誤用寄りです。

**実験の file:**
- /tmp/kie3a9-blind-a/exp_consumers.py(出力 /tmp/kie3a9-blind-a/exp_consumers.out)
- /tmp/kie3a9-blind-a/exp_ex08_ledger.hy(出力 /tmp/kie3a9-blind-a/exp_ex08_ledger.out)

WAIT: work — 依頼元の次の指示
