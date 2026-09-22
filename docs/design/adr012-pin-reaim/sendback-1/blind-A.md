# 盲検 A の記録(責務分離への反例・2026-09-22)

- 起動口: Claude Code の Agent tool(独立の新規文脈・会話履歴の fork / resume なし)。
- 模型: 1 度目 `claude-fable-5-1` → Fable の利用枠(429)で落ちた → 作法の優先順に従い
  `claude-opus-5` で起動し直した(effort は起動口が受け付けないので未確認)。
- 入力: `blind-input.md`(sha256 `7ca29cbab4d2f26d9fc0f351a27ae3699cf4b6ab4812b8d958d08491ac4c585c`)+
  `prototype.diff`(前の版・束ねた名の直接の読みだけを見る形)。
- 作業樹: `~/.worktrees/doeff-wt-blind-a2`(detached `09309e77` + 前の版の試作)。
- ⚠ 最終報告の本文は手番の切れ目(会話が機体を移り、Agent の返答が届かない)で失われた。
  ここに書くのは A が作業樹と `/tmp/blind-a2-*.log` に残した差分と走行の結果から回収した内容。

## A が置いた反例

「注入の口には 1 回の text の上限が在る(溢れると手番が壊れる)。上限を超える郵便は切らず・
落とさず器の側で止めて行に残す」— 運ぶ側(agentd `deliver-interrupts-of`)で、束ねた `text` の
**長さを測る**だけの読み `(len text)` を足す。文は 1 byte も組み替えない。

```hy
(<- text str (mail-turn-text-of message-id message.spec body message.status))
(when (> (len text) settings.interject-text-limit)
  (<- (LogLine :text …))
  (setv stopped True))
(when (not stopped) (<- outcome … (SessionInterject … :text text …)))
```

## 実射

| 版 | 結果 | 文言 |
| --- | --- | --- |
| 基準(試作のみ) | 緑 | 1 passed, 58 deselected |
| V1(上の変更) | **赤**(15.3s) | `agentd.hy の deliver-interrupts-of が合成した文を、冊の名簿の外の読み手が読んでいる … ["'(len text)", "'(len text)"]` |
| V2(V1 + 名簿へ `"len"` を 1 行宣言) | **緑**(2.6s) | 1 passed |

## 読み

- A の形は**責務の違反ではない**(組み替えていない)のに赤になる = **過剰に赤くする範囲**の実例。
- 設計が用意した逃げ道(名簿へ 1 行宣言)で緑になることも実射で成立(V2)。
- ⇒ 検査を緩めて緑にするのではなく、註に「組み替えない読みも赤になる・その時は名簿を人が
  1 行直す」と書く(依頼者 §5 の規則)。試作(2 版)の註と `design.md` §4 に写した。
  反例の行列では `lencheck`(赤)/ `lencheckdeclared`(緑)の対として固定した。
