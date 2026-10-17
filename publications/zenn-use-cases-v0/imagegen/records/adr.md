# 設計の記録の記事の図の制作記録

記事ごとの制作記録。はじめ[imagegen/README.md](../README.md)の末尾へ書き足していた節を、2026-10-17に文を変えずにこのファイルへ移した。文中の`manifest.json`・`captions.json`は、今は記事ごとの`../manifest/adr.json`・`../../visuals/captions/adr.json`である。

## 設計の記録の記事の2枚をSVGで描く（2026-10-17追記）

- 10本目の新しい回「設計の記録をテストとして走らせる」(doeff-adr.md)の2枚(adr-concept・adr-flow)を、手で書いたSVGから作った。
- 正本と作り方は[adr-svg-source.txt](../adr-svg-source.txt)に書いた。manifest.jsonの`tool`は時間の記事と同じ。
- 新しい回の図なので、まだリポジトリに無い他の記事の図の枚数（2026-10-17に数えて38枚）は変わらない。

## adr-flowの1行目を11月以後の結果に直す（2026-10-17追記）

- 1行目のpytestの枠が「3 passed・10月31日まではdeftestも通ってしまう」だったのを、「走らせた日で変わる・10月30日まで 3 passed・10月31日から 1 failed」に直した。10本目は11月より後に出るため。
- 下の注記を「doeffのmainで走らせた結果」から「PyPIのdoeff 0.6.0で走らせた結果(1行目のpytestは日時を固定)」に直した。記事の出力は、PyPIの0.6.0を入れて走らせたため。
- 作り方は同じ（`visuals/render_svg.mjs`・幅1200・高さ1000）。台帳`manifest/adr.json`のsha256・bytesと、`captions/adr.json`の説明を合わせた。
