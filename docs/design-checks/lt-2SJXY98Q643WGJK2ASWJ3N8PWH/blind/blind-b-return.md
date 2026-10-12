# 盲検 B の返答 — 失敗例: S1 どおりに節の操作 `throw` を足すと、書き手の関数 `throw` の意味が黙って替わる

S1(節の操作 `(throw 例外)` を足す)を、設計の主張のとおり `clause_ops.hy` だけで実装しました。提示の検査は 215 件すべて通りました(実測)。それでも、書き手の module が自分の関数 `throw` を節の本体で呼んでいると、展開の誤りも警告も出ずに、その呼びが VM の `ResumeThrow` に書き換わります(実測)。

## 1. 機能の要求と差分

- **要求(S1 の文のまま)**: handler の節から、effect を出した所(続き)へ例外を投げたい。末尾では handler を外す `transfer-throw` に替える。
- **差分**: `/tmp/blind-b-ki-1618/s1-throw.patch`。`clause_ops.hy` だけに 26 行を足します。
  - `_throw` と `_transfer-throw` を足します。どちらも実行時は `` `(yield (~(alias-symbol "ResumeThrow") k ~error)) `` です(`transfer-throw` は `TransferThrow`)。
  - 型検査のための展開では、`_resume` の `typed_resume` と同じく、使う所で `import-form` します。
  - 行は次の 2 つです。
    - `throw`: `:ending RESUMES :arity 1 :returns True :tail "transfer-throw" :nodes #("ResumeThrow") :static-nodes #()`
    - `transfer-throw`: `:returns False :nodes #("TransferThrow") :static-nodes #()`
- **この形になった経緯(実測)**: 最初は検査の中の行 `_THROW` と同じく `static-nodes` に node を載せました(`/tmp/blind-b-ki-1618/s1-throw-v1-static-nodes.patch`)。すると、提示の pyright の検が 2 件赤になりました。throw を使わない handler でも `_doeff_ResumeThrow`・`_doeff_TransferThrow` が import され、`reportUnusedImport` になるためです。実装者はこの赤を見て、`_resume` の形をまねた今の差分に直すはずです。

## 2. 本来の所有者と、侵す契約

- **名の持ち主**: 書き手の関数 `throw` は、writer-namespace(書き手の名の束縛)の物です。
- **判断が移る所**: 「節の本体の `(throw x)` は節の操作か、書き手の関数の呼びか」という判断は、`clause-op` が頭の名だけで決めています。
  - この判断は `_terminates`・`clause-ops`・`_tail-resume-to-transfer`・`_rewrite-ops` が節の本体のすべての式に当てます。
  - `_rewrite-ops` は入れ子の `fn` の中まで書き換えます。
  - handle.hy は書き手の module の名(`_module-names` → `_build-clause` の `module-names`)を持っていますが、`:=` にしか使っていません。
- **起きること**: 表に行を 1 つ足すだけで、既にある全ての節の本体で、名 `throw` の意味が書き手から表へ移ります。
- **カードの不具合と同じ形です**: 1 つの名を書き手と展開が分け合い、黙って別の物を指します。R1〜R4 と検査が見ているのは doeff の node の名だけで、節の操作の名は見ていません。
- **S1 の主張との関係**: 「書き手の `TransferThrow` を隠さない」は成り立ちます。そのかわりに書き手の `throw` を隠します。
- **侵す契約**: R1・R2 の趣旨である「書き手の名は書き手の物のまま」と、既にある書き手の code の意味です。前は「書き手の throw を呼ぶ」として compile が通り、今は「続きへ ResumeThrow」として compile が通ります。

## 3. 提示の各検査が拒否しない理由

全体の結果(実測): 基準の版は 211 passed、overlay の版は 215 passed です。増えた 4 件は、表の行ごとに回る検が 2 行分増えたものです。

| 検査 | 拒否しない理由 |
|---|---|
| `test_handle_writer_names.py` | 監視するのは doeff の 7 つの名(+ 型検査用の 2 つ)だけで、書き手の物は effect の class です。節の本体は resume・transfer・try・`:=` だけで、操作と同じ名の書き手の関数を持ちません。新しい node は `_doeff_` の別名で読まれ、そもそも監視の外です。 |
| `test_expansion_names.py` | 書き換えは `alias-symbol` と `import-form` だけを使い、素の Symbol を書きません。9 つの名の表は変えていません。 |
| `test_handle_clause_ops.py` | 新しい行を受け入れる作りです。本物の行が先に引かれ、検査の中の行 `_RESUME_THROW`・`_TRANSFER_THROW` と同じ動きをします。`"throw"` の綴りは clause_ops.hy の注釈にしかありません。「表の外に操作の名を書かない」の検は doeff_hy 自身の module だけを見て、書き手の module は見ません。 |
| `test_handle_scope.py` | 置き場ごとの組み立てを変えていません。 |
| `test_static_check_defhandler.py` | 検体が throw を使いません。`static-nodes` が空なので、使われない import が出ません。 |
| 品質検査の module 契約 | **実行していません**。契約の JSON を読んだ限りでは、縛るのは expansion_names を誰が import できるかと、その依存だけです。差分は import を足さず、clause_ops.hy は契約に未登録です(推論)。 |

## 4. 最小の再現手順と期待結果(すべて実測)

作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-1618` の根で実行します。

```
mkdir -p /tmp/blind-b-ki-1618/overlay && cp -r packages/doeff-hy/src/doeff_hy /tmp/blind-b-ki-1618/overlay/
patch /tmp/blind-b-ki-1618/overlay/doeff_hy/clause_ops.hy < /tmp/blind-b-ki-1618/s1-throw.patch
PYTHONPATH=/tmp/blind-b-ki-1618/overlay uv run pytest tests/test_handle_writer_names.py tests/test_expansion_names.py \
  tests/test_handle_clause_ops.py tests/test_handle_scope.py packages/doeff-hy/tests/test_static_check_defhandler.py -q -W ignore
```

- **検査**: 215 passed になります。
  - overlay は今は当て済みです。手順は新しい複製から始める場合のものです。
  - `doeff_hy.__file__` が overlay を指すことは確かめました。
- **違反 1(黙って壊れる形)**: `/tmp/blind-b-ki-1618/probe_writer_throw.py`
  - 書き手は `(defn throw [e] (raise e))` を持ち、節の中で検める関数 `(fn [a] (when (< a 0) (throw …)) a)` を作ってから `(resume (checked amount))` します。
  - 基準の版: `pay(5) -> 5`、`pay(-5) -> ValueError`。
  - overlay の版: `pay(5)` も `pay(-5)` も generator object を返します。入れ子の `fn` に yield が入り、検めごと消えます。誤りは出ません。
- **違反 2(直に書いた形)**: `/tmp/blind-b-ki-1618/probe_writer_throw_direct.py`
  - 節は `(when (< amount 0) (throw …)) (.append log amount) (resume amount)` です。本文は ValueError を受けて `"rejected"` を返します。
  - 基準の版: `-5` は `'rejected'`、log は `[]`。
  - overlay の版: 書き手が止めていたはずの `log [-5]` が走り、その後に `RuntimeError … one-shot violation` になります。

## 5. 未確認の前提と、不足する情報

### 書き手の側の前提

- この repo の `.hy` に `(throw …)` の呼びはありませんでした(grep、実測)。
- 一方で、`doeff_agents/handlers/headless.hy` と `headless_codex.hy` の節の本体は、書き手自身の defk `stop` を呼んでいます(読んで確かめた)。同じ名の操作が将来足されれば同じことが起きます。
- 他の repo は、読む所の制限で見ていません。

### S1 の予想の範囲について

- 提示外の `tests/test_linter_clause_ops.py` は overlay で赤になりました(実測)。S1 では生成物 `packages/doeff-linter/data/clause_ops.json` も書き直す必要があり、予想の範囲「clause_ops.hy(+ 検)」から漏れています。tracked な file なので、私は書き直していません。
- throw を使う handler が pyright strict で赤 0 になるかは確かめていません。

### 同じ型の既存の穴(変更なしの 1410ce5 で実測・`/tmp/blind-b-ki-1618/probe_field_k.py`・`probe_preexisting.py`)

- **欄の名が `k`**: effect の欄の名が `k` だと `TypeError: argument 'k': 'int' object is not an instance of 'K'` になります(大きく落ちる)。
- **欄の名が `effect`(黙って抜ける)**: 包みの effect `Traced [on effect]` で `:when` が外れると、外の handler には中身の effect が渡ります。答えは `'outer saw Inner'` で、期待は `'outer saw Traced'` です。展開が素で読む `effect`・`k` が、書き手の欄の名と同じ関数の中に在るためで、R1〜R3 の監視の外です。欄の順を `[effect on]` にすると AttributeError になります。
- **R2 の文と検の差**: module の段の handle と defhandler の後、globals に `_doeff_*` の 12 の名、`_hy_anon_3`・`_hy_anon_5`、`doeff_hy` が増えます。R2 の検が見るのは素の 7 つの名だけです。

### 読んで確かめたこと(実行していない)

- **S2 の主張と公開契約の食い違い**: ADR-DOE-HY-006 R8、`session.py` の説明、`test_val_var_lazy.py` の外の handler の検は、`Get`・`Put`・`Some` を `session_key` で引くことを外の handler への公開契約にしています。S2 の予想の範囲には、これらが入っていません。
- **R4 の検は推論の段階です**:
  - 監視の名の一覧は表(`_HOMES`・CLAUSE-OPS)から引かず、手で書いた固定の一覧です。
  - `.hy` では Symbol の model だけを拾うので、`(Symbol "Put")` のような文字列からの組み立ては拾いません。
  - `.py` では、素の `Symbol("…")` の呼びだけを拾い、`hy.models.Symbol` の呼びは拾いません。

### 確かめていないこと

- 品質検査の module 契約
- bytecode guard(S3)
- Python 3.14 より前の版での動き

共有の source は変えていません。作業木の差分は、準備の時に消された `docs/design-checks/` と `BLIND_INPUT.md` だけです。

WAIT: work — 頼んだ側(盲検の依頼元)の次の依頼
