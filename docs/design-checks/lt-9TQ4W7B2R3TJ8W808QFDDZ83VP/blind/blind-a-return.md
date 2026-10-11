I読んだ範囲と、見つけた失敗例を書きます。テストと検査は走らせていないので、下に書く動作はすべて code を読んで追った推測で、実測はありません。読む木と共有の source は変えていません。

# 失敗例: 常駐の process で、読み込み済みの値の module を書き換えた後に Hy の module を compile すると、展開と食い違う記録が .pyc と置き場に残る

## 1. 変更要求と、それが必要になる理由

**要求:** 長く動き続ける process(Hy の REPL、Jupyter の kernel、file の変更を見張って検を走らせ直す常駐の検、型検査の常駐の server など)の中で、次の手順を正しく扱えるようにする。
- 開発者が、macro が読む表の module(例: `tables.py` の `ROWS`)を書き換える(手で編集する、または `git pull`・`git checkout` で替わる)。
- その後、同じ process が、まだ読んでいない Hy の module か、.pyc が古い Hy の module を読み込む。

**理由:** これは主張 S3 の場面そのものです(「長く走る process で、読み込みと読み込みの間に macro の依る file が変わる」)。S3 はこの場面で変える所は「なし」と予想しています。開発の作業では、常駐の process を残したまま file を替える事はよく起きます。

## 2. 関係する主張・前提と、予想に反して変わる物

**関係する主張と前提**
- **S3:** 次の一番外の読み込みは file の状態を読み直し、辿り直して古いと判じる。開け閉めは L の 1 か所で、確かめは M。予想の範囲は「なし」。
- **S4:** 置き場を作業木と版をまたいで共有する。
- **前提:** 照らしの正しさは「歩みが読んだ物が同じなら digest が同じ」に依る。
- **B1:** 記録の digest が依った物が違えば古いと判じる。

**予想に反する所**
- 古いと判じる所までは S3 の通りに動きます。
- ところが、その後の compile し直しは、process の中に既に在る**古い module の object**(`ROWS["b"] == 2`)で展開します。
- 一方、記録の digest は**今の disk の file**(`ROWS["b"] = 20` の文)から作られます。
- その結果、展開(2)と記録(20 の文)が食い違った .pyc と置き場の entry ができます。
- 後の新しい process(disk は 20)は、この記録を「今に合う」と判じて 2 の展開を使います。新しく compile すれば 20 になるはずです。
- B1 の字面(digest が依った file は同じ)は守られていますが、見張りの目的(.pyc の展開が、今の環境で展開し直した結果と同じであること)が破れます。破れているのは、前提の裏にある「歩みが読んだ物 = 展開が読んだ物」です。

**直すのに変わる module(予想は「なし」)**

| module | 変わる理由 |
|---|---|
| L(loader_hooks) | module を実行した時の source の状態を、Hy に限らず `.py` の値の module も含めて覚える役が新しく要る。今は Hy の source だけを `get_code` の間だけ覚え(`_remembering_get_data`、line 328 の `pop`)、「source を process の間ずっと持たない」と決めている。`file_sha256` は今の disk を読む |
| G(import_scope) | 持つのは「一番外の読み込み 1 つ」の写しだけで、寿命は context ごと。要るのは「module ごとの実行の時点」の状態で、寿命は process。G の形と寿命が合わない(G を変えるか、新しい持ち主が要る) |
| M(macro_use) | `_bound`・`_fold`・`_closure_files`・`_note` と `_remembered` の覚えで、実行の時点と今の file の状態を比べる必要がある。違えば、その digest を記録に使わない(いつも古いと判じる印にする)か覚えない、と決める |
| B(bound_source) | `binding_of(path, name)` は path から今の中身を読む(`_source`、line 129–149)。実行の時点の中身か、期待する sha を受け取る形に口が変わる |

## 3. どの知識がどこへ漏れて波及するか

**読む時点の混在**
- M の歩みは、2 つの違う時点の物を混ぜて 1 つの digest にしています。
  - process の中の object の時点: 関数の code(`readings.facts(function.__code__)`)と素の値。
  - 今の disk の時点: 辿れない値を覆う文(`_bound` → `bound_source.binding_of`)、`_fold` の file の sha256(`self._sha256_of` = L の `file_sha256`)、`record.files`。
- 展開が使うのは前者だけです。

**隠れた仮定**
- 「disk の今の中身 = その module を実行した時の中身」という仮定は、M の `_state_value` の註の 1 行(line 1523「初めの中身と書き換えは source に書いてある」)にしか書かれていません。誰もこれを確かめていません。
- B は「中身で決まる純な読み」を隠す責務を持っています。しかし「**いつの**中身を読むか(path から今読む)」という決めは、B の内側に隠れたままです。M の正しさは、その内部の決めに依っています。これが漏洩です。
- 直すのに要る事実(各 module を実行した時の source の状態)を手にできるのは L だけで、L は意図して捨てています。
- G の「読み込み 1 つ = file の状態の 1 つの写し」は file の状態の写しで、module の object の写しではありません。写しの粒度の知識が、L/G と M の間で食い違っています。

**区別**
- **公開契約の拡張:** なし。利用者向けの新しい機能ではありません。
- **配線の変更:** L から M/B へ、実行の時点の状態を渡す線が新しく要ります。これは正当な配線の変更です。
- **共同の不変条件:** 「記録の digest のうち file から読む部分は、展開が使った module の object を作った時の file の中身を写す」。L(実行する)・M(歩む)・B(読む)にまたがり、今はどの module も持っていません。これが主な欠陥です。
- **隠すはずの知識の漏洩:** B の読む時点と、L の source の寿命の決めに、M の正しさが依っています。

**S4 への波及(推測)**
- 誤った code は、`_to_shared_store` で user の source の bytes を鍵に置き場へ入ります。
- 同じ中身の user と新しい `tables.py` を持つ別の作業木は、冷えた .pyc の代わりにこの entry を引きます。照らしは「合う」になり、誤った展開が作業木をまたいで広がります。

## 4. 最小の入力と再現手順、観測すべき結果(走らせていない)

既存の検 `test_a_file_changed_between_two_imports_is_noticed_by_the_second`(tests/test_macro_use_record.py line 1234)の組を、1 か所変えて使います。

**入力の変更**
- `CROSS_MACROS` の `(defmacro rows [] (get tables.ROWS "a"))` を `"b"` を読む形に替える。今の検は `"a"`(書き換えの前後で 1 のまま)を読むので、食い違いが見えません。
- `SCOPE_CHILD` に、読み込んだ module の `value` を出す 1 行を足す。

**手順**(`DOEFF_HY_CODE_STORE` と `PYTHONPYCACHEPREFIX` は fixture の通り、`PYTHONDONTWRITEBYTECODE` は無し)
1. `run("top")`: 全部を compile する(ROWS["b"] = 2)。
2. `run("user_0", 'edit:tables.py:ROWS["b"] = 2:ROWS["b"] = 20', "user_1")`: user_0 の照らしで tables が process に読まれ(2)、file を書き換えた後に user_1 を読む。
3. 新しい process で `user_1` だけを読み、`value[3]` を出す。
4. 対照: pycache と置き場を消して 3 をもう一度走らせる。

**観測すべき結果(推測)**
- 手順 2 の `compiled == ["user_1.hy"]`: S3 の通りに古いと判じる。
- 手順 3 の `compiled == []` と `value[3] == 2`: 欠陥。
- 手順 4 の `value[3] == 20`: 正しい値。3 と 4 が食い違えば失敗です。

**なお短い形:** 1 つの process で `import pkg.tables` → `tables.py` を書き換える → `import pkg.user` と進め、新しい process で `pkg.user.value` を見ても同じになるはずです。この形では、覚え(`_REMEMBERED`)も読み込みの覚え(`ImportScope`)も通りません。つまり、この便の変更 2 が原因ではなく、M と B の作りに前から在る問題です(推測)。

## 5. 未確認の前提・不足する情報(推測と実測の区別)

**実測:** なし。テスト・検査・Python の式は 1 つも走らせていません。

**code を読んで確かめた事**
- `_bound` は `binding_of(file, name)` で今の disk の文を読む。
- `_fold` は L の `file_sha256` で今の disk の sha を読む。
- `_remembering_get_data` は Hy の source だけを覚え、`get_code` の後で捨てる。
- 既存の検は `"a"` を読むので、この食い違いを見ない。

**推測(未確認)**
- macro `rows` の code の名の表に `tables`・`ROWS` が出て、歩みが `_attribute` → `_bound` に入る。
- Hy の `require` は `sys.modules` に在る古い module をそのまま使う(読み直さない)。
- 手順 2 で書いた .pyc の頭が user_1.hy に合い、手順 3 で `_from_shared_store` を通らず .pyc を読む。
- `ast.dump` の文が 2 と 20 で違う。

**不足する情報**
- agora や doeff-cluster で、実際に「常駐の process が file の書き換え後に Hy の module を遅れて読む」使い方があるか(worker・型検査の server・REPL の運用)。
- 型検査の展開の保存(doeff_hy.static_cache)が、同じ口で同じ食い違いを残すか。

## 付録: 主の例とは別に見つけた候補(どれも推測・未検証)

- **S3・S5(thread が覚えを継ぐ):**
  - Python 3.14 の free-threaded では、thread が起こした側の context の写しを継ぐのが既定だと理解しています(この環境では未確認)。
  - 一番外の読み込みの途中で起きた thread は、読み込みが終わった後も同じ `ImportScope` を持ち続けます。その thread の後の読み込みは、新しい覚えを置かず(`_scoped_exec_module`)、file の状態を読み直しません(`_state_now`・`_confirmed`)。
  - 直すには G(閉じた印か持ち主)・L(閉じる)・M(閉じた覚えを無いとみなす)が変わります。
  - process の中の module は古いままなので、害は主に S3 の主張が破れる事と、費用の側だと見ています。
- **S1・S4(code の読みの鍵):**
  - 読み方の版は `macro_use.py` 1 file の sha256(`_own_digest`)です。
  - `_called_modules` は `hy.mangle`・`slashes2dots` に依りますが、`_facts_entry` の鍵に Hy の版がありません。
  - 読みの code を別の file に分けると(macro_use.py は 1,824 行)、その file の規則の直しが置き場の entry を冷やしません。共有の置き場(7 日残る)では直しが効かず、検は新しい一時の置き場で緑のままになります。
- **B1 の条 3 の別の形:**
  - `(setv MODES {"a" (os.environ.get "X" "plain")})` のように、環境変数から組んだ dict は辿れない値として文の字面だけで覆われます。`os` は標準 library なので名だけが入ります。
  - そのため、環境変数を替えても digest は変わらないと読みました。今の検(条 3)は素の文字列の `MODE` だけを見ています。
