# 添付の画像の実 CLI 物理(段 10 lane 10o・agora-redesign #96 の便 1)

会話の画面から送る画像(郵便の添付)を、headless の claude と codex の app-server が**手番の入力としてどう受けるか**を
実物の CLI で測った記録。interrupt-physics.md / resume-physics.md と同じ役割 — 偽 CLI(tests/headless_stubs)と
Dialogue の検が真似る本物の測定値を凍結する。
測定の手順(script は測定機の一時 file — API を撃ち実クォータを消費するので repo には置かない): 64×64 の合成 PNG を 2 枚
(左半分 赤 / 右半分 青 = 137 byte・左半分 緑 / 右半分 黄 = 139 byte)作り、「左半分と右半分の色を 2 語で」と問う。
env から `CLAUDECODE` / `CLAUDE_CODE_*` / `HERDR_*` の入れ子の印を外す(親の session の hook を継がない)。

測定日 2026-09-14。機体 = 会社 Mac。claude = Claude Code 2.1.270(model claude-sonnet-5・`--permission-mode
bypassPermissions`)。codex = codex-cli 0.153.4(`codex app-server --listen stdio://`・model は家の既定 gpt-6-astra・
effort low / medium・sandbox read-only・approvalPolicy never)。

## claude — `-p --input-format stream-json --output-format stream-json`

- **stdin の user の行の content は block の列を受ける**(Messages API と同じ形):
  `{"type":"user","message":{"role":"user","content":[{"type":"text","text":<問い>},{"type":"image","source":{"type":"base64","media_type":"image/png","data":<base64>}}]}}`。
  手番 1 の答え「赤、青」(正)・result 1 つ・num_turns 1。
- **温かい process の次の手番も同じ形**: 手番 2 で block の順を image → text にして緑黄を送ると「緑、黄」と答え、1 枚目の色も
  「赤、青」と言い直した(同じ session_id・init の行がもう 1 度)。
- **走っている手番への注入(割り込み)も同じ形**: 「`sleep 3` を Bash で 4 回」の途中(t = 6.07 s・1 回目の Bash の途中)に
  text + image(緑黄)の user の行を書くと、その Bash の tool_result(t = 7.5 s)の次の境界で model が見て「Green, yellow. ACK」
  と答え、result は 1 つ(num_turns 2)。
- **`--resume <sid>` の別 process でも答えは履歴と一致する**: 手番 1〜2 の session を起こし直し、新しい画像なしで問うと
  「1枚目: 赤、青 / 2枚目: 緑、黄」。画像そのものを読み直したか前の答えの text を読んだかは、この測定では分けていない。
- **API が受けない mime(`image/svg+xml`)を CLI は断らない**: result は success・is_error false で、model が本文で
  「画像を処理できなかった(PNG・JPEG・GIF・WebP に変換を)」と答えるだけ。手番は失敗にならない — 受ける mime の判断を
  CLI の手前に置かないと、黙って劣化する。

## codex — app-server(schema v2)

- **turn/start の input(`UserInput` の oneOf)に画像の項が 2 つ**(`codex app-server generate-json-schema` の
  `v2/TurnStartParams.json`): `{"type":"image","url":<URL>,"detail"?:"auto"|"low"|"high"|"original"}` と
  `{"type":"localImage","path":<path>,"detail"?}`(ほかに text / audio / localAudio / skill / mention)。
- **data URL(`data:image/png;base64,…`)も localImage(path)も model に届く**: 手番の最初の `item/completed`(userMessage)が
  送った項をそのまま名乗り、agentMessage が色を答えた。
- **API へ渡る形は同じ**: rollout の記録(`~/.codex/sessions/…/rollout-*.jsonl` の response_item)では、どちらも
  `input_image`・`detail high`・`data:image/png;base64,…` で、中身は元の PNG と byte が同じ(139 byte・sha256 の先頭
  4cc3a49e1e3d)。送り方の違いは API に届く中身を変えない。
- **答えの揺れは model の側**: 赤青は data URL(effort medium・detail 既定 / high / original)と localImage で「赤 青」、
  data URL の effort low で 1 度だけ「青 赤」(左右が逆)。緑黄は localImage で「緑 黄」「緑 黄色」、data URL で「緑 緑」×3 と
  「Green chartreuse」。上のとおり API の入力は同じなので、64×64 の合成画像の黄を読む model の揺れで、送り方の性質ではない。
- **`thread/resume` の別 process でも答えは履歴と一致する**: 3 枚を送った thread を新しい process で resume し、画像なしで問うと
  前の答えと同じ並び。画像そのものを読み直したかは、この測定では分けていない。
- **svg の data URL を app-server は断らない**: turn は completed・agentMessage「画像 確認不可」。claude と同じく黙って劣化する。

## 含意(sessionhost の物理)

- claude: 郵便の添付は、手番の本文と同じ user の行の content を block の列にする(text + `image{source base64}`)。割り込みの
  本文も同じ行の形で運べる(`ClaudeDialogue.inject`)。
- codex: 添付は turn/start の input に `{"type":"image","url":"data:<mime>;base64,<data>"}` の項として積む(localImage と
  API への入力は同じなので、agentd は一時 file を作らない)。割り込みの継ぎ足し(`pending_injection`)も項の列で持つ必要が
  ある(今日は文字列の連結)。
- 両 CLI とも受けない mime を断らないので、受ける mime の判断は agentd の手前に 1 点置く(置き場は agora-redesign #96 の問い)。
- 再開: claude の `--resume` と codex の `thread/resume` は、CLI の履歴に画像の手番を残す。履歴からの再開(記録の service からの
  要約 — 9f-4 / 9o-3)の 1 項に画像をどう載せるかは、この測定の外。

## 記録

- `/tmp/lane10o-probe/`(`claude_probe.log`・`claude_resume.log`・`codex_probe*.log`・`run-*.jsonl`)— 測定機の一時 file
  (残らない)。要点は上の数字。
