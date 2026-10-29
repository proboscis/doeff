# 時間の記事の図の制作記録

記事ごとの制作記録。はじめ[imagegen/README.md](../README.md)の末尾へ書き足していた節を、2026-10-17に文を変えずにこのファイルへ移した。文中の`manifest.json`・`captions.json`は、今は記事ごとの`../manifest/time.json`・`../../visuals/captions/time.json`である。

## 時間の記事の2枚をSVGで描き直す（2026-10-16追記）

- 2026-09-16の47枚は、`.gitignore`の例外が`generated/`に届かず、リポジトリに入っていなかった。
- 時間の記事の2枚（time-concept・time-flow）は、週3の改稿に合わせて手で書いたSVGから作り直した。
- 正本と作り方は[time-svg-source.txt](../time-svg-source.txt)に書いた。manifest.jsonの`tool`は`SVG (hand-written) + chromium screenshot`。
- 残り45枚は、まだリポジトリに無い。`verify_publication.py`は、その45枚が揃うまで画像の検査で止まる。

## 英語の3本目の版に合わせて2枚を描き直す（2026-10-29追記）

記事を英語の3本目「Time as an effect」の版に書き直した。time-conceptは、同じtwo_napsを自作の時計tickingとdoeff-timeのsim_time_handlerで走らせた時の待ちの並びを、帯の図にした。time-flowは、英語の記事のsequenceDiagramを、やりとり1つずつの縦の図にした。どちらも手で書いたSVG(../../visuals/time-concept.svg・time-flow.svg)で、render_svg.mjsで幅1080のPNGにした(作り方は[time-svg-source.txt](../time-svg-source.txt))。
