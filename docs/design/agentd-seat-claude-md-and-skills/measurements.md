# 実測記録(据わっている claude 2.1.278)

- 機体: 会社 Mac `CA-20038667` / macOS Darwin 25.5.0
- 本体: `/Users/s22625/.local/bin/claude` → `/Users/s22625/.local/share/claude/versions/2.1.278`
  (`claude --version` = `2.1.278 (Claude Code)`)
- 実行の形: agentd と同じ(`CLAUDE_CONFIG_DIR=<試験用ホーム>` + `CLAUDE_CODE_OAUTH_TOKEN=<この会話の
  親プロセスが持っていた札>`)。`-p`(1 ターン)・`--model sonnet`・`--no-session-persistence`。
- 日時: 2026-09-21 13:5x〜14:1x JST

## M0 出発点(この会話そのもの)

```
HOME=/Users/s22625
CLAUDE_CONFIG_DIR=/Users/s22625/.local/state/doeff/agentd-homes/claude/claude_b2d48a2a0d023bad
```

- そのホームの中身: `.claude.json` / `backups` / `plugins` / `projects` / `session-env` / `sessions` /
  `shell-snapshots` / `policy-limits.json` / `.last-cleanup`。**CLAUDE.md も skills も無い。**
- 会社 Mac のホームは 31 個。
- `$HOME/.claude/skills` は `~/dotfiles/agent/skills` への symlink(117 本)。`$HOME/.claude/CLAUDE.md` は
  `~/dotfiles/claude/CLAUDE.md` への symlink。
- この会話の context に載っている共通 CLAUDE.md のラベルは
  `Contents of /Users/s22625/.claude/CLAUDE.md (project instructions, checked into the codebase)`。
  使える skill の一覧に dotfiles の skill は 1 本も無い。

## M1 設定ホーム直下の CLAUDE.md はどの層で読まれるか

試験用ホーム `/tmp/ccx/cfg` に `CLAUDE.md`(目印 `SENTINEL_USER_MEMORY_9F3A`)と
`skills/zz-sentinel-skill/SKILL.md` を置き、作業ディレクトリ `/tmp/ccx/work`(= `$HOME` の外)で 1 ターン。

質問: context に目印が在るか・ラベルの逐語・指示ファイルとして挙がるパス。

答え(逐語):

```
1) Yes. It appears under the heading "Contents of /tmp/ccx/cfg/CLAUDE.md (user's private global
   instructions for all projects):" ...
3) - /tmp/ccx/cfg/CLAUDE.md (labeled "user's private global instructions for all projects")
   - /tmp/ccx/cfg/projects/-private-tmp-ccx-work/memory/ (the memory system directory)
```

⇒ **設定ホーム直下の CLAUDE.md は user 層**。作業ディレクトリが `$HOME` の外でも載る。

注: このターンは `--tools ""`(道具なし)で走らせたため、使える skill の一覧そのものが context に
出なかった(「一覧が無い」と答えた)。skills は M2 で測り直した。

## M2 設定ホーム直下の skills は一覧に入るか

同じホーム、道具を既定に戻して 1 ターン。答え(冒頭抜粋・逐語):

```
- zz-sentinel-skill: SENTINEL_SKILL_7C1B — a probe skill used to measure skill discovery from the config home.
- dataviz: ...
```

⇒ **入る**(一覧の先頭)。組み込みの skill 13 本と並ぶ。

## M3 symlink は効くか / M4 二重読みは起きるか

試験用ホーム `/tmp/ccx/cfg2` を symlink だけで作る:

```
CLAUDE.md -> /Users/s22625/dotfiles/claude/CLAUDE.md
skills    -> /Users/s22625/dotfiles/agent/skills
```

作業ディレクトリ `/Users/s22625/tmp-ccx-probe`(= `$HOME` の下 — Mac の今日の偶然の読みが起きる位置)で 1 ターン。

答え(逐語):

```
(a) 命令/CLAUDE.md 系ファイルとして提示されているのは1つだけです:
- /tmp/ccx/cfg2/CLAUDE.md — ラベル:「user's private global instructions for all projects」
(b) いいえ。同一内容が複数箇所に重複して出てはいません。
(c) available-skills リストのエントリ数は 129 です。
- agora-artifact: 有り / coupling-core: 有り / model-routing: 有り
```

⇒ symlink はファイル・ディレクトリの両方で効く。129 = 組み込み 12 + dotfiles 117。
⇒ `$HOME/.claude/CLAUDE.md` は project 層として **出なかった**。

対照(M4): 同じ作業ディレクトリ・同じ本文を、symlink ではなく**実体のコピー**で置いたホーム
`/tmp/ccx/cfg3` で 1 ターン。答え(逐語):

```
1. /tmp/ccx/cfg3/CLAUDE.md — 「Contents of ... (user's private global instructions for all projects):」
2. /Users/s22625/.claude/CLAUDE.md — 「Contents of ... (project instructions, checked into the codebase):」
同じ本文テキストが2回出現しているかどうか: はい、2回出現しています。
```

⇒ 祖先を登る project 層の読みは**起きている**。symlink の時に出なかったのは、
**解決後のファイル実体が同じだから重複排除された**ため。コピーは 64KB が 2 度載る。

## M7 走っているプロセスの途中で効くか

`--input-format stream-json` の 1 プロセスを保ったまま、ターンの間にホームへ
`CLAUDE.md` と `skills/zz-live3/SKILL.md` を足し、同じ質問を 3 回。

質問(目印を質問文に含めない形): 「user 層の指示ファイルが context に在るか(在れば本文を逐語で)・
使える skill の一覧の件数」。

```
TURN1: (1) NONE  (2) 12
TURN2: (1) NONE  (2) 12
TURN3: (1) NONE  (2) 12
```

⇒ **走っているプロセスには効かない**。読みは起動時の 1 回。

対照(別プロセス): 同じホームで新しいプロセスを立てると、足した CLAUDE.md も skill も載る
(`--resume` で立て直した場合も同じ)。

誤検出を 1 度作った: 最初の版では質問文の中に目印の文字列を書いたため、モデルが「context に在る」と
答えた。目印を質問文から外して測り直した結果が上の 3 ターン。

## M8 起動の引数で skills を渡せるか

```
--plugin-dir /Users/s22625/dotfiles/agent/skills  → 一覧 13(組み込みのみ)・agora-artifact: no
--plugin-dir /Users/s22625/dotfiles/agent         → 一覧 129・agora-artifact: yes / model-routing: yes
```

⇒ 親ディレクトリ(`skills/` を含む方)を渡せば skills は届く。
⇒ `claude --help` の全数を読んだが、**user 層の CLAUDE.md を指す引数は無い**。
`--add-dir` は project 層のディレクトリ、`--append-system-prompt` は CLAUDE.md ではない、
`--settings` に置き場を指すキーは無い(本体の文字列表を検索しても `skillsDirectory` 系のキーは無い)。

## M9 壊れた symlink を置いたセッションは立つか

```
CLAUDE.md -> /nonexistent/dotfiles/claude/CLAUDE.md
skills    -> /nonexistent/dotfiles/agent/skills
```

答え: `ALIVE 12`(異常終了なし・組み込みのみ)。

⇒ リンク先が無い日もセッションは止まらない。pod が古い checkout に戻って立つ日の振る舞いがこれ。

---

## 追補 M10 symlink が効くかは**起動口(CLAUDE_CODE_ENTRYPOINT)で分かれる**

2026-09-21 14:3x JST・依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 の裁定(郵便 lt-Z4X6DP96ZVYGJZ59A4JRH4CYSV)を
受け、据わっている本体 `/Users/s22625/.local/share/claude/versions/2.1.278` の綴りをこの会話でも
直接読んで確かめた(逐語・minify 済み):

```js
async function H4(e,n,r,s,g=0,h,y,w){
  let M=GRt(e,n,r,g,w); if(M===void 0)return[];
  let O=s&&(n!=="User"||KRt()), {resolvedPath:B,isSymlink:U}=ho(le(),e);
  if(oA(B))return[];
  if(g>0&&!O&&!MB(B))return[];
  if(n==="User"&&!O)try{ let ve=await le().lstat(e);
    if(g===0&&ve.isSymbolicLink()||(ve.nlink??1)>1&&ve.isFile())return[] }catch{}
  ...
  for(let ve of _e){ if(!MB(ve)&&!O)continue; ... }   // @ の取り込み
}
function KRt(){return mB()!=="local-agent"}
function mB(){return n().entrypoint}
```

user 層の CLAUDE.md の呼び口(同じ本体):

```js
await H4(Le,"User",B,!0,0,void 0,{backend:g,key:Re.state("user-memory")},_e)
```

⇒ 第 4 引数 `s` は user 層では常に真なので、`O = KRt() = (entrypoint !== "local-agent")`。

**読み方(3 点)**

1. **user 層の規則 file が symlink / hard link のとき黙って落ちるのは、起動口が `local-agent` の席だけ。**
   M3・M4 を測った `-p` の席と、agentd が起こす席(この会話の env は `CLAUDE_CODE_ENTRYPOINT=sdk-cli`)では
   落ちない。pod 側の「symlink は 0 件」は `local-agent` の席で測ったもので、**両方の実測が正しい**。
2. **`@` の取り込みも同じ門で落ちる** — `if(!MB(ve)&&!O)continue`。⇒ 設定ホームに実体の CLAUDE.md を置いて
   中身を `@~/dotfiles/…` で取り込む形も、`local-agent` の席では**外部の取り込みが消える**(`MB` = 同じ木の
   中かの判定)。symlink を避けて `@` へ逃げても解にならない。
3. 落ちる条件の細目: **symlink は深さ 0 の file だけ**(`g===0`)・**hard link(`nlink>1` の file)は深さを問わず**。
   project 層(`n!=="User"`)はこの門の外なので、symlink でも落ちない。

**まだ測っていないこと**: skills の探索は H4(規則の読み込み)とは別の経路なので、この門は skills の
ディレクトリ symlink には**当たらないはず**だが、`local-agent` の席で skills を測ってはいない。

⇒ **起動口に依らない形が要るなら、user 層の CLAUDE.md は実体の写し(中身ごと)にするしかない。**
下書き `design.md` の推し(案 A = 設定ホームへ symlink)は、`local-agent` の席では受入条件 1 を満たさない。
