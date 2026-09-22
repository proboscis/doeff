# 盲検 B の記録(静的検査を通過する責務違反の反例・2026-09-22)

- 起動口: Claude Code の Agent tool(独立の新規文脈・A の返答も見ない)。
- 模型: 1 度目 `claude-fable-5-1` → Fable の利用枠(429)で落ちた → `claude-opus-5` で起動し直した
  (effort は起動口が受け付けないので未確認)。
- 入力: A と同じ `blind-input.md` + `prototype.diff`(前の版)。
- 作業樹: `~/.worktrees/doeff-wt-blind-b2`(detached `09309e77` + 前の版の試作)。B が冊に足した
  差分は無い(`diff` で試作と同一を確認)。
- ⚠ 最終報告の本文は手番の切れ目で失われた。作業樹の差分・`/tmp/blind-b2-*.log`・B が書いた
  検体 `/tmp/blind-b2-probe.d/probe.py` から回収した。

## B の対照(検査が生きている証拠)

`message-bodies-of` の中で `(setv text (+ text "\n\n----"))` と素朴に組み替える → **赤**
(`judgment.hy の message-bodies-of が合成した文を、冊の名簿の外の読み手が読んでいる … '(+ text …)' '(setv text …)'`)。

## B の反例(前の版の試作を**緑で通過**した責務違反)

「1 手番に 2 通以上を畳む拍は、agent が『何通目/全何通』を読めるように見出しに通番の欄を出す。
全 M 通は畳み終わるまで分からないので、並べ終えた `bodies` へ 1 度だけ書き足す」:

```hy
(.append bodies text)            ; ← 名簿の読み手(そのまま)
…
(setv total (len bodies))        ; ← 束ねた名 text ではなく、積んだ先 bodies を歩く
(when (> total 1)
  (setv numbered [])
  (for [[index folded] (enumerate bodies)]
    … (.append numbered (+ (cut head 0 -1) "・通=" … "]\n" rest)))
  (setv bodies numbered))
```

- 前の版の試作: **緑**(1 passed in 11.12s)。`text` の読みは `bodies.append` の 1 つだけで名簿と一致
  するため。文の組み替えは `bodies` を経由して起きる。
- B の検体(`probe.py`): 郵便 1 通では judgment の 1 点が組んだ文と手番へ渡る文が同一。郵便 2 通
  (相乗り = 本番で普通に起きる形)では**同じ郵便に 2 つの綴り**が生まれる(畳みの路は「・通=1/2]」つき、
  注入の路は無し)= 第 2 の合成点が実際に立った。

## 読み — 当席の主張 1 の反例が成立

主張 1「同じ頂点の form の中で組み替える実装は綴りに依らず赤」は、**束ねた名の直接の読み**しか
追わない設計では成り立たなかった。B の形は同じ頂点の form の中で、名簿の読み手が積んだ先を
後から組み替える。「覆わないと宣言した範囲」(別の頂点の form・handler の実 I/O・呼ばずに作る形)の
どれにも当たらない ⇒ **設計の穴**。

## 直し(試作 2 版)

束ねた名から始めて**運ぶ名の閉包**(名簿の読み手が `(.m recv 文)` で積んだ受け手 recv・
`(setv 名 文)` / `(<- 名 型 文)` で写した名)を同じ頂点の form の中で求め、閉包の全部の読みを
名簿と突き合わせる。運ぶ名ごとの最初の書き込み(導入・`(setv bodies [])`)だけは読みに数えない。
名簿の judgment 側に `"tuple"`(積み終えた bodies をそのまま返す読み)を 1 行足した。
反例の行列に `numbered`(B の形・期待 = 赤)として固定した。
