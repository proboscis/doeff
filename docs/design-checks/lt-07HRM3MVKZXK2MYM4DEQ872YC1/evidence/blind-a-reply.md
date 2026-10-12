## 結論

S4 の主張「配っても作り直しは起きない・予想する波及はなし」が崩れる例が見つかり、実際に再現しました。関数の中で require を使う module の場合、直す前の包みが作った記録には局所の macro の行がありません。今の包みはその記録を「今に合う」と判じます。そのため直した包みを配った後に macro を変えても、使う側は古い展開のまま動きます。要件を守るには、予想になかった records.py(RECORD_TAG)か記録の形を変える必要があります。

## 1. 変更要求と、それが必要になる理由

- **運用変更:** 関数の中の require の記録漏れを直した包み(この作業木 a4abf5218)を、worker と作業木へ配る。配った後も「関数の中の require で入れた macro を変えたら、使う側を展開し直す」という要件が成り立つことを求めます。
- **理由:** この直しは、記録漏れで古い展開が黙って使われた事への対処です。配った後に macro を変えて同じ事が起きるなら、直した意味がありません。
- **前提の扱い:** S4 の前提はすべて守っています。
  - 共有の置き場と .pyc は、直す前の包みが作った記録を持つ。
  - RECORD_TAG は変えない。
  - Hy は 1.3.1 のまま。
  - 同じ module の import は直列。

  契約の適用範囲の外にはしていません。規模は module 1 つと macro 1 つです。

## 2. 関係する主張と、予想に反して変わる物

- **主張 S4:** 関数の中の require を持たない module の記録は直す前と同じなので、作り直しは起きない。
- **実際:** 照らし(`macro_use.record_is_current`)は、記録にある行しか照らしません。macros も providers も空の記録は、macro が何に変わっても「今に合う」と判じられます。.pyc も置き場の entry もそのまま信じられ続けます。
- **要件を満たす直し方と、それぞれが S4 の「波及なし」に反する点:**
  - (a) `records.py` の RECORD_TAG を 7 に上げる。records-names の変更になり、全部の Hy の module が 1 度作り直されます。S4 の「作り直しは起きない」と両立しません。
  - (b) 印を上げず、記録に「どの覆いの記録者が作ったか」の欄を足して、古い物だけ外す。MacroRecord の形が変わります(RecordParts・record_of・record_to_json と、JSON の保存の使い手)。形が変わるので、結局 RECORD_TAG も上がります。
  - (c) 照らしの側で source を読み直し、「関数の中に require があるのに局所の行が無い」記録を見分ける。`record_is_current_here(record)` の公開の形に source が要るようになり、guard-hooks の契約とその使い手 3 つが変わります。
  - (d) 配る手順で置き場と .pyc を消す。配備の道具が包みの覆いの変化を知る必要があり、作り直しも全部に及びます。

## 3. 波及の原因 ― どの知識・判断がどこへ漏れるか

- **共同の不変条件(責務の表に無い):** 「同じ RECORD_TAG の記録は、同じ範囲を覆う記録者が作った」。これが成り立って初めて「記録に無い行 = 使っていない」と読めます。この条件は次の 3 者にまたがりますが、責務の表のどこにも書かれていません。records-names は「名と鍵の字・隠す知識なし」とされています。
  - 作る側: `MacroRecording.record`
  - 照らす側: `record_is_current`、`code_store.current_code`(合う最初の entry を使う)
  - 印の持ち主: records-names
- **隠すはずの知識の漏れ:**
  - RECORD_TAG を上げるかの判断には、guard-recording が隠すはずの「局所の表の覚え方」(直す前は覚えていなかった)が要ります。
  - 実際、`records.py` の RECORD_TAG の注釈(世代 3〜6)と ENVIRONMENT_READ の注釈(「前の版の照らしは…・前の版の記録は…古いと判じる」)は、記録者と照らしの意味を records.py の中で論じています。
  - `macro_use.py` 814〜815 行の `_NAMED_ONLY` の注釈も「記録の作り方そのもの(記録の形は RECORD_TAG で覆う)」としています。包み自身の変更を印で覆う前提ですが、今回の直しは覆う範囲を変えたのに印を変えていません。
  - (c) を選ぶと、「どの書き方が局所の表に入るか」という Hy の知識が照らしの側へ写されます。S2 の「局所の状態を開く口 1 か所で覚える」が崩れます。
- **対照:** code の読みの entry(`_facts_entry`)は、鍵に macro_use.py 自身の sha256(`_own_digest`)を入れています。一方、code の entry の鍵(`code_store.code_key`)と記録には、包みの版が入っていません。
- **区別:**
  - 配線(口の包み)の変更は要りません。
  - (b) の欄の追加は、意図した公開契約の拡張として扱えます。ただし S4 は記録の形を変えない前提だったので、予想の外です。
  - 「漏れ」と呼んでいるのは、(a) の判断が guard-recording 内部の覆いの知識を要する事と、(c) が Hy の知識を照らしへ写す事です。

## 4. 最小の再現(実測・作業木の source は変えていない)

**入力**
- `pkg/macros.hy`: `(defmacro answer [] 11)`
- `pkg/user.hy`:
```
(defn f []
  (require pkg.macros [answer])
  (defn g [] (answer))
  (g))
(setv value (f))
```

**直す前の包みの代役:** `/tmp/blind-a-lt07hr/scripts/probe.py` が、Hy を読む前に `macro_use._LOCAL_WATCH["installed"] = True` を置きます。局所の状態を開く口を包まないだけで、他は今の包みのままです。

**実行**
`env PYTHONDONTWRITEBYTECODE=1 DOEFF_HY_CODE_STORE=<off|dir> uv run --no-sync --project /work/toolset/worktrees/doeff-wt-blind-a-lt07hr python /tmp/blind-a-lt07hr/scripts/probe.py <根> <prefix|fixed>`

macro の書き換えは `edit_macro.sh` で行い、更新時刻を 2 秒進めます。

| 場合 | 手順 | 出た値 | 要件の値 |
|---|---|---|---|
| A(.pyc) | 代役で 11 を compile → macro を 12 へ → 今の包みで 2 回 | 11、11 | 12 |
| B(対照) | 今の包みで 11 → 12 へ → 今の包み | 12 | 12 |
| C(置き場) | 代役が置き場へ entry(11)→ 同じ user.hy の新しい木(macro 12・.pyc 無し)を今の包みで | 11 | 12 |
| D(対照) | 今の包みで置き場へ entry(11)→ 新しい木(12)を今の包みで | 12 | 12 |

**観測した事**
- A の .pyc の記録は `macros=() providers=() absent=('defn','f','g',…)` でした。macros.hy が 12 の状態で、今の包みの `record_is_current_here` は True を返しました。
- C では user の鍵の entry が 1 つのままでした。今の包みは compile せず、直す前の entry を使って、新しい entry を足しませんでした。D では 2 つになりました。
- 見るべき物は 3 つです: 値、.pyc の記録の macros の行、user の鍵の entry の数。

## 5. 未確認の前提・不足する情報

**実測したもの:** 上の A〜D だけです(Hy 1.3.1・Python 3.14t の venv)。

**推測(確かめていないもの):**
- 代役が直す前の包みと同じかは確かめていません(git の歴史は読んでいない)。根拠は 2 つあります。入力の「RECORD_TAG を変えず」と、`test_macro_use_record.py` の「直す前は赤: … 記録に無かった」です。外の macro を関数の中の require だけで使う module なら、記録に macro の行が無いので、代役の細部に結論は依らないと見ています。
- 古い展開は長く残ると見ています。`current_code` は合う entry を使うたびに時刻を進めるので、その entry は掃除の 7 日にかかりません。終わるのは、使う側の source を変えた時か、Hy の版を上げた時です。
- 直す前の doeff-hy の作業木が残る間は、行の無い entry が足され続けると見ています。既存の検 `test_two_record_generations…` が測るのは印の違う世代だけで、同じ印で覆いの違う世代は測りません。この点は測っていません。
- 解析器の cache と型検査の展開の保存も、同じ記録と同じ照らし方を使うので同じ形になると見ていますが、その code は読んでいません。

**既存の検で見つからない理由:** `test_bytecode_guard.py` の関数の中の require の検は、毎回空の木から始めます。直す前の記録を持つ木を測りません。

**不足する情報:**
- 実際の置き場と worker に、関数の中で require を使う module がいくつあるか(全数は数えていません)。
- 配る手順が置き場や .pyc を消すかどうか。
- Hy を上げる予定。
- 直す前の版の code そのもの。

この結果をもって合格・安全とは判じません。作業木は変えていません(`git status` は元から在った BLIND_INPUT.md と docs の削除だけ)。作業用の file はすべて `/tmp/blind-a-lt07hr/` の下にあります。

WAIT: work — 頼んだ側の次の依頼
