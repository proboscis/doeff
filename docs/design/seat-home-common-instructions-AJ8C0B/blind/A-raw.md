# 盲検 A の返答(未加工)

役: 将来変更で責務が波及する反例の生成 / 要求モデル `claude-opus-5`(effort は起動口が受け付けない)
起動: Agent 口・新規文脈 / 2026-09-21 / 所要 494 秒 / tool 25 回 / 107,142 token

---

## 1. 現実的な変更要求と、その要求が必要になる理由

**要求: 共通条文と skills の正本の置き場を移す(設計自身の S1)。**
具体には「pod の容器で dotfiles を clone せず、image 同梱の `/opt/agent-canon/{CLAUDE.md,skills}` から配る」に切り替え、**宿ごとに順に**(会社 Mac → pod → 個人 MacBook)当てる。

必要になる理由は設計自身が書いている。§3.6 の ⚠ と §7 手順 0 が「pod の容器のどこに dotfiles が在るかは未確認 ⇒ 据える口を 1 つ決めるのが実装の最初の判断」と残している。つまり pod の置き場は**これから決まる**ので、決めた後に動く(image layout の変更・volume 化・会社 Mac の dotfiles path 変更)のは特殊事態ではなく通常運用。規模は宣言 2 行 × 宿の数で、極端でも破壊的でもない。

前提は全部守る。P1(正本は node の file)・P2(家は共有・doeff だけが書く)・P3(本体の逐語)・P4(宿ごとの差は宣言の 1 点)はどれも成り立ったままで、本体の版にも触らない。**契約の適用範囲内**の変更要求である。

## 2. 関係する主張と、予想に反して変わる責務

対象の claim は S1 そのもの:

> claim: **変わるのは `host-declaration` の 2 鍵の値だけ**。doeff 側は 1 行も変わらない。
> unchanged: … `claude-home-installer`(運ばれてきた path を実体化するだけ)。

実コードで測ると偽になる。**skills の「正本へ向け直す」動作が、現行の substrate の effect 語彙で書けない。**

- `/home/kento/repos/doeff/packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy:451-470` の `FsLinkArtifact` は、**target が実在して別実体なら触らず `"target-conflict"` を返す**。raise ではなく値。effects 側の契約もその逐語(`effects.hy:700-710`「別実体 = 触らず `target-conflict`(silent 置換はしない — share.py 同型の no-op)」)。
- **readlink 相当の effect が substrate に 1 つも無い**(`effects.hy` の fs-* は canonical-path / read-text / write-text-atomic / make-dirs / list-dir / compose-home-view / link-artifact / dir-exists / file-exists / file-mtime の 10 本)。
- 張り替えを実装している唯一の code は `substrate.hy:295-310` の `_ensure-view-symlink` だが、これは `FsComposeHomeView`(**codex の profile view**)の private で、effect として公開されていない。

⇒ 実際に動くのは設計が unchanged と書いた `claude-home-installer` ではなく、**§5 の責務表に 1 行も載っていない `sessionhost/effects.hy` + `substrate.hy`(+ 偽 substrate + `.pyi`)= substrate の語彙そのもの**。ここが予想外の座。

副作用として §3.1 の判定基準が採用案に跳ね返る。案 A を退けた 1 つ目の理由は「黙って落ちる枝が在る・log は 1 行も出ない」だった。`fs-link-artifact` をそのまま使うと張り替えは `target-conflict` で**黙って**落ち、§4 の計器は `skills=<指した先>` に**宣言した先**を印字するので、log は起きなかった張り替えを肯定する。

## 3. どの知識がどこへ漏れるか(拡張・配線・共同不変条件との区別)

**(a) 意図した公開契約の拡張 — 反例ではない**
2 鍵の追加、`seat-memory`/`seat-skills` labels、`claudeMdExcludes` を `CLAUDE-SETTINGS-OWNED-KEYS` に入れること、S1 で宣言の値が変わること。すべて §3・§8 に宣言済み。

**(b) 配線 — 反例ではない**
effect を 1 本足すと `effects.hy` / `substrate.hy` / 偽 substrate / `.pyi` の 4 座が動く。file が複数変わるだけなので欠陥ではない。

**(c) 隠すはずの知識の漏洩 — これが反例の核**
「`<家>/skills` の既存 link を黙って置換してよいか」は **claude の共通条文の据え付けの policy 判断**だが、その答えは現在 **別責務の中に書かれている**:

- `FsLinkArtifact` の no-op は **会話 artifact の transplant**(ADR-DOE-AGENTS-006 R7 / dotfiles `share.py`)由来。
- `_ensure-view-symlink` の erosion guard(実 file なら typed fail)は **codex の profile bundle**(`substrate.hy:298-308`「silent 置換は registry と token の fork を隠す」)由来。

つまり `claude-home-installer` は「運ばれてきた path を実体化するだけ」ではいられず、**自分と無関係な 2 つの責務が置いた置換 policy を知った上で迂回するか、その policy を再審議するか**を選ばされる。§5 の表はこの依存を `effects: Fs* / EnvGet のみ` と平坦化していて、`Fs*` を一様な能力として扱っている。そこが漏れ口。

**(d) 共同の不変条件(持ち主が無い)**
切り替えは「宿ごとに順に」なので、**同じ家に違う中身を書こうとする 2 席**が存在しうる窓を作る。これは S3 の claim「同じ node の全席が同じ宣言を読むので書く中身は同一 ⇒ 冪等」が唯一成り立たない窓であり、そこで §3.5 の「**lock を足さない**」の根拠が崩れる。

- 家は `<homes-root>/claude/<account>`(`acp/judgment.hy:5593-5598`, `:2922`)。homes-root は機体に 1 つ(`acp/runtime.py:132`)。⇒ **同じ account の全席が 1 つの家を共有**。
- host は connection ごとに thread で回す(`host.hy:2001`)。家に対する lock は無い(唯一の lock は `substrate.hy:271-283` の **view 単位**で、しかもその註は「同一 binding の並行 launch が symlink の unlink/relink で race しないよう」と、まさにこの race を既に認めている)。
- `FsWriteTextAtomic`(`substrate.hy:439-445`)の tmp path は `path + tmp-suffix` で、**書き手ごとに一意でない**。

⇒ 「1 つの家に同時に 1 人しか書かない」という不変条件は `judgment.claude-home-of`(家の鍵付け)・`claude-home-installer`(書き)・`substrate`(tmp の命名)に跨り、**どの module の公開契約にも書かれていない**。

**(e) 執行の穴(漏洩の最短経路が lint に見えない)**
`/home/kento/repos/doeff/.semgrep.yaml:455-473` の `doeff-agents-substrate-clean-impls` が見るのは `subprocess` / `sqlite3` / `os.system` / `open(` だけ。**`os.symlink` / `os.unlink` / `os.readlink` は規則の外**。`impls/claude_code.hy` は今 `json` / `re` / `uuid` しか import していない(`:18-20`)が、`(import os)` を足して直に張り替えても `make lint-semgrep` は緑になる。§6 の新設規則は `claudeMdExcludes` / `"CLAUDE.md"` / `"skills"` の第 2 の綴りしか禁じないので、この穴は塞がらない。

## 4. 最小の再現と観測すべき結果

どちらも `substrate.hy` の分岐を逐語で転写して `/tmp` で撃った(repo は変更していない)。

**再現 A — 張り替えが黙って落ちる**

```
canonA/ canonB/ と空の home/ を作る
fs_link_artifact("/tmp/ce1/canonA", home/skills)  # 1 回目
fs_link_artifact("/tmp/ce1/canonB", home/skills)  # 宣言を移した 2 回目
```

実測:

```
1st (canonA): linked          -> /tmp/ce1/canonA
2nd (canonB): target-conflict -> /tmp/ce1/canonA     # 張り替わらない
seat sees: ['a.md']                                   # 席は旧い正本の skills を読む
dangling:  FsDirExists(isdir)=False  FsFileExists(isfile)=False
           FsCanonicalPath(realpath)=/tmp/ce1/canonMISSING
```

観測すべきもの: `os.readlink(<家>/skills)` と、§4 の log 行の `skills=` の値の**食い違い**。加えて壊れた link は `FsDirExists` / `FsFileExists` がどちらも False を返すので、「link が無い」と「link が旧い正本を指して壊れている」を現行語彙で区別できない(readlink が無い)。

deftest 形に落とすなら: 偽 substrate の `FsLinkArtifact` を実物と同じ 4 値で返す実装にし、同じ家に対して宣言 canonA → canonB の順で `claude-pre-launch` を 2 回通し、最後に**家の中の link の先**を読む。期待 `canonB` / 実際 `canonA`。

**再現 B — 同じ家への同時書き(切り替え窓)**

`path + ".agentd-tmp"` を共有したまま、同じ path へ 62,696 byte(§3.1 の実測 = `~/dotfiles/claude/CLAUDE.md`)を 2 thread で書く × 200 回:

```
os.replace の例外: 184 / 200   FileNotFoundError: '.../CLAUDE.md.agentd-tmp' -> '.../CLAUDE.md'
最終 file が A でも B でもない(torn): 0
```

観測すべきもの: 家に残る `CLAUDE.md.agentd-tmp` の残骸と、`claude-pre-launch` から抜ける `FileNotFoundError`(`fs-write-text-atomic` の handler は OSError を捕まえないので、**席が起きない**方向に落ちる)。実系での最小手順は「同じ account に結ぶ 2 席を同拍で `session.launch` する」。

## 5. 未確認の前提・不足する情報(推測と実測の区別)

**実測(この会話で撃った / grep で確かめた)**
- `FsLinkArtifact` の 4 値と「別実体は no-op」(`substrate.hy:451-470`・`effects.hy:700-710`)。
- readlink 相当の effect が無いこと(`effects.hy` の fs-* の全数)。
- `FsWriteTextAtomic` の tmp が `path + suffix`(`substrate.hy:439-445`)と、同時書きで 184/200 が `FileNotFoundError` になること。
- 家が `<homes-root>/claude/<account>`(`judgment.hy:2922`, `:5593-5598`)で homes-root が機体に 1 つ(`runtime.py:132`)。
- connection ごとの thread(`host.hy:2001`)と、家に対する lock の不在。
- semgrep の substrate-clean が `os.symlink` を見ないこと(`.semgrep.yaml:455-473`)と `claude_code.hy` が `os` を import していないこと(`:18-20`)。

**推測(測っていない)**
- 同じ account の 2 席が同拍で launch する**頻度**は測っていない。到達可能性を code 経路(家の鍵付け + connection thread + lock 不在)から読んだだけで、thread の実験は窓を人工的に詰めている。実系の launch の間隔は未測定。
- `claude-pre-launch` が connection thread 上で走ることは `host.hy` の `session.launch` handler から PreLaunchSetup までを追い切っていない。
- substrate 本体を import せず**逐語の転写**で撃った。転写の忠実性は目視照合であって、実 handler を走らせた測定ではない。

**設計の側の不足(この反例の射程を限る)**
- 設計は「whole-dir symlink を張る」としか書いておらず、**どの effect を使うかを書いていない**。だから「張り替えが書けない」は設計の誤記ではなく実装段の制約で、明示的に偽になるのは S1 の「**doeff 側は 1 行も変わらない**」と S3 の「**lock を足さない**」の 2 つの claim ちょうど。
- pod の席が agentd と同じ容器で走るか(= 正本の path が席から見えるか)は未確認。§7 手順 0 と同じ穴。

**副次の所見(主反例ではないが観測契約に関わる)**
`seat_settings_present` は `runtime.py:159-160` で **agentd の起動時に 1 度だけ** `os.path.isfile` され、`node-labels-of`(`judgment.hy:3244-3268`)はその凍った値を present/missing として名乗る。§3.3 が「同じ形で」`seat-memory` / `seat-skills` を足すなら、§4 の「直ったことの観測 2 点」のうち label 側は**起動の拍の真実を名乗らない**(degrade が checkout で追いついた日も missing のまま、正本が消えた日も present のまま)。§5 の表が `launch-readout` に置いた「不在の名乗り」と、行に出る label が 2 つの拍に割れている。

**結論として、合格・安全とは言えない**。ここで示したのは S1 と S3 の claim に対する具体的な反例 1 件であって、他の軸(S2・S4・S5・S6)は撃っていない。
