# 実装の依頼書 — symlink を据える 2 つの関数に失敗の語彙を入れる

- 依頼者: 計画段の会話 `c-ZCN5BD6XR7GCT960ZH6QJGX98M`(この文書の著者)
- 発端: 依頼 `lt-B9RTA0JDQ81KV9B7RJ84QFS6G7` + 射程を広げる申し送り `lt-9V6CJG6XP502YWDDXMNERY3P1Q`
  (依頼者 `c-AJ8C0BK9RF29HQ92ZQ986FXQVT`)
- リポジトリ: `doeff`(`~/repos/doeff`)。基点 = 本線 `7451fa17` 以降の origin/main
- 設計の正本: 同じディレクトリの `design.md`。**先にこれを読むこと。**
  実測の計器と生の出力は `evidence/` に在り、`ROUNDS` を変えて再実行できる。
- 結合核: **該当なし**(名簿 98 + 1 パターンと突合して 0 件・`evidence/coupling_core_match.log`)
- 既知の形: 既存の形(CI runner — agentd = runner)の中の修理。概念・状態・境界・仕組みを
  足さない。規律「手番の終わりに終端の状態を必ず返す」に近づく方向で、逆行する点は無い。

## 0. 1 文で

`substrate.hy` の symlink を据える 2 つの関数は「例外を投げない・決まった値のどれかを返す」と
約束しているのに、ファイルシステムが断った場合の値を持っていない。**失敗を語彙に入れ、
理由(errno)を運ぶ型にする。** 併せて、同じ敷設先への同時実行で 92 % のラウンドで例外が
抜ける穴(実測)を閉じ、検査の射程をファイル単位から関数単位へ動かす。

## 1. 正本の読み口(どこが唯一の定義点か)

| 何 | 唯一の定義点 | 註 |
| --- | --- | --- |
| 状態の綴り(7 語) | `sessionhost/effects.hy` の定数 | 現在 `FsLinkArtifact` の 4 語は呼び出し側に文字列リテラルで散っている = 第 2 定義点。**これを畳むのが仕事の一部** |
| 関数ごとに返しうる状態の集合 | 同上(定数 2 つ) | 検査が集合の外を許さない |
| symlink を据える物理 | `substrate.hy` の 2 関数だけ | 物理が逆なので 1 つには畳めない(`design.md` 3.6) |
| 失敗の理由の運び方 | `FsSymlinkOutcome` の `errno` / `detail` | ログ・例外の文言・typed reject の message はすべてここから読む。第 2 の経路を作らない |
| 移植の拒否コード | `effects.hy` の `RESUME-ERR-*` | 1 語足す |

**旧側の値を運ぶこと。** 既存 6 語の綴りと意味は 1 ビットも変えない。ログに出る語も変えない
(`__str__` が `state` を返す。理由が在る時だけ `state (detail)` になる)。

## 2. 起動・停止・排水・同時性

- **同時性がこの依頼の本体。** 家(config ディレクトリ)は資格ごとに鋳られ、同じ資格の複数の
  会話が 1 つの家へ同じ拍で降りる(プールの入れ替えの直後は溜まった郵便が一斉に手番になる)。
  ロックは足さないこと — 家はプロセスをまたいで共有されるので、プロセス内のロックは届かない
  (D8 と同じ判断)。閉じ方は物理そのもの:
  - 「置き換える据え付け」= 一意な名の仮 symlink + `rename`(既に出荷済み・触らない)
  - 「置き換えない敷設」= `os.symlink` を撃って `FileExistsError` を合図に読み直す(この依頼)
- **起動**: 席の家の据え付けは「起こす拍」ちょうど。降りたプロセスの続きはこの経路を通らない。
- **停止・排水**: この依頼は停止・排水の経路に触らない。
- **プロセス内の並行**: `_compose-view-lock`(view ごとのロック)は現状のまま。これはプロセス内の
  スレッド用で、プロセスをまたぐ競合には効かない — 効かせようとしないこと。

## 3. やること

`design.md` の 3.1〜3.6 が設計の正本。以下は作業の目録。

### 3a. `effects.hy` / `effects.pyi`

1. `FsSymlinkOutcome`(frozen・kw-only の dataclass)を足す。欄 = `state: str` /
   `errno: int | None` / `detail: str | None`。
2. `__str__` = `state`(`detail` が在れば `f"{state} ({detail})"`)。
3. `__eq__` は文字列と比べられたら `TypeError`。理由をコメントに 1 行
   (「レコードと文字列の比較は常に誤り — 移行で黙って False になるのを防ぐ」)。
   frozen dataclass で独自の `__eq__` を持つには `eq=False` にして `__eq__` と `__hash__` を
   自分で書く必要がある。
4. 状態定数 7 つ(`design.md` 3.2 の表)。**旧 `FS-ENSURE-SYMLINK-UNCHANGED` /
   `-LINKED` / `-OCCUPIED` は消す**(参照元が import で落ちる = 移行の声)。
5. 関数ごとの状態集合の定数 2 つ。
6. `RESUME-ERR-TRANSPLANT-REFUSED "transplant_refused"`。
7. `FsEnsureSymlink` と `FsLinkArtifact` の docstring を新しい語彙に改訂。
   `FsLinkArtifact` の docstring は現在「4 値」と書いてあるので 5 値に。

### 3b. `substrate.hy`

1. `ensure-symlink-outcome`: `os.makedirs` と `os.symlink` を `try` の中へ。`os.replace` の
   `except OSError` を errno で割る(`EISDIR` / `ENOTEMPTY` / `EEXIST` → `occupied`、
   残り → `refused`)。仮 symlink の後始末は今と同じ(自分の仮だけ消す)。
2. `_ensure-view-symlink`: `occupied` は今の文言のまま `RuntimeError`。`refused-by-container` は
   **別の文言**の `RuntimeError`(器が断ったこと・`detail` をそのまま載せる・手で片付ける実体が
   在るとは言わない)。
3. `FsLinkArtifact`: `design.md` 3.4 の形に書き換える。`samefile` の `OSError` は今のまま
   `target-conflict` に倒し、`detail` に理由を載せる。`os.makedirs` の親が空文字のとき
   (`(when parent …)`)のガードも足す — `ensure-symlink-outcome` 側には在って、こちらには無い。
4. import を新しい定数に入れ替える。

### 3c. 呼び出し側

`design.md` 3.5 の表のとおり。**`{"ok" True "action" outcome}` の `action` には
`(. outcome state)` を入れる**(レコードを辞書に入れない)。

### 3d. 検査(ゲート)

1. `.semgrep.yaml` の `doeff-agents-symlink-install-has-one-home` の `pattern-either` に
   shell の綴りを 1 行(`ln\s+-s` 相当)。対象ディレクトリは広げない。
   検体 `tests/semgrep/fixtures/.../symlink_install_outside_substrate_forbidden.hy` と
   `tests/semgrep/test_symlink_install_rule.py` の行番号の期待値を併せて更新する。
2. **新しい構造検査を 1 本**(`design.md` 3.6 (b))。試作が
   `evidence/verb_scope_needle_prototype.py` に在り、そのまま骨として使える。
   置き場は `packages/doeff-agents/tests/` の `test_*.py`(`.hy` は直接収集されない)。
   - Hy のリーダ(`hy.reader.read_many`)で `substrate.hy` を読む
   - 許した 2 つの形の行範囲を構文木から取る
     (`(deff ensure-symlink-outcome …)` と `real-substrate` の中の `(FsLinkArtifact …)`)
   - `(. os symlink)` と `(.symlink_to …)` の出現がすべてその範囲の中に在ることを確かめる
   - ⚠ Hy は `os.symlink` を `(. os symlink)` に割る。綴りの grep では見つからない
   - 検査の文言に「許す綴りが 2 つなのは物理が逆だから」を書く

### 3e. 検査の同型性(偽ハンドラ)

`tests/sessionhost_launch_deftests.hy` の `FsEnsureSymlink` / `FsLinkArtifact` の台本版が、
実物と同じ `FsSymlinkOutcome` を返すようにする。台本の世界では器は断らないので
`errno` / `detail` は `None` でよいが、**型は同じ**にすること(同じ effect 契約の fake)。

### 3f. ADR

`docs/adr/defadr_doeff_agents_006_conversation_resume_fork.hy` の R7 は `FsLinkArtifact` の
4 値を逐語で宣言している。5 値に改訂し、`transplant_refused` を R7 の拒否語彙に足す。
**同じ変更セットに入れる**(法・反例・テスト・実装の一括出荷)。
`docs/adr/enforcement-ledger.json` は数の歯止め — ADR の法を足したら `adr_laws` を更新する
(semgrep は既存規則への 1 行追加なので `semgrep_rules` は 269 のまま)。

## 4. 受入

芯は 1 と 2。番号は報告でそのまま使うこと。

1. **同時実行**: 同じ敷設先へ 2 プロセスが同拍で `FsLinkArtifact` を撃ち、
   200 ラウンド × 2 = 400 呼び出しで **例外 0・語彙の外 0・敷設先が正しくない回 0**。
   検査は `tests/sessionhost_substrate_deftests.hy` の既存 2 本
   (`test-fs-ensure-symlink-survives-two-seats-landing-on-one-empty-home` ほか)と同じ骨
   (`os.fork` + 共有メモリのスピン待ち・ロックを取らない)。
   ⚠ 既存の骨をコピーせず、**共通の助け関数を使い回す**(`race-spin-barrier` /
   `race-child-installs` / `race-read-line` / `race-reap` は既に在る)。
2. **器の断り**: 親の位置に実体ファイル / 家が `r-x` / 親ディレクトリを作れない の 3 形で、
   **両方の関数**が `refused-by-container` を返し、`errno` が非 None、例外 0。
3. **`os.replace` の割り方**: 実体ディレクトリ(空・中身入り)は `occupied-by-real-entity`、
   それ以外の `OSError` は `refused-by-container`。
4. **検査の射程**: `substrate.hy` の中で `os.symlink` / `.symlink_to` が許した 2 関数の外に
   0 件。対象ディレクトリ配下の `ln -s` も 0 件。どちらも検査が現に赤くなることを、
   わざと違反する検体で確かめる(semgrep 側は既存の検体に、構造検査側はテストの中で
   一時的な文字列を食わせる形でよい)。
5. **既存の意味が変わっていない**: `test-fs-ensure-symlink-three-outcomes` と
   cross-binding の移植の検査が緑。ログに出る語が変わっていない
   (理由が在る時だけ `state (detail)` になる)。
6. **偽ハンドラの同型性**: 台本版が実物と同じ型を返す。

## 5. 検査の走らせ方(制約)

**全数は走らせないこと。** このリポジトリの全数は約 36 分で、1 日 1 回の定期検証が担う。
開発中に回すのは触ったファイルの焦点検査だけ・1 分以内。

```
uv run pytest -q packages/doeff-agents/tests/test_sessionhost_substrate.py -k symlink
uv run pytest -q packages/doeff-agents/tests/test_sessionhost_launch.py
uv run pytest -q packages/doeff-agents/tests/test_sessionhost_resume_cross_binding.py
uv run pytest -q tests/semgrep/test_symlink_install_rule.py
```

実測(変更前・この worktree): 1 本目は 2.55 秒 / 3 本。`.hy` のテストは直接収集されない —
入口は同名の `test_*.py` の薄い包み。周回ごとに型検査(pyright)を回さないこと。

変更したコードの型・lint・契約検査は実行して、違反したまま出荷しないこと(焦点の範囲で)。

## 6. 作業場

隔離 worktree で作業すること。置き場は `~/.worktrees/` の中だけ。
この調査の worktree(`~/.worktrees/doeff-wt-symlink-fail-vocab`・branch `wt/symlink-fail-vocab`)は
**設計文書と計器の置き場**なので、実装は別の worktree を切ること。

着地は `ai land` の列へ。承認帯は自分で押してよい(operator を待たない)。

## 7. やらないこと

- **扉 3(綴りの正規化)には手を出さない**(`design.md` 3.7)。別の card。
- **偽 `occupied` の到達路を探さない**。関数の中に到達路は無いと依頼者が確かめている。
  語彙の誤りとして 3b-1 の割り方で一緒に閉じる。
- **ロックを足さない**。
- **`launch.hy:363` の非破壊方針を変えない**(`{linked, same-entity}` 以外は loud のまま)。
- **semgrep のファイル単位の除外を外さない**。外すと出荷側の 2 関数が赤くなる。
