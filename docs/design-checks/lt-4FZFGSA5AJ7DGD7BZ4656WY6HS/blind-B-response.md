# 盲検 B の返答(未加工)

- 起動: Agent tool(subagent_type general-purpose・新しい文脈・fork / resume なし・A と同じ返事の中で同時に起動し、互いの返答を見ていない)・要求 model `opus`(claude-opus-5-5)・effort `xhigh`
- 識別子: agentId a7e0d383bd985e9cb・所要 965155 ms・tool の呼び 56 回
- 機体: doeff-worker-zeus-5d6d78459d-zl8pw(親と同じ Pod)・profile: 親と同じ agora の会話の資格(子へ別の資格は渡していない)
- 優先 1 の gpt-6-astra(low)は、この環境の subagent の起動手段(Agent tool の model は sonnet / opus / haiku / fable)で起こせないため、優先 2 の opus(xhigh)で起こした。実際に応答した model の名は観測できていない(要求値だけ)。
- 入力: blind-input.md(sha256 9e29e210d34b45e36a86bbeafb5225c45ea8bc64bd7525694a4adbd753904385)と、skill の参照文書の B の依頼文(本文へ直に貼った)・写しの木で試す注を 1 行足した
- 起動の刻: 2026-10-11T16:23Z 頃(A と同時)

---- 返答 ----

## 結論

ご依頼の失敗例を 1 つ作り、提示の検をすべて通しました。題材は S1(層 2 の fake に、利用枠切れで失敗するターンを出す欄を足す)の、ふつうの実装です。

その結果、adapter が持つ窓の名の表(窓の長さ → 名)の逆の表(名 → 長さ)が doeff-codex の fake に入ります。S2 の主張「窓の名の表は adapter の 1 か所で、codex-fake は変えない」が崩れます。ただし影響は検と模擬の側だけで、本番の挙動は変わりません。

作業木は書き換えていません(`git status` は元から在る `.scratch/` だけ)。試したのは `/tmp/cx-s1fake-ESu72L` に写した木で、そこで `uv sync --frozen` して作った環境を使いました。

## 1. 機能要求と差分

**要求**: agora の模擬の筋書き `HitLimit`(`:window "five_hour"/"seven_day"/None :resets-at :text`、agora-controllers の `controllers/agora_sim/peers/agent_cli_account_ends.hy`)を、codex の会話にも流したい。

この要求が自然だと考える根拠は 2 つです。
- agora は既に `doeff_codex.fake [FakeReply]` を import しています(`controllers/agora_sim/tests/test_codex_turn_on_agent_runtime.hy:21`)。
- claude の fake は窓の名で `FakeReply.account-limit` を受けており(doeff-claude-code `fake.hy:154`)、これが手本になります。

**差分**(全文は `/tmp/cx-s1fake-probe/s1.diff`。`fake.pyi` は道具で作り直した物):

```hy
;; packages/doeff-codex/src/doeff_codex/fake.hy
(val USAGE-LIMIT-WINDOW-MINUTES {"five_hour" 300 "seven_day" 10080})
(val USAGE-LIMIT-KIND "usageLimitExceeded")
(defrecord FakeReply ...
  (setv #^ (| str None) usage-limit-text None)
  (setv #^ (| str None) usage-limit-window None)
  (setv #^ (| int None) usage-limit-resets-at None))
(defk usage-limit-records [thread-id turn-id reply] ...
  ;; 名 → 長さを引き、used-percent 100 の窓を five_hour なら primary・ほかは secondary に置く
  ;; → RateLimits → TurnError → TurnEnded(FAILED, usageLimitExceeded)
  ...)
;; fake-start: usage-limit-text が在れば、上の記録を積んで答えを出さずに返す
```

検の側では `test_headless_codex_adapter.hy` を変えました。`respond` に USAGE-LIMIT の筋書き(`:usage-limit-window "five_hour"`)を足し、fake と public-fake の 2 本の deftest で既存の `check-usage-limit` を通します。

## 2. 本来の持ち主と、破られる契約

- **窓の名の表の持ち主**: codex-adapter です(`headless_codex.hy:82` の `WINDOW-NAMES`。責務の表の「持つ知識」と S2)。差分は、その逆の表を codex-fake(doeff-codex)に置きます。
- **fake の作り方**: fake は、adapter の `account-limit-of` が狙った答えを返すように記録を作ります。つまり adapter の判定の逆を書いています。
- **層の向き**: 窓の名 `five_hour`・`seven_day` は doeff-agents の公開型 `AgentAccountLimit.window` の語(claude の綴り)です。差分の後は、層 2 の package がその語を話します(要件 3 の層の向きと逆)。
- **主張への影響**: adapter の「内部で自由」なはずの窓の名の表が、事実上 package をまたぐ契約になります。S2 の「変えない module: codex-fake」が成り立たなくなります。
- **違反ではない部分**: fake が書く綴り `usageLimitExceeded` は codex の綴りで、S4 も codex-fake を「変えない」側に挙げていません。used-percent 100 も codex のデータの形です。

## 3. 提示の各検が拒まない理由(すべて実測)

| 検 | 結果 | 拒まない理由 |
|---|---|---|
| defwire / defrecord | 拒まない | fake は lines の記録を正しい型で作るだけで、JSON は解きません。窓の名はただの `str` です |
| defk の `:pre` / `:post` | 拒まない | 新しい defk も `fake-start` も型の条件を満たします |
| test_lines.hy | 9 passed | lines.hy は変えておらず、fake を読みません |
| test_headless_codex_adapter.hy | 24 passed(新しい 2 本を含む) | fake の表が adapter の表のちょうど逆なので、出力は一致します。検は出力の等しさを見るだけで、知識がどこに在るかは見ません |
| test_headless_codex_account_limit.hy | 4 passed | 判定の関数を直に呼び、fake を通りません |
| test_generated_stubs.py | 2 passed | `.pyi` は道具で作り直しました。pyright の検も skip されずに通りました |
| semgrep-hy(fake.hy と検の file) | 15 規則・0 件 | 語の禁止の一覧は agora・kanban などだけで、five_hour は含みません。表の重複を見る規則もありません |
| semgrep-other-files(fake.pyi) | 31 規則・0 件 | 同上 |
| semgrep(Python の hook) | 対象外 | `.pyi` は identify の tag が `['pyi','text']` で、python の型を持ちません。`.py` は変えていません |
| agora の境界の検 | 対象外 | agora の code に触れていません |

semgrep は .git の無い写しの木で回したので、規則の path の指定が効くかを確かめました。`packages/doeff-codex/` の下に「agora」と書いた検体を置くと、語の禁止の規則が 1 件当たりました。

## 4. 最小の再現

1. `D=$(mktemp -d /tmp/cx-XXXX)` で空の dir を作り、`git -C <作業木> archive HEAD | tar -x -C $D` で写し、`cd $D && uv sync --frozen` で環境を作ります。
2. `s1.diff` を当て、`uv run --no-sync python -m doeff_hy.static_stub --write packages/doeff-codex/src/doeff_codex/fake.hy` で `.pyi` を作り直します。
3. 提示の 4 本の pytest と semgrep の hook の命令を 1 回ずつ走らせます。期待する結果は上の表のとおり全部緑です。
4. **知識が 2 か所に在ること**: `grep -n five_hour` で、adapter の `WINDOW-NAMES` と fake の `USAGE-LIMIT-WINDOW-MINUTES` の両方に当たります。
5. **S2 を主張どおり adapter だけに入れる**: `(val WINDOW-NAMES {300 "five_hour" 1440 "one_day" 10080 "seven_day"})` にしてから `uv run --no-sync python /tmp/cx-s1fake-probe/probe_s2.py` を走らせました。実測の結果は次のとおりです。
   - adapter は 1440 分の窓を `AgentAccountLimit(window='one_day', resets_at=1893456000, ...)` にします。
   - 同じ筋書きを fake で書く(`usage_limit_window="one_day"`)と `KeyError: 'one_day'` で落ちます。
   - したがって S2 は codex-fake を直さないと終わりません。

## 5. 未確認の事と推測

- **実測と違う点**: 作業木ではなく写しの木で、写しの木の環境から回しました。pre-commit そのものは回しておらず(写しの木に .git が無い)、hook の entry の命令を同じ引数で直に走らせました。
- **推測(未実行)**: S2 を入れた後に提示の検を回し直してはいません。1440 や one_day を使う検が無いので、緑のままだと推測しています。
- **推測(未実行)**: S2 が窓の長さを変える形(例: five_hour を 360 分に)なら、今回足した fake の検が赤になり、やはり fake を直すことになるはずです。
- **推測**: agora の模擬の書き手がこの API を選ぶかどうか。根拠は 1 節に挙げた `HitLimit` の形、claude の fake の手本、agora の import の 3 つです。
- **前提**: 「替え玉は上の層の語を持たない」は責務の表の読み方として扱いました(codex-fake の持つ知識は「筋書き」だけ)。設計の意図として替え玉が adapter の写しを持ってよいなら、違反ではなく S2 の「変えない module」の表を直す話になります。
- **安全という結論ではありません**: ほかの候補として、adapter がターンをまたいで枠の知らせを持ち越す形(S3)と、lines が綴りを揃える形(S4)も検討しました。agora の使い方での害の根拠が弱いため、どちらも試していません。

## 置いた file

- `/tmp/cx-s1fake-ESu72L`(S1 の差分を当てた写しの木。S2 の試しは元に戻し済み)
- `/tmp/cx-s1fake-probe/s1.diff`
- `/tmp/cx-s1fake-probe/probe_s2.py`
