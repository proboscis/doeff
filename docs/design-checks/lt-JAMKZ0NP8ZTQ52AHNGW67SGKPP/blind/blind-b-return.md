# 盲検 B の答え: DOEFF002 を通る責務違反の例

**結論:** 失敗例が 1 つ見つかりました。DOEFF002 は、`async def` の method の中の `self.X = …` を数えません。S1 の前提どおりの素の代入の形でも、DOEFF002 も基点との比べも黙って通ります。

repo の file は変えていません(`git status --short` は空)。一時の file はすべて `mktemp -d` で作った `/tmp/tmp.lgT0CD25H6/` の下に置き、linter もそこの file だけに当てました。

## 1. 機能の要求と差分

**要求:** OpenRouter の client が、呼ぶたびに `httpx.AsyncClient` を作って閉じている。接続を使い回したい(keep-alive)。

**対象:** `packages/doeff-openrouter/src/doeff_openrouter/client.py` の `OpenRouterClient`。今は `@dataclass` で、毎回 `async with httpx.AsyncClient(...)` している。

**差分**(写しの before → after。実測に使った物そのまま):
```diff
-from dataclasses import dataclass
+from dataclasses import dataclass, field
@@ class OpenRouterClient:
     default_headers: dict[str, str] | None = None
+    # One connection pool per client (keep-alive): opened by the first request, reused by the next ones.
+    _http: httpx.AsyncClient | None = field(default=None, init=False, repr=False, compare=False)
@@ async def a_chat_completions(...):
         request_timeout = timeout if timeout is not None else self.timeout
-        async with httpx.AsyncClient(
-            base_url=self.base_url,
-            timeout=request_timeout,
-        ) as client:
-            response = await client.post(
-                "/chat/completions",
-                json=payload,
-                headers=self.build_headers(headers),
-            )
-            response.raise_for_status()
-            return response.json(), response.headers
+        if self._http is None:
+            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
+        response = await self._http.post(
+            "/chat/completions",
+            json=payload,
+            headers=self.build_headers(headers),
+            timeout=request_timeout,
+        )
+        response.raise_for_status()
+        return response.json(), response.headers
+
+    async def aclose(self) -> None:
+        """Close the kept connection pool (the next request opens a new one)."""
+        if self._http is not None:
+            await self._http.aclose()
+            self._http = None
```

**対照の版(twin):** 同じ状態を同期の補助 `def _pool(self)` の中で作る版です。違いは「代入が `def` の中か `async def` の中か」だけです。

## 2. 本来の持ち主と、侵す契約

**後から変わる状態を名で見せる判断の持ち主は M1(DOEFF002)です。**
- M2 の説明文は「`__init__` / `__post_init__` / `__set_name__` の外で代入される属性は、型を問わず `mut_` / `_mut` で始める」と言っています。
- S1 の主張も「後から代入する状態を、型に依らず `_mut` の名に強いる」です。
- `_http` は、作った後の最初の `await` で作られ、`aclose` で `None` に戻されます。契約に従えば `_mut_http` でなければなりません。
- 代入の形は S1 の前提の `self.<名> = …` そのものです。前提を外れた書き方ではなく、M1 の数え方の穴です。

**M1 が見落とす理由(実装を読んだ):**
- `collect_class_attributes` は class の本体の `Stmt::FunctionDef` だけを見ます。`Stmt::AsyncFunctionDef` の method は丸ごと読みません。
- このため、M2 の説明文(初期化の外の代入はすべて当たる)と M1 の振る舞いが食い違い、「M1 と食い違わない事」という M2 の条件が崩れています。
- どの入れ子の本体に降りるかは `utils::nested_bodies` が「規則が読む 1 か所」と名乗っています(`_ =>` を使わない・#2834)。ところが M1 は自前の `collect_stmt_attributes`(`_ => {}`)で、同じ知識を 2 つ目として欠けたまま持っています。
- 「どの method・どの本体を代入の場として数えるか」は、`is_init_method` の外の 2 か所で決まっています。S1 の「初期化の集合を変えるのは 1 か所だけ」は字面では正しいのですが、数える範囲を決める場所は 1 つではありません。

**状態が移る先:**
- 接続の pool の寿命(最初の要求で開き、次の要求で使い回し、`aclose` で閉じる)と、event loop への結びつきが、設定の値の object に入ります。
- その object は docstring で「Thin wrapper around … configuration」と名乗り、`get_openrouter_client` が `Put("openrouter_client", client)` / `Ask("openrouter_client")` で、run の中のすべての Program に配ります。
- doeff の Await は process で共有する「doeff-await-bridge」の loop で走り、使えなくなれば新しい loop に替えます(`doeff_core_effects/handlers.py` の 375〜405 行)。替わった後は、持っていた pool が閉じた loop に結びついたまま残ります。
- 誰が `aclose` を呼ぶか(effect / handler の持ち主)は、提示された責務の表に無いので推測に留めます。

## 3. 各検査が拒否しない理由

| 検査 | 結果 | 根拠 |
|---|---|---|
| DOEFF002 | 通る(実測) | after は DOEFF002 が 0 件。twin は同じ `self._http = …` が 128 行で DOEFF002 に当たる。dataclass の分岐も、数えた非初期化の代入が無いので field の宣言を足さない。 |
| DOEFF004 | 通る(実測) | `os.environ` / `getenv` への参照が無く、所見 0 件。 |
| 日次の lint の段の基点の比べ | 通る(写しで実測) | client.py の基点は DOEFF006 2・DOEFF016 1・DOEFF021 1。after も同じ数で、増えた組が無い。 |
| exclude・`:exempt` | 使っていない | file は Plain の母集団(`packages/doeff-openrouter/architecture.hy` が外すのは `local_dotenv` だけ)。 |
| commit の hook | 通るはず(推測) | 同じ `measure` を stage した file の path で呼ぶ作りなので、同じ数になるはず。linter の `--commit-hook` も同じ規則を当てるはず。どちらも走らせていない。 |
| cargo test | 影響しないはず(推測) | linter の code を変えておらず、tests は client.py を読まない。走らせていない(禁止)。 |

日次の段の行は、`scripts/hook_finding_baseline.py` そのものではありません。同じ引数(`--no-log --force-exclude --output-format json`)・同じ `[tool.doeff-linter]`・同じ `architecture.hy` を置いた写しの木で、`linter_error_counts` と同じ数え方をしました。写しの before が基点の数とちょうど一致するので、写しの設定は忠実と見ています。

## 4. 最小の入力・手順・期待結果

**a) 最小の入力(M5 の `_Reader` と同じ形)**

`/tmp/tmp.lgT0CD25H6/min/` に、`[tool.doeff-linter]` の写しの pyproject.toml と `.git` dir を置き、次の 2 file を作りました。`reader_sync.py` は `async def` → `def`、`await read_absent()` → `read_absent()` だけが違います。
```python
class Reader:
    def __init__(self) -> None:
        self._absent: object | None = None

    async def absent_receiver(self) -> object:
        if self._absent is None:
            self._absent = await read_absent()
        return self._absent
```
```
$ cd /tmp/tmp.lgT0CD25H6/min && $B --no-log --force-exclude --output-format json reader_async.py   (error を抜き出す)
Found 1 issue(s): 0 error(s), 0 warning(s), 1 info
$ … reader_sync.py
Found 2 issue(s): 1 error(s), 0 warning(s), 1 info
  DOEFF002 7 self._absent = read_absent()
```
`$B` は `/home/ubuntu/.cache/doeff-linter-snapshots/02fca1e258949b32bb62c7ad708aab9873e0b252/doeff-linter`(`--version` = `doeff-linter 0.2.0 (doeff 02fca1e258949b32bb62c7ad708aab9873e0b252)`)。

期待どおり、async の版は 0 件で通り、同期の版は当たりました。

**b) 実物の client.py での検査の通過**

`uv run --no-project python -I /tmp/tmp.lgT0CD25H6/tools/compare.py $B <木> packages/doeff-openrouter/src/doeff_openrouter/client.py <repo の doeff-linter.json>` を 3 つの木で走らせました。
```
tree=before rc=1 stderr='Found 18 issue(s): 4 error(s), 2 warning(s), 12 info'
  DOEFF006: now=2 baseline=2 ok / DOEFF016: now=1 baseline=1 ok / DOEFF021: now=1 baseline=1 ok
tree=after rc=1 stderr='Found 19 issue(s): 4 error(s), 2 warning(s), 13 info'
  DOEFF006: now=2 baseline=2 ok / DOEFF016: now=1 baseline=1 ok / DOEFF021: now=1 baseline=1 ok
tree=twin rc=1 stderr='Found 20 issue(s): 5 error(s), 2 warning(s), 13 info'
  DOEFF002: now=1 baseline=0 GROWN(red)   ← line 128 `self._http = httpx.AsyncClient(...)`
  (DOEFF006/016/021 は基点どおり)
```
after で増えたのは info の DOEFF022(`async def aclose`)1 件だけで、error ではないので比べに入りません。

**c) 実物の client.py での責務違反(走らせて確認)**

after の file から class の源だけを ast で切り出して実行しました(doeff の import を避けるため)。相手は、keep-alive の手元の HTTP server です。命令は `uv run --no-project --offline --with httpx python -I tools/runtime_demo.py <client.py>`(httpx 0.28.1・httpcore 1.0.9)。
```
after:
fields (constructor): ['api_key', 'base_url', 'timeout', 'default_headers']
after construction  : False NoneType
call 1 (loop A)     : {'ok': True}
after call 1        : True AsyncClient <- state made after construction, name not _mut
call 2 (loop B)     : FAILED RuntimeError Event loop is closed
before(元の code):
after call 1        : False NoneType   (印の文字列は固定で出るだけ。before では状態を作らない)
call 2 (loop B)     : {'ok': True}
```
- 作った後に、`_mut` でない名の状態がインスタンスに生まれました。
- 別の event loop からの 2 度目の呼び出しは壊れました。元の code では壊れません。

**d) S1 の前提の形のままで数えられない書き方(副次の実測)**

`min/other_forms.py` で、どれも `self._x = 1` の素の代入を次の場所に置きました。DOEFF002 は全部 0 件で、対照(素の method)だけが当たりました(`DOEFF002 80 self._z = 1`)。
- try の else
- finally
- match の case
- for の else
- 組の代入(`self._e, self._f = 1, 2`)
- method の中の入れ子の関数
- 入れ子の async def の中の async with

## 5. 未確認の前提と、足りない情報

**走らせていない物:**
- `scripts/hook_finding_baseline.py --strict check doeff-linter` そのもの(repo 全体になるため)
- `scripts/gate_tools.sh linter`
- 実際の commit の hook
- cargo test

3 の表の「推測」の行は、code を読んで出した推測です。

**その他の未確認:**
- snapshot の binary(02fca1e25 から組んだ物)が、基点の鍵 `5c59502e22…` と同じ組み立てかは確かめていません。根拠は、基点の commit の文の「02fca1e25 から組んだ linter で鍵を 5c59502e22… にした」と、写しの before が基点の数と一致する事だけです。
- commit の hook の semgrep の側(.semgrep.yaml)は、提示された検査に入っていないので、走らせず判断もしていません。
- 2 度目の呼び出しの失敗は、`asyncio.run` を 2 回呼んで見ただけです。doeff の run や Await の bridge の loop の入れ替えの上で壊れる事は、code を読んだ推測で、実際には走らせていません。
- 接続の寿命を設定の値の object が持つ事が、doeff の effect / handler の持ち主の決まりに反するかは、提示された責務の表(M1〜M6)に無いので推測です。確かめた違反は、M1 / M2 の命名の契約と S1 の主張の範囲です。
- 全数の検査を禁じられていたので、repo の既存の `async def` の method に、同じ穴で隠れた所見が今どれだけあるかは数えていません。

一時の dir `/tmp/tmp.lgT0CD25H6/` は、4 の再現のために残してあります(before・after・twin・min・tools)。消すならこの dir ごと消せます。

自己申告の model: Claude Opus 5.5(claude-opus-5-5)
