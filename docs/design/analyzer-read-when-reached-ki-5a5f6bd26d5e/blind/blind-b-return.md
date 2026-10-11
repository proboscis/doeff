私は Opus 5.5(model id: claude-opus-5-5)です。

# 失敗例: handler の読みの答えを process 全体で覚える

検は元の版と同じく 122 passed のまま通ります。ruff も新しい指摘を出しません。それでも、閉じの解析の答えが「同じ process で前に何を解析したか」で変わります。その誤った答えは答えの置き場に残り、別の process にも返ります。下の表と結果はすべて実測です。推測は 5 節でだけ、推測と書いて述べます。

## 1. 機能要求と差分

**要求(現実的な形):** 「閉じの検は、同じ土台の handler を、組ごと・scope ごとに読み直している(`_dispatch_of` の注記では 5 本の検で 924 回)。節の本体は effect が届いた時に読むようになった。それなら handler の読みの答え(`HandlerEffects`)を process の中で覚えれば、節の本体も process で 1 回だけ読めば済む。覚え方は `_DISPATCHES`・`_CLAUSE_FUNCTIONS`・`_BODY_FACTS` と同じ形(`id` と、同じ物かの確かめ)にする。」

**差分:** `handler_effects.py` の `_analyze_handler` だけを変えます。再帰の守り(`_READING` の確かめ)より後に置くので、handler が自分の節の中で自分を入れ直す再帰は今までどおり止まります。全文は `/tmp/ki5a5f-blind-B/variant.patch` にあります。

```diff
+_HANDLERS: dict[tuple[int, str, int], tuple[Any, HandlerEffects]] = {}
 ...
     if function in _READING.get():
         return _unread(...)
+    key = (id(obj), label, depth)
+    kept = _HANDLERS.get(key)
+    if kept is not None and kept[0] is obj:
+        return kept[1]
     token = _READING.set(_READING.get() | {function})
     try:
         read = _read_function(function, label, depth=depth)
     finally:
         _READING.reset(token)
-    return _decide(read, declared)
+    answer = _decide(read, declared)
+    _HANDLERS[key] = (obj, answer)
+    return answer
```

## 2. 本来の持ち主と、侵す契約

手本にした `_DISPATCHES` などの表は、AST だけで決まる値を覚えています(材料の S5 の主張もそう書いています)。`HandlerEffects` はそうではありません。中に次の物を持ちます。
- 作った時の `_READING` の写しと `_Reader` を握った、まだ読んでいない節。
- ある解析の `reading_sources` の中で読まれた、節の emits。

覚えることで、次の判断・状態が持ち主から離れます。

- **clause-reading の判断:** 持ち主は `analyze_handler` と、節を作った時の `_READING` です。寿命は「handler の読み 1 回」です。
  - 「この handler は自分の節の中で入れ直している → 読まない(unread)」という判断は、本来は今の解析の呼び出しの道で決まります。
  - 差分の後は、その process で最初にその handler を読んだ解析の写しが、後の全部の解析で使われます。判断の根拠が「今の解析の道」から「process の履歴」(`_HANDLERS` の表)へ移ります。
  - 侵す物: R1(答えを変えない)、S6(順に依らない)、公開の `check_coverage` の答え。
- **fact-reader の状態:** 寿命は「解析 1 回」です。節が握る `_Reader`(とその `_facts` / `_reports`)が、別々の解析で共有されるようになります(実測: 元の版は別の reader、変えた版は同じ reader)。S5 の「報告の覚えは reader ごとで、別の解析と混ざらない」が崩れます。
- **source-io の数え:** `reading_sources` は「disk・展開の木の置き場・この process の置き場のどこから読んでも数える」という公開契約を持ちます。覚えの表はその上に新しく置かれた process の置き場で、`_locate` / `_module_source` を通らないので数えません。R3 を侵します。
- **result-cache の不変条件:** 「答えは compute が読んだ物だけを持つ」と前提 P3 です。job の答えに、warm の解析で読まれた emits が入り込みます。

## 3. 各検査がこのコードを拒否しない理由

検は、元の版の写しも変えた版の写しも 122 passed でした。4 節で名の出た 5 本も、変えた版で 5 passed です。

| 検 | 拒否しない理由 |
|---|---|
| `test_a_clause_is_read_when_..._counted_where_it_is_read` | `make_package` は検ごとに新しい名(uuid)の package を作り、その中で handler を `reading_sources` の中で 1 回だけ読みます。通るのは覚えに当たらない道(元と同じ道)だけで、数えを飛ばす道は一度も通りません。`check_coverage` に渡すのは `handler.value` なので、どちらの版でも同じ物です |
| `test_a_pickled_handler_keeps_what_its_clauses_emit` | 解析は 1 回だけで、覚えは関わりません |
| `test_residual_through_an_env_and_recursion_through_a_scope` | 再帰は inline の `handle`(`_inline_handler`)を通るので、覚えの対象外です。`_READING` の確かめは覚えを引くより前にあります。同じ handler を違う `_READING` の下で読む場面(答えが分かれる条件)がありません |
| 覚えの使い回しの検 2 本(`…walked_again`・`…the_report_walked_before`) | 見ているのは Program を読む reader の `_KeptReport` です。`two_clauses` は同じ解析の中で覚えに当たりますが、読む時の `_READING` はどれも空なので答えは同じで、検も handler を見ていません |
| ruff | 元も変えた版も B009 の 1 件だけで、元の file から既にある指摘です(行が 257 から 271 へずれただけ)。終了 code はどちらも 1 です。「ruff が通る」とは言えず、言えるのは「変更が新しい指摘を足さない」ことまでです |
| R1 の digest の比較(下流の 19 組) | 撃っていません(repo の外の手で撃つ検で、ここでは撃てません)。捕まえるかどうかは分かりません |

## 4. 確かめる最小の手順と結果

**検査を通ることの確かめ:** 作業木の中で、`PYTHONPATH=/tmp/ki5a5f-blind-B/variant` を付けて材料の pytest を撃つと 122 passed です。ruff は `--stdin-filename` で元の場所の設定を使って撃ち、上の結果でした。

**責務違反の確かめ:**
- 入力は、`/tmp/ki5a5f-blind-B/pkgroot/blindb_mutual/` に置いた小さな package です。
  - `handler_a` の X の節は、`handler_b` を入れて Y を出します。
  - `handler_b` の Y の節は、`handler_a` を入れて Z を出します。Z は `handler_a` の Z の節が答えます。
  - `warm` は A の下で X を出します(A → B → A の本当の再帰)。
  - `job` は B の下で Y を出します(再帰はありません)。
- script は `/tmp/ki5a5f-blind-B/scripts/run_steps.py <pkgroot> <手順…>` です。

| 1 つの process の中の順 | 元の版 | 変えた版 |
|---|---|---|
| job だけ | complete: true | complete: true |
| warm → job(job の答え) | complete: true | gaps [Z by handler_b]、unknown [handler_a]、complete: false |
| job → warm(warm の答え) | gaps [Z by handler_b]、unknown [handler_a] | gaps [Y by handler_a]、unknown [handler_b] |
| job → job(2 回目に読んだ module) | handlers・inner・programs | programs だけ |

**答えの置き場を通すと**(置き場を `/tmp/ki5a5f-blind-B/cache3-*` に向けて):

| | 元の版 | 変えた版 |
|---|---|---|
| process 1: warm → `cached_result` で job | true | false(この答えが置かれる) |
| process 2(新しい process): `cached_result` で job | true | **false が置き場から返る** |
| process 3(新しい process・置き場は off): job | true | true |

## 5. 確かめていない前提と、足りない情報

- **推測:** 下流(agora-controllers)の handler に、節の中で互いを入れる形があるかは分かりません。下流の閉じの検が 1 つの process で順に走るかどうかも確かめていません。
- **実測で分かった限界:** この例では、置き場の鍵の材料に `handlers`・`inner` が入っていました。`reading_sources` が数え落とした module を、`_depended_modules` が `programs` の名前空間からたどって補ったためです。数えが落ちても鍵が欠けない例しか作れていません。補われない形があるかは確かめていません。
- **推測:** free-threaded の Python で、共有された `_Reader` を 2 つの thread が使う場合を考えました。`facts` は読み終わる前に空の `_Facts` を表に入れるので、他の thread が読みかけの事実を辿って覚え、effect を取りこぼすおそれがあります。実測していません。なお 1 つの `HandlerEffects` を thread の間で共有することは、この差分が無くても既にできます。
- **実験の作りによる物:** 最初の試しでは、新しい process で `hy` を読み込む前に置き場を照らしたため `hy.core.macros` が見つからず、置き場が外れました。script の先頭で `import hy` するように直して撃ち直しました(検の conftest も先に `import hy` します)。下流の import の順は確かめていません。
- **守った制限:** 撃ったのは analyzer の package の検(材料の撃ち方どおり)、ruff、自分の script だけです。pyright と code-quality の検は撃っておらず、それについての判断もしていません。作業木の追跡されている file は変えていません(`git status` は始めから在った `docs/design/` の未追跡の dir だけ)。git の log・`docs/design/` の他の file・agora の card と会話は読んでいません。

WAIT: work — 呼び出し元の次の指示
