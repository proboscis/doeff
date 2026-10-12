<!-- 盲検 A の返答(agentId aaf4e3aa8fe6f337b)。Agent tool の返却文をそのまま保存した。harness が各行に付けた 2 文字の字下げだけ外している。 -->
## 失敗例: session の値を読む番の柵(保守の門)を足すと、M3 の規則が変わる

番が確実に外へ渡す効果について、`pass_through`(M3)は節の中身を全く数えません。ところが doeff-hy(M4)は、番の前に効果を出す文を置くことがあります。この柵を足すと、それらの効果が analyzer の答えから消えます。直すには M1・M2・M3・M5 の変更が要ります。主張は「なし」または「M3 は変わらない」としています。

以下はすべて e76b005 のソースを読んだ推論です。何も実行していないので、実測の値はありません。

### 1. 変更要求と、それが要る理由

運用の変更として「保守中の門」を足します。保守の間だけ、待ち(Nap)以外の効果を止める柵です。開け閉めは handler の session の値で切り替え、その値は初めて使う時に効果で読みます。handle.hy の docstring は「dead-process gate」に触れており、柵や門は実際に使われる形です。

この門は、M4 に今ある機能(`session val` と、class の判定と session の値を `and` で結んだ `:when`)だけで書けます。M4 の変更は要りません。

### 2. 関係する主張・前提と、予想に反して変わるもの

関係する主張は次のとおりです。

- **M3:** 「`passes_on` が True の効果は節で消さず、届いたまま外へ出す」「節には `handles`・`passes_on`・`emits` しか問わない」。隠す知識は「節の中身の読み方・番の綴り」。
- **M1:** `Clause.passes` は、節が「走らずに」効果を外へ渡す条件(program_effects.py 242-258)。
- **M4:** 「`(if (not 番) (yield (Pass effect k)) 本体)` を節の suite の直下に置く」。M4 は変更していない。
- **S5:** 複合の番の柵を足しても、変わる責務は「なし」。class で決まる部分で確実に渡る効果は外へ出る。

前提を守っているかについて:

- 番の if の形(suite の直下・本体は効果への yield 1 つ)は変えません。M4 も変えません。範囲外とされた「番の if の形の変更」には当たりません。
- S5 の文面は「欄の and」で、ここでは欄の代わりに session の値を使います。その値を読む必要は無く、S5 と同じく「不明」で扱います。表のどの行も、session の値を読む番を範囲外にはしていません。
- 前提の外に見えるのは、M4 の記述が実際の展開の一部を書き漏らしている点だけです。handle.hy 566-583 と 615-623 では、番が session の名を読む時、Get・初期化の式・Put(guard-prefix)を番の if の前に置きます。doeff-hy の test `test_val_var_lazy.py` 646-703 は、この「番の前に取り出す」を仕様として確かめています。

変わるもの(推論)は次の 4 つです。

- **M1:** `Clause` に「外へ渡す前に節が出す効果」を持たせる必要があります。今の契約(`passes_on` と `emits` だけ)では表せません。
- **M2:** `_read_clauses` は、番の if より前の文とそれ以後を分けて読む必要があります。今は分岐全体を 1 つの `region` で読んでいます。
- **M3:** `pass_through` の 510-511 行は `passes_on` が真なら `continue` するだけです。外へ渡す時も前の効果と unresolved を足すよう、行そのものを変える必要があります。
- **M5:** `Clause.__getstate__` に新しい欄を足し、pickle の前にその欄を読ませる必要があります。result_cache の依存の集め方(`reading_sources`)にも関わります。

### 3. どの知識がどこへ漏れているか

- **漏れた知識:** 「外へ渡す効果に対して、節は何も実行しない」という前提です。これは番の前に何を置くかという M4 の展開の並びの知識です。
  - M3 の `continue` と、M1 の docstring の "without running" がこの前提を持っています。
  - handler_effects.py の 16-21 行は "at the head of the clause"、`_guards_in` の docstring は「欄の束ねだけが前に来る」と書いています。
  - 実際の M4 は guard-prefix で、効果を出す文を番の前に置きます。
- **共同の不変条件:** 「番の前に効果を出す文が無い」は M1・M2・M3 の 3 つが当てにしています。それを保証する所も、明記した所もありません。番の if を suite のどこからでも拾う M2 の形の契約(557-576 行)は、この並びを確かめません。
  - M4 の柵の形の宣言 `_passed-effects-source`(663-690 行)は `(not (isinstance effect X))` の形だけを扱い、VM はその handler を丸ごと飛ばします。
  - この形の時だけ M3 の規則は正しく、この不変条件はたまたま成り立ちます。
- **意図した拡張との区別:** M1 と M5 の新しい欄は、公開契約の拡張です。M2 が前と後を分けて渡すのは配線で、並びを知る M2 の本来の責務です。この 2 つは欠陥ではありません。
- **欠陥の中心:** 「節の中身を知らない」はずの M3 が、M4 の並びに依存した判断を持っている点です。そのため、使い方が変わると M3 の行が変わります。
- **M2 だけで直せない理由:**
  - `passes=None` にして節に答えさせると、Nap が消えます(元の不具合が戻ります)。
  - 外へ渡す効果を `emits` に入れると、番が不明な Tick なども外へ出ます。これは受入条件 2 の「不明は答えるに畳む」に反します。
  - 届いた class ごとに「出す効果」を変えるには、M3 が class ごとに問う形に変える必要があります。

### 4. 最小の例と再現手順、観測すべき結果

この file は作っていません。案です。test_guarded_clauses.py と同じく `make_package` で読み込む想定です。

```python
# effects.py に足す
@dataclass(frozen=True)
class ReadFlag(EffectBase):
    name: str
```
```hy
(import {pkg}.effects [Tick Nap Zap ReadFlag])
;; 保守中(closed)は Nap 以外を止める。closed は初めて使う時に ReadFlag で読む
(defhandler maintenance-gate
  (session val closed (! (ReadFlag "maintenance")))
  (EffectBase []
    :when (and closed (not (isinstance effect Nap)))
    (raise (ValueError "closed for maintenance"))))
(defk nap-only [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) 1)
(defk through-gate [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [maintenance-gate] (nap-only)))
  r)
```

読みの道筋(推論):

1. 番の if の test は `not (closed and not isinstance(effect, Nap))` です。
2. `_pass_test` はこれを `NotTest(AllTest((UnknownTest(), NotTest(ClassTest((Nap,))))))` にします。
3. Nap について評価すると `AllTest` が False になり、全体は True です。よって `passes_on(Nap)` は True です。
4. 番の形が柵の形ではないので、`declare-passes` は付きません。VM は Nap に対しても handler に入り、guard-prefix を走らせます。

手順(実行していません):

1. 別の作業木で、上を足した test を `uv run pytest packages/doeff-effect-analyzer/tests/python -k gate -q` で走らせます。
2. `_left(pkg, "through_gate")` の値を見ます。
3. `check_coverage(analyze_program(".../programs:through_gate"), [Nap だけを答える handler])` の `complete` を見ます。
4. 実際の動きと比べます。状態の handler と、届いた効果を記録する外の handler の下で `through_gate` を走らせます。
5. 比較として、analyzer を 6533804cf に戻して同じ入力を読みます。

観測すべき結果(予想):

| 何を見るか | 予想 |
|---|---|
| 今の版の analyzer の答え | `{"Nap"}` だけ |
| 実際に門の外へ出る効果 | Get・ReadFlag・Put・Nap(初回) |
| Nap だけを答える env での `coverage.complete` | True。ReadFlag と Get の穴が隠れる(危ない方向) |
| 変更前(6533804cf)の答え | `{"Get", "ReadFlag", "Put"}`。Nap が抜ける |

変更前は Nap が抜け、今は ReadFlag・Get・Put が抜けます。この入力では、今回の変更は穴を塞がず位置を移しただけです。また、外へ渡す時は前の文の unresolved も捨てられるので、`complete` は実際より強く出ます。

### 5. 未確認の前提・足りない情報

- **すべて推論で、実測はありません:** pytest も Hy の展開も走らせていません。上の値はすべて読みからの予想です。
- **展開の形:** guard-prefix が分岐の suite の直下で番の if より前に並ぶことは、`(do ~@bindings ~@guard-prefix (if ...))` から推論しました。Hy が実際に出す AST(一時変数の代入の形など)は見ていません。
- **session の値を持つ handler を analyzer が読めるか:** analyzer の test に session の値を持つ handler は 1 つも無く、ほかの所で unread や unresolved にならないかは確かめていません。もし unread になれば結果は「不完全」と出て、穴は隠れません。
- **実際の利用:** agora-controllers に、session の値を読む柵や門があるかは調べていません(全数検査はしない指示のため)。番つき節 170 件の内訳も分かりません。
- **実行時の動き:** 番の前に初期化が走ることは、doeff-hy の test(`test_val_var_lazy.py` 646-703)と展開の source で裏づけました。今回の門そのものは走らせていません。
- **参照の範囲:** blind-input.md と impl-change.patch 以外の `.design-check` の file は読んでいません。
- **確信の低い別の候補:** S2 の例 `type(effect) is X` は、`arrived` が「その class ちょうど」か「上限の class」かで答えが変わります。`ClassTest` の上位の class で不明を返す分岐は上限の読みで書かれており、正しく扱うには M3 の Escape に「ちょうどか」を運ぶ必要が出る可能性があります。こちらは詰めていません。

WAIT: work — 呼び出し元の次の指示
