# 連載6本目のreplayの記事を、記事の書き方の決まりへ揃える

- 決定日時：2026-10-17（JST）
- 決定担当：記事を月4本公開する取り組みで、連載6本目「外部APIのテストを、通信せずに回す」の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：記事の取り組みが決めた書き方の決まり9項目と、連載の並べ方（案M）の6本目の困りごと「外部APIのテストが遅く、落ちる」。
- 置き場：シリーズ全体の決定は[2026-09-15-zenn-use-cases.md](2026-09-15-zenn-use-cases.md)にある。週ごとの記事が同じ文書の末尾へ同時に書き足すと取り込みの時に衝突するため、週2と同じく、この回の記録は別の文書にした。

## 決定と理由

- 原稿は既存の`doeff-replay.md`を書き直す。ファイル名は変えない。公開順の表（`publication-plan.md`）とslugは、表を書き換える別の仕事が持つので、ここでは触れない。
- 冒頭を悩み3つ（遅い・相手の都合で落ちる・CIに鍵が要る）にし、最初のコードを自作の最小ハンドラ`fake_http`のテストにする。各回の仕上げ基準「ハンドラの中身を見せる」を最初の枠で満たすため。
- 記録と再生は、HTTPの取得担当とメモ化の記録の層を分けて合成する組み方を主役に残す。`http_fixture_handler`は記録の時に取得担当を内側で組み立て、鍵の作り方も替えられないため、補足に留める。
- 本番の取得担当の枠を足し、外のサービスの代わりに同じ機械の中のHTTPサーバーで実際に通信して確かめる。記事の「未確認の機能を実証済みとして扱わない」を守りつつ、記録の道を実物で確かめるため。
- 既定の鍵がヘッダのAPIキーを含むため、偽の鍵では再生できないことを実践の要点にする。`key_fn`でメソッドとURLだけの鍵にする枠を足す。読者の「CIに本物の鍵を置きたくない」に直に答えるため。
- 図2枚を、週2〜4と同じ作り方（手で書いたSVG → `publications/zenn-use-cases-v0/visuals/render_svg.mjs`）で作り直す。作り方は`publications/zenn-use-cases-v0/imagegen/replay-svg-source.txt`に書く。
- 確かめの`reviews/replay-check.py`は`verify_examples.py`から呼ばない。同じ区切りの他の記事の仕事が同じ文書を同時に直すと衝突するため。命令は`reviews/replay.md`に書いた。

## 戻す手順

本記事・`examples/http_replay.py`・`reviews/replay-check.py`・`reviews/replay.md`の追記・`reviews/replay-visuals.json`・図2枚とSVG・台帳（captions.json・manifest.jsonのreplayの2行・replay-svg-source.txt）・この文書を、この変更のcommitで戻す。他の記事と製品コードには触れていない。
