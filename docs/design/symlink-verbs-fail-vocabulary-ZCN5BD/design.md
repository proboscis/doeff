# symlink を据える 2 つの関数に、失敗の語彙が無い

- 調査: 計画段の会話 `c-ZCN5BD6XR7GCT960ZH6QJGX98M`(依頼 `lt-B9RTA0JDQ81KV9B7RJ84QFS6G7` + 射程を広げる申し送り `lt-9V6CJG6XP502YWDDXMNERY3P1Q`・依頼者 `c-AJ8C0BK9RF29HQ92ZQ986FXQVT`)
- 検体: 本線 `7451fa17`(調査の出発点 `ef0eaa55` の 2 つ先。`substrate.hy` に差は無い)
- 実測機: 会社 Mac CA-20038667 / Darwin 25.5.0(APFS)/ python 3.14.3
- 先行: card `acp:kanban-issue:ki-62aa1f4e9c9c` の決定 D8(`docs/design/seat-home-common-instructions-AJ8C0B/`)

## 1. 何が問題か

`packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy` には、symlink を据える関数が
2 つある。どちらも「例外を投げない・3 値(または 4 値)のどれかを必ず返す」と約束している。

| 関数 | 約束している結末 | 約束の文言 |
| --- | --- | --- |
| `ensure-symlink-outcome`(301 行) | `linked` / `unchanged` / `occupied-by-real-entity` | 「3 値を返す(raise しない — 方針判断は呼び手所有)」 |
| `FsLinkArtifact`(537 行・`real-substrate` の中) | `source-missing` / `linked` / `same-entity` / `target-conflict` | 「方針判断は呼び手所有 — substrate は観測結果の 4 値を返すだけ」 |

**この語彙には、ファイルシステムが据え付けを断った場合が 1 つも無い。** 断りは必ず起きる
(権限・容量・読み取り専用・同時実行の衝突)。語彙に無いので、断りは 2 つの出口しか持たない。

- **(a) 素の `OSError` が呼び出し元まで抜ける** — 約束が破れる。セッションが起動しない。
- **(b) 正常系の値に化ける** — 誤った報告になる。人が存在しないものを探す。

これは呼び出し側にガードを足しても閉じない。**関数の戻り値の型が持つ穴**である。
コード品質方針(「失敗を型で明示する」「状態は代数的データ型で表す」)の違反でもある。

### 1.1 依頼者の見立てとの差

依頼書は当初「`FsLinkArtifact` の同時実行の隙」という 1 関数の話だった。申し送りで
「根は 1 段上 = 失敗の語彙が無いこと」に言い直された。本調査はその言い直しを支持し、
**さらに 1 つ足す**: 依頼者は「器が断る問題」を `ensure-symlink-outcome` だけの話と書いたが、
実測では **`FsLinkArtifact` にも同じ 3 形すべてが在る**(下の 2.2)。また依頼者が提案した
直し方(`FileExistsError` を合図に読み直す)は**同時実行だけを閉じ、器の断りは閉じない**
ことも実測で確かめた。したがって 2 つの関数に同じ直しが要る。

## 2. 実測

再現用: `evidence/link_artifact_doors.py`(出力 `.log`)・`evidence/rename_errno.py`(同)。
`ROUNDS` 環境変数で回数を変えられる。既定 200 回。

### 2.1 同時実行 — 同じ敷設先へ 2 プロセスが同時に降りる

実 `real-substrate` ハンドラを駆動して `FsLinkArtifact` を撃つ。共有メモリのスピン待ちで
拍を揃え、ロックは 1 つも取らない(プロセスをまたぐのでプロセス内のロックは届かない)。

2 回走らせた(同じ機体・同じ検体)。

| 実装 | 走行 | `linked` | `same-entity` | `RAISED:FileExistsError` | 敷設先が正しくない回 |
| --- | --- | --- | --- | --- | --- |
| **出荷中** | 1 回目 | 200 | 16 | **184** | 0 |
| **出荷中** | 2 回目 | 200 | 4 | **196** | 0 |
| 試作(下の 3.4) | 1 回目 | 200 | 200 | **0** | 0 |
| 試作(下の 3.4) | 2 回目 | 200 | 200 | **0** | 0 |

出荷中の実装では、**200 ラウンドのうち 184〜196 ラウンド(92〜98 %)で負けた側が例外で落ちる**。
(`.log` は 2 回目。1 回目の値はこの表にだけ残した。例外の生の出力は `.stderr.log`。)
`os.path.exists` / `os.path.islink` で見てから素の `os.symlink` を撃つ 2 手のため、
見た後・張る前に相手が張ると `FileExistsError` が effect の外まで抜ける。

### 2.2 器の断り — 3 つの形すべてで例外が抜ける

| 家の形 | `ensure-symlink-outcome`(依頼者の実測) | `FsLinkArtifact`(本調査の実測) |
| --- | --- | --- |
| 親の位置に実体ファイルが居る | `FileExistsError`(`os.makedirs`) | `FileExistsError`(`os.makedirs`) |
| 家が書けない(`r-x`) | `PermissionError`(`os.symlink`) | `PermissionError`(`os.symlink`) |
| 親ディレクトリを作れない | `PermissionError`(`os.makedirs`) | `PermissionError`(`os.makedirs`) |

`FsLinkArtifact` は `try` を 1 つも持たない(`samefile` の周りを除く)ので、当然すべて抜ける。
**試作(同時実行の直しだけ)でも 3 形とも抜けたまま**だった — これが「同時実行の直しでは
足りない」の実測である。容量切れ(`ENOSPC`)・読み取り専用のディレクトリでも同じ。

### 2.3 `os.replace` が返す errno(扉 2 の直しの前提)

`ensure-symlink-outcome` は `os.replace` の `except OSError` を無型で受け、すべて
「実体が居る」と名乗る。errno で割れるかを測った。

| 据わっている物 | 結果 |
| --- | --- |
| 空のディレクトリ | `IsADirectoryError` errno=21 `EISDIR` |
| 中身入りのディレクトリ | `IsADirectoryError` errno=21 `EISDIR` |
| 実体ファイル | **成功(黙って置き換わる)** — 既知の残存窓 |
| 別の先を指す symlink | 成功(意図どおり) |

`EISDIR` は POSIX が `rename()` に定めた errno(新しい側がディレクトリで古い側がそうでない
場合)なので、Darwin 固有ではない。**`EISDIR` だけを「実体が居る」に写し、残りを
「器が断った」に写せばよい。**

### 2.4 同時実行が現実に届く敷設先は 2 つ(1 つは本調査が新たに見つけた)

`FsLinkArtifact` の呼び出しは 6 か所。敷設先に会話 ID が入っていれば、同じ会話の 2 重起動は
別の仕組みが禁じているので競合しない。**入っていない敷設先が 2 つある。**

1. `impls/claude_code.hy:740` — `{target-project}/sessions-index.json`(依頼者の指摘)。
   `target-project` = `{binding の config_dir}/projects/{作業ディレクトリを変換した名}`。
   家は資格ごとに鋳られ、どの資格を借りるかは手番ごとに変わるので、**同じ資格・同じ作業
   ディレクトリの 2 会話が同時に別の家から移植する**とここが同じパスを争う。
2. `launch.hy:363` — `{workspaces-root}/{sibling-name}`(**本調査が読みで見つけた**)。
   `workspaces-root` = `dirname(seed.dir)` で、同じ親の下に複数の worktree を作る運用では
   **共有される**。`link_siblings` を持つ新しい seed が 2 つ同時に降りると同じパスを争う。
   ここでは負けた側が `FileExistsError` でセッションの起動そのものを落とす。
   ⚠ 読みで確かめただけで、同時実行の実射はしていない。

### 2.5 到達しない扉も 2 つ(記録のため)

- `os.makedirs(os.path.dirname(target-path))` は、敷設先が裸のファイル名だと
  `os.makedirs("")` になり `FileNotFoundError`(実測)。現在の呼び出しはすべて絶対パスなので
  到達しない。`ensure-symlink-outcome` 側には `(when parent …)` のガードが在るので非対称。
- 依頼者の「扉 2(偽 `occupied`)」— 関数の中に到達路は見つかっていない(依頼者の調査)。
  語彙の誤りとして扉 1 と一緒に閉じる。**到達路の探索に時間を使わないこと。**

## 3. 設計

### 3.1 決定 1 — 戻り値を「状態 + 理由」のレコードにする(戻せる決定)

両方の関数が同じ frozen dataclass を返す。

```
FsSymlinkOutcome
  state   : str          閉じた語彙(下の 3.2)
  errno   : int | None   器が断った時と、安全側に倒した時だけ非 None
  detail  : str | None   人が読む 1 行(どの syscall が何と言ったか)
```

`__str__` は `state`(`detail` が在れば `state (detail)`)を返す。ログの
`f"outcome={outcome}"` はそのまま読め、理由が在る時だけ理由が載る。

**なぜ 5 つ目の文字列だけでは足りないか。** 呼び出し側が本当に要るのは理由である。
`ENOSPC` と `EACCES` と `EROFS` は、運用者が取る手がまったく違う。理由の無い
「器が断った」は、扉 2 が作った「在りもしない実ファイルを探せ」という無益な文言を、
別の無益な文言に置き換えるだけになる。

**なぜ例外(typed exception)にしないか。** それは今まさに起きている壊れ方そのもの
(effect の外へ例外が抜けてセッションが起動しない)であり、受け損ねた 1 か所が同じ事故を
再生産する。D8 の設計の前提「この関数は raise しない」も覆る。

**移行の安全。** 旧来の `(= outcome "linked")` がレコード相手に**黙って False** になるのが
唯一の危険。2 つの手当てで声を出させる。

- 旧定数 `FS-ENSURE-SYMLINK-*` を消す ⇒ 参照する 2 ファイルが import で落ちる。
- `FsSymlinkOutcome.__eq__` は文字列と比べられたら `TypeError` を投げる。レコードと文字列の
  比較は常に誤りなので、これは恒久的に正しい(型逃げの逆)。

**wire との境界。** `{"ok" True "action" outcome}` の `action` はレコードではなく
`(. outcome state)` を入れる。レコードはホストの中だけに置く。
(調査の結果 `action` は現在どこからも読まれていないが、JSON 化できない値を辞書に
入れる形は残さない。)

**戻し方**: レコードをやめて文字列に戻し、`errno` / `detail` を捨てる。影響は 6 呼び出し
座と 2 つの偽ハンドラ。

### 3.2 決定 2 — 語彙(戻せる決定)

状態の綴りは `effects.hy` の 1 か所に定数で置く(現在 `FsLinkArtifact` の 4 語は
呼び出し側に文字列リテラルで散っている — 閉じた語彙の第 2 定義点)。

```
FS-SYMLINK-LINKED           "linked"
FS-SYMLINK-UNCHANGED        "unchanged"
FS-SYMLINK-OCCUPIED         "occupied-by-real-entity"
FS-SYMLINK-SOURCE-MISSING   "source-missing"
FS-SYMLINK-SAME-ENTITY      "same-entity"
FS-SYMLINK-TARGET-CONFLICT  "target-conflict"
FS-SYMLINK-REFUSED          "refused-by-container"   ← 新しい 1 語
```

関数ごとに「返しうる状態の集合」も定数で宣言し、テストが集合の外を許さない。

- `ensure-symlink-outcome` → `linked` / `unchanged` / `occupied-by-real-entity` / `refused-by-container`
- `FsLinkArtifact` → `source-missing` / `linked` / `same-entity` / `target-conflict` / `refused-by-container`

**既存 6 語の意味は 1 ビットも変えない。** 足すのは 1 語だけ。

### 3.3 決定 3 — `ensure-symlink-outcome` の直し(戻せる決定)

- `os.makedirs` と `os.symlink` を `try` の中へ入れ、`OSError` を `refused-by-container` に写す。
- `os.replace` の `except OSError` を errno で割る。`EISDIR`(念のため `ENOTEMPTY` / `EEXIST` も)
  は `occupied-by-real-entity`、残りは `refused-by-container`。実測表 = 2.3。
- `os.readlink` の `except OSError` は今のまま(据え付けで決め直すので正しい)。
- `_ensure-view-symlink` は `occupied` だけを今の文言の `RuntimeError` に写す。
  `refused-by-container` は**別の文言**の `RuntimeError`(「器が据え付けを断った: <detail>」)。
  これで「在りもしない実ファイルを手で片付けろ」という誤った案内が構造的に消える。

### 3.4 決定 4 — `FsLinkArtifact` の直し(戻せる決定)

この関数の約束は「据わっている物を絶対に置き換えない」なので、原子的な 1 手は `rename` では
なく `os.symlink` そのもの。**見てから張るを判断の座にせず**、`FileExistsError` を
「見た後に何かが現れた」の合図として受け、そこで改めて `samefile` を読んで
`same-entity` か `target-conflict` を返す。**語彙を 1 つも足さずに閉じる**(実測 2.1)。

```
source が実体でも symlink でもない        → source-missing
敷設先が既に在る                          → samefile を読んで same-entity / target-conflict
親を作って symlink を張る
  成功                                     → linked
  FileExistsError                          → samefile を読み直して same-entity / target-conflict
  それ以外の OSError                       → refused-by-container(+ errno / detail)
```

`samefile` が `OSError` になった時の扱いは**今のまま `target-conflict` に倒す**
(据わっている物を触らないという約束は、観測できない時こそ守る側に倒すのが安全)。
ただし `detail` に理由を載せる ⇒ 語彙は安全側・報告は正直、が両立する。これがレコードに
した効き所の 1 つ。

### 3.5 決定 5 — 呼び出し側の方針(戻せる決定)

| 呼び出し座 | `refused-by-container` をどう扱うか | 理由 |
| --- | --- | --- |
| `impls/claude_code.hy:532`(dir-link の据え付け) | **ログ 1 行のまま**(今の 3 値と同じ扱い) | 既に `occupied` が「セッションは起動するが skills は入らない」で出荷済み。容量切れ・読み取り専用ならセッションの起動は別の場所でも失敗する見込みが高く、ここで先に落とすと診断が遠のく。ログが理由を運ぶようになるので「黙って skills を入れない」は消える |
| `impls/claude_code.hy:728`(必須の transcript) | **typed reject** | 起動してから実 CLI が 120 秒かけて死ぬのを前倒しする、という既存の設計思想に合う |
| `impls/codex.hy:422`(必須の rollout) | **typed reject** | 同上 |
| `impls/claude_code.hy:740/742/744`(周辺 3 対) | 値を捨てるまま | best-effort と明記されている |
| `launch.hy:363`(sibling の鏡) | 既存の `raise` にそのまま流れる | `{linked, same-entity}` 以外は loud、という既存の方針のまま。文言に理由が載る |

typed reject のコードは `RESUME-ERR-TRANSPLANT-REFUSED "transplant_refused"` を 1 つ足す。
既存の 5 語(`transcript_not_discoverable` ほか)はどれも意味が合わない
(「見つからない」ではなく「据え付けを断られた」)。

⚠ `ADR-DOE-AGENTS-006` の R7 が `FsLinkArtifact` の 4 値を逐語で宣言しているので、
**同じ変更に ADR 本文の改訂を含める**(法・反例・テスト・実装の一括出荷)。

**戻し方**: `claude_code.hy:532` を loud にしたくなったら、その 1 座に分岐を 1 つ足す。
逆に typed reject をやめたければ `RESUME-ERR-TRANSPLANT-REFUSED` を消して値を捨てる。

### 3.6 決定 6 — 検査(ゲート)の射程(戻せる決定)

依頼者の 2 つの問いに答える。

**(a) shell の `ln -s` を semgrep の綴りに足すか → 足す。**
既存の規則 `doeff-agents-symlink-install-has-one-home` は `os\.symlink` と `\.symlink_to` の
Python の綴りしか見ていない。BSD の `ln -sfn` は原子的でない(依頼者の Darwin 実測:
読み 80,733 のうち `ENOENT` 4,531 = 5.64 %)ので、規則の文言が禁じている物理の中で最悪。
対象ディレクトリ配下に `ln -s` は現在 **0 件**(確認済み)なので、`pattern-either` に 1 行
足すのは無害。対象ディレクトリは広げない(検体側が意図的に symlink を張るため)。

**(b) 検査を「関数の単位」へ動かすか → 動かす。ただし semgrep ではなく pytest で。**
いまの semgrep は `**/sessionhost/substrate.hy` を**ファイル単位で除外**して出荷側の 1 点を
通している。つまり「このファイルの中は自由」という意味で、同じファイルに 3 つ目の
手書き実装が生えても緑のまま。実際この調査の発端がそれだった。

semgrep の generic モードでは Hy の関数の範囲を表現できない。代わりに **Hy 自身のリーダで
`substrate.hy` を解析する pytest を 1 本足す**。試作で実証済み
(`evidence/verb_scope_needle_prototype.py`・出力 `.log`):

```
-- 許した関数の本体の範囲(構文木から) --
  (deff ensure-symlink-outcome) 行 301-362
  (FsLinkArtifact )             行 537-557
-- 禁じた綴りの出現 --
  行  353  os.symlink   → 許した関数 ensure-symlink-outcome の中
  行  555  os.symlink   → 許した関数 FsLinkArtifact の中
-- 判定: 関数の外の出現 = 0 件(0 なら緑)--
```

⚠ Hy のリーダは `os.symlink` を `(. os symlink)` に割るので、綴りの grep ではなく
**構文木の形**で見分ける必要がある(試作はそうしている)。

**許す綴りは 1 つではなく 2 つ**になる(実装の会話 `c-EWR4R2XY…` の申し送りのとおり)。
「置き換える据え付け」(`ensure-symlink-outcome` = 一意な仮 symlink + `rename`)と
「置き換えない敷設」(`FsLinkArtifact` = `os.symlink` を撃って `FileExistsError` を読み直す)は
物理が逆で、1 つの正しい形には畳めない。検査の文言もそう書く。

semgrep のファイル単位の除外は**維持する**(ファイルの外は 1 つも許さない、という別の役目)。
既存の `tests/semgrep/test_symlink_install_rule.py` の 2 本目(「出荷側のファイルには綴りが
現に在り、除外がそれを通している」)もそのまま。

**戻し方**: 構造検査の pytest を消す。semgrep の 1 行を消す。

### 3.7 決定 7 — 扉 3(綴りの正規化)はこの変更に入れない(戻せる決定)

依頼者の実測で「張り替えは綴りが変わった 1 度きり・毎起動ではない」と分かっており、
実害は小さい。正規化を据える場所は `join` がパスを組む拍 = policy / launch 側で、この変更の
射程(substrate の 2 関数)とは別の層。射程を混ぜると一括出荷の芯がぼける。
⇒ **別の card を起こす**(担当: この計画段の会話)。

## 4. 影響範囲

| ファイル | 変更 |
| --- | --- |
| `sessionhost/effects.hy` | `FsSymlinkOutcome` の定義・状態定数 7 つ・関数ごとの状態集合 2 つ・`RESUME-ERR-TRANSPLANT-REFUSED`・`FsEnsureSymlink` と `FsLinkArtifact` の docstring 改訂・旧 `FS-ENSURE-SYMLINK-*` の削除 |
| `sessionhost/effects.pyi` | 上に対応する型 |
| `sessionhost/substrate.hy` | `ensure-symlink-outcome`(3.3)・`_ensure-view-symlink`(3.3)・`FsLinkArtifact`(3.4)・import の入れ替え |
| `sessionhost/impls/claude_code.hy` | 532(ログ)・728(typed reject)・740/742/744(そのまま) |
| `sessionhost/impls/codex.hy` | 422(typed reject) |
| `sessionhost/launch.hy` | 363(文言に理由を載せる) |
| `tests/sessionhost_substrate_deftests.hy` | 既存 3 本の更新 + 新しい検査(受入 1〜3) |
| `tests/sessionhost_launch_deftests.hy` | 偽ハンドラ 2 つが同じレコードを返す |
| `tests/sessionhost_resume_cross_binding_deftests.hy` | typed reject の検査 |
| `tests/semgrep/…`(規則・検査・検体) | 3.6 (a) |
| 新規: 構造検査の pytest | 3.6 (b) |
| `docs/adr/defadr_doeff_agents_006_conversation_resume_fork.hy` | R7 の 4 値 → 5 値 |

**結合核: 該当なし。** dotfiles の機械可読な名簿 2 節(98 + 1 パターン)と突き合わせて
当たり 0 件(`evidence/coupling_core_match.{py,log}`)。

**既知の形**: 既存の形の中の修理で、概念・状態・境界・仕組みを足さない。形 = CI runner
(agentd = runner・席の家の据え付けはジョブごとの注入)。規律 (i)「手番の終わりに終端の
状態を必ず返す」に**近づく**方向の直しで、逆行する点は無い。足すのは失敗の語彙 1 語と、
その理由を運ぶ型(新しい概念ではなく既存の結末の型付け)。

**執行台帳** `docs/adr/enforcement-ledger.json` は数の歯止め。semgrep 規則は既存規則への
1 行追加なので数は変わらない(269 のまま)。ADR の法を足す場合だけ `adr_laws` を更新する。

## 5. 受入(実装の依頼書と同じ)

1. 同じ敷設先へ 2 プロセスが同時に `FsLinkArtifact` を撃って **例外 0・語彙の外 0**(200 回 × 2)。
2. 器の断り 3 形で **両方の関数**が `refused-by-container` + errno を返し、例外 0。
3. `os.replace` が `EISDIR` の時だけ `occupied-by-real-entity`、他は `refused-by-container`。
4. `substrate.hy` の中で `os.symlink` / `.symlink_to` が許した 2 関数の外に 0 件(構造検査)。
   対象ディレクトリ配下の `ln -s` も 0 件(semgrep)。
5. 既存 6 語の意味が変わっていない(既存の検査が緑)。
6. 偽ハンドラが実物と同じレコードを返す。

## 6. 検査の走らせ方(全数は走らせない)

```
uv run pytest -q packages/doeff-agents/tests/test_sessionhost_substrate.py -k symlink
```

実測 2.55 秒 / 3 本(この worktree・変更前)。`.hy` は直接収集されない — 入口は同名の
`test_*.py` の薄い包み。全数は約 36 分なので走らせない(1 日 1 回の定期検証が担う)。
