## 責務違反の失敗例: class の本体の defhandler の節が自分の名 `h` を名で読む

提示された検査は通るのに、class の本体の defhandler を壊し、R3 も破る実装を 1 つ作れました。下の実測の記録は、すべてこの作業木で実際に走らせた結果です。作業木は元に戻してあり、`git status` は `?? BLIND_INPUT.md` だけです。

### 1. 機能要求と実装

**要求:** defhandler の `{:effects [...]}` 宣言は、今は属性 `__doeff_effects__` として残るだけで、実行の時には効きません。節が `<-` で宣言の外の effect を外へ出したら TypeError にしたい、という要求です。defk の `:pre`/`:post` と節の終わり方の検め(R15)は実行の時に効くので、それに揃える形です。

**差分:** `/tmp/blind-b-ki-edab/failure-example.diff`(2 file・40 行)
- `declarations.hy` に `declared-effect [handler effect]` を足しました。`handler.__doeff_effects__` と照らし、宣言の外なら TypeError を出します。宣言の持ち主の module に置いています。
- `handle.hy`:
  - import は S2 の決まりどおりにしました。`_declared-effect-import` を handler-imports に足し、defhandler の `imports` の並びに載せています。class の本体では組む関数の中で import されます。型検査のための展開では何も出しません。
  - 違反の芯は次の 1 行です。`_build-clause` が handler の名(Symbol)を `_expand-handler-binds` へ渡し、節の `<-` の effect の式をこう包みます。
    ```hy
    (setv (get core -1) `(_doeff-declared-effect ~handler ~(get core -1)))   ; ~handler = 定義の名 h
    ```
  宣言は `declaration-setters` が定義の後に `h` の属性として置きます。なので「節から `h` を名で引いて宣言を読む」のが、宣言を写さずに済む素直な書き方になります。

### 2. 本来の持ち主と、破られる契約

- **持ち主:** 「節の関数からどの名が見えるか」の判断は、scope-judgment と handle/defhandler-expansion だけが持ちます(S1)。
- **何が移るか:** 新しい行は handler-expr(`_build-clause`・`_expand-handler-binds`)の中で、定義の名 `h` を節の関数の自由な名として書き込みます。これで handler-expr が「定義の名は節から見える」という前提を、scope の判断なしに持つことになります。
  - module の段では module の globals から読めます。
  - 関数の本体では、`_scoped-definition` が包む関数の閉包から読めます。
  - class の本体では `h` が class の属性になり、節の関数は名を引く時に class の段を飛ばすので読めません。
- **S2 では防げない理由:** S2 が決めているのは「足した import の名」の通り道だけです。import ではない名(定義の名)を節が名で読む形は、どこも縛っていません。
- **破られる契約:**
  - **R2:** class の本体の defhandler で、節に `<-` があれば、宣言の有無に関係なく NameError になります。
  - **R3:** module の段に同じ名の `h` があると、class の本体の節がそちらの宣言を読みます。正しい handler を誤って断る場合と、違反を黙って通す場合の両方が起きます。
  - 宣言の知識(`__doeff_effects__`)を、節が書いた場所の名の引きを通して読む形になります。

### 3. 提示された検査が拒否しない理由

`uv run pytest tests/test_handle_scope.py -q` は差分を当てた後も **45 passed** でした(実測)。

| 検査 | 拒否しない理由 |
|---|---|
| `test_class_body_handle_clauses_read_their_operations` | 6 形の節のどれにも `<-` が無いので、包む式が生まれない。増えた import は組む関数の中に入る |
| `test_class_body_handle_leaves_module_globals_alone` | module の globals の名の集まりは変わらない。書き手が束ねる名は Transfer・Resume・Pass だけで、`h` を束ねず、`<-` も走らない |
| `test_module_and_class_level_definitions_are_not_wrapped` | `Holder.inner_h` の節に `<-` が無い。`__qualname__` と、module の globals に `WithHandler` があることは変わらない |
| 関数の中の検(`_CASES`・`_HOSTS`・順の検) | defhandler の節に `<-` が無い。handle(名なし)は包まない。import は外の関数の局所に出ない |
| 型・静的な規則 | §4 のとおり無い。型検査のための展開は、試しの file で差分の前後が 1 字も同じだった(実測)。`doeff_hy.static_check.project` で比べた |

### 4. 確かめる手順と結果

1. `git -C <作業木> apply /tmp/blind-b-ki-edab/failure-example.diff`
2. `uv run pytest tests/test_handle_scope.py -q` → 45 passed
3. `uv run python /tmp/blind-b-ki-edab/probe_bind.py`
4. 戻す: `git apply -R` で同じ diff を逆に当てる

最小の入力(module の頭は `_CLASS_ONLY_HEADER` と同じ形に `Scale` を足したもの):
```hy
(defclass C []
  (defhandler h (Bump [n] (<- r (Scale n)) (resume (+ r 100))))
  (defhandler outer (Scale [n] (resume n))))
```
これを `run(scheduled(state(initial={})(C.outer(C.h(Bump(1))))))` で走らせます。

| probe の形 | 差分の前 | 差分の後 |
|---|---|---|
| class の本体・宣言なし / 引数あり / `{:effects [Scale]}` | 101 | `NameError: name 'h' is not defined` |
| module に `h`(宣言 [Other])があり、C.h は [Scale] を守っている | 101 | TypeError(宣言: Other)— 誤って断る |
| module に `h`(宣言 [Scale])があり、C.h は [Other] なのに Scale を出す | 101 | **101** — 違反を黙って通す |
| module の段・関数の本体・module の段の引数あり | 101 | 101 |
| module の段で宣言の外を出す | 101 | TypeError(機能は module の段では働く) |

### 5. 未確認の前提と不足

- **推測:** この機能を出す card があるかは知りません。「実行の時に `:effects` を守らせたい」は、`:pre`/`:post` と R15 に揃える要求として私が立てたものです。
- **未実行:** 提示されていない検(`test_val_var_lazy.py`・`test_handle_macro.py` など)は走らせていないので、それらが捉えるかは分かりません。doeff-hy-check(pyright)も走らせておらず、比べたのは展開した Python の字だけです。
- **未実行:** `declarations.pyi` に `declared_effect` を足していません。型の面への影響は測っていません。
- **測った範囲:** Python 3.14.3 の上で、`hy.eval` で読み込む経路だけを確かめました。.pyc を通らないので、S4(bytecode-guard)の経路は通っていません。3.14 より前の版で同じ動きかは測っていません。NameError は Python の名の引きの決まりから来るので、版に依らないと見ていますが、これは推測です。
- **調べたが採らなかった候補(読んだだけで未実測):**
  - S2 の新しい節の操作は、名を `_vm-imports` に足すか、dispatcher の中で inline に import するのが普通の書き方です。どちらも class の本体で動くと読めたので採りませんでした。
  - 引数ありの形の import を 1 度にまとめる速さの直しは、module の globals へ setdefault する形なら R3 の検が捉えます。包んで `__qualname__` を書き直す形は通りますが、目に見える害が弱いので採りませんでした。
- **広い見方(推測):** class の本体に定義の場所で置かれる名(定義の名・`doeff_hy`・`_doeff_QuotedForms`)を、節や組む関数が名で読む変更は、どれも同じ穴を開けます。提示された検は 6 形の操作の名と module の globals しか見ないので、これらは捉えられません。

Files are in /tmp/blind-b-ki-edab:
- failure-example.diff
- probe_bind.py
- static_text.py
- static_case/static_probe.hy
- static_before.py
- static_after.py
