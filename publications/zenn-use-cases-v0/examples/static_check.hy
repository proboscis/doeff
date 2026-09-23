;; 記事用の静的検査であり、リポジトリの実運用規則へは登録しない。
(require doeff-adr.macros [defsemgrep]) ; badとgoodを実際に検査するテストを生成する。
(defsemgrep no-empty-title ; 記事用の検査名を付ける。
  :languages ["python"] ; Pythonの構文木としてパターンを照合する。
  :message "空の見出しでは文書を識別できません" ; 検出したときに示す理由を指定する。
  :pattern "Title(\"\")" ; Titleへ空文字リテラルを渡す呼び出しを検出する。
  :bad ["Title(\"\")"] ; 反例が少なくとも1件検出されることを検査する。
  :good ["Title(\"遊び方\")"]) ; 有効な見出しの例が検出されないことを検査する。
