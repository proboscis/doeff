

## 11. 素の関数(deff)の理由の種類 — DOEFF110・111・203

architecture.hy の `defarchitecture` に、素の関数を許す理由の種類の閉じた一覧を宣言する(名と説明は Rust に書かない):

```hy
:plain-callable-reasons [(reason library-callback "外の library が素の関数として呼ぶ(sorted の key・dataclass の hook・内包表記の中の値)")
                         (reason framework-entry "framework の規約が決まった形の関数を呼ぶ(pytest の fixture・doeff-cluster の job_entry)")
                         (reason process-entry "process の入口の main(defp にして doeff の CLI で走らせられない時だけ)")
                         (reason macro-time "マクロの展開の時に呼ぶ")]
```

- 註の形: `; defk にできない(<種類>): <この定義に固有の詳細>`(括弧と `:` は全角でもよい)。定義の行か、直前の註だけの行に書く。
- **DOEFF111**(一覧を宣言した repo): 註が無い・種類が一覧に無い・詳細が空か「同上」は error。種類の無い旧い形(`; defk にできない: …`)は移行の間は warning
  (登録簿に載れば info)。reason に種類の一覧と、名乗った種類の説明を差し込む。hint = 「組み立て(handler の並び)なら `(defk handlers-of [foundation])` に・
  テストなら deftest に・値を組む補助なら defk にして `(<- …)` で呼ぶ」。一覧の無い repo は今どおり目印の有無だけを見る。
- **DOEFF110**: defn の同じ行の註の種類が一覧に在れば hint =「deff にする(種類 X)」、無ければ「defk にする(理由が種類に当たらない)」。
- **登録簿**: 登録簿に載った warning は info に下げる(載った error は `registered_severity`・既定 warning)。
- **DOEFF203**(意味・Jev・Choice): 種類を名乗った deff ごとに、定義の source と名乗った種類と詳細を state にして、一覧の種類 + none から「本当に素の関数でなければ
  ならない理由」を選ばせる(問いの文は `src/project/semantic.rs` の `plain_callable_wire` に英語で 1 か所・criteria は一覧の説明)。名乗った種類の確率が
  `semantic.plain_callable.info_below`(既定 0.3)未満で info、`warning_below`(書いた時だけ)未満で warning。error にはしない。撃つのは `--semantic` /
  `--semantic-all` の時だけで、較正の見張りは Noul の問い(DOEFF201・202)が有効な時だけ撃つ。
