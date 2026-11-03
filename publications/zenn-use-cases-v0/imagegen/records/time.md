# 時間の記事の図の制作記録

記事ごとの制作記録。はじめ[imagegen/README.md](../README.md)の末尾へ書き足していた節を、2026-10-17に文を変えずにこのファイルへ移した。文中の`manifest.json`・`captions.json`は、今は記事ごとの`../manifest/time.json`・`../../visuals/captions/time.json`である。

## 時間の記事の2枚をSVGで描き直す（2026-10-16追記）

- 2026-09-16の47枚は、`.gitignore`の例外が`generated/`に届かず、リポジトリに入っていなかった。
- 時間の記事の2枚（time-concept・time-flow）は、週3の改稿に合わせて手で書いたSVGから作り直した。
- 正本と作り方は[time-svg-source.txt](../time-svg-source.txt)に書いた。manifest.jsonの`tool`は`SVG (hand-written) + chromium screenshot`。
- 残り45枚は、まだリポジトリに無い。`verify_publication.py`は、その45枚が揃うまで画像の検査で止まる。

## 概念図をよくある書き方との対比に描き直す（2026-11-03追記）

- 利用者が記事のレビュー（agoraのレビュー書）で、概念図の日本語が不自然で質が低いと指摘した。
- 冒頭を「よくある書き方（`time.sleep`とモック）→doeff」の順に改めたのに合わせ、time-conceptを上段・下段の対比に描き直した。自作のハンドラと同期の時計の箱は外した。
- 正本は`../../visuals/time-concept.svg`、PNGは幅1200・高さ1076。time-flowは文言に問題が無いので変えていない。
