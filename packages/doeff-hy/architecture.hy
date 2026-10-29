;;; doeff-hy の層の宣言(agora-redesign #2811)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy
;;; (この file)から決める — doeff の根の実行(pre-commit・make lint-doeff)も、この宣言の除外を読む。
;;;
;;; startup = Python の起動の時点で `.pth`(src/doeff_hy_bytecode_guard.pth)から入る、Hy の bytecode の見張り
;;;   (doeff_hy_bytecode_guard)。venv の Python が起動する時、どの Program・どの handler よりも前に走るので、設定を依存の注入
;;;   (doeff の Ask)で受ける入口が無い。共有の code の置き場の dir と置き場を切る旗は環境変数(DOEFF_HY_CODE_STORE・XDG_CACHE_HOME)で
;;;   読む(置き場を切る手 DOEFF_HY_CODE_STORE=off は #2799 の当座の手として要る)。
;;;   #3012 で見直して免除のまま置く: ここで doeff の handler を被せると venv のすべての Python の起動が doeff を読み込む(build の入口 PEP 517 と同じ種類)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module(doeff_hy_bytecode_guard とその下)に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする(外した規則が業務の code にも
;;;     当たらなくなる形を作らない)。
;;; 道具が使う置き場を環境変数から読む module(doeff_hy.env_places)を外していた層 environment は消した — 環境変数を ReadEnvironment の
;;;   効果で本物の答え手 subprocess_handler の下で問う形にしたので、DOEFF004 が当たらない(agora-redesign #3012)。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり見張りに当たる。
;;;
;;; 不変条件(startup の層の .pyc の記録の照らし — doeff_hy_bytecode_guard._macro_use。Program の外なので defservice の :invariants に
;;; 置けず、条をここに 1 か所で書く):
;;;   B1 stale-expansion-is-never-current — 記録の digest が依った物(macro を定義した file の中身・macro が辿る値の module の file の
;;;     中身・環境変数など file の外で決まる値)のどれかが記録を作った時と違えば、照らしは古いと判じて展開し直させる。digest も
;;;     照らしの答え(合う / 古い)も process をまたいで覚えない(生きた値を、辿った file や記録が名指す package の file と環境だけで
;;;     信じる形になる — 素の値はどの module から来たかを持たない)。process をまたいで覚えてよいのは、中身で決まる純な読み —
;;;     辿れない大域を覆う source の文の読み(bound_source — 鍵 = source の中身・名・読み方の code と版)と、歩みが読む関数の code の
;;;     読み(macro_use の CodeFacts — 鍵 = code の中身の marshal の sha256・読み方の code と Python・Hy の版・card ki-be596ac8276f)
;;;     — だけ。class を中身で辿るか公開の形だけで覆うかは、標準の拡張の module の末尾で判じる(source の拡張子の写しで判じない
;;;     — 知らない末尾は中身で辿る)。
;;;     関数の code の中身は、その module の展開で実行された関数(called_code が sys.monitoring で拾う)と、束縛の文の先の関数(値を
;;;     作った code)だけを覆い、記録は覆った関数の印を持つ — 実行されなかった関数の code は展開の結果を変えない(#3769 の子)。関数の
;;;     名・class の member の名・値・束縛の文は今までどおり全部入る。関数の中身を値として読む code(説明の文・引数の形)を覆う時と、
;;;     拾えない環境では全部を覆う。失敗ケース = tests/test_macro_use_called.py(呼ばない method を替えても合う・呼ぶ method なら古い・
;;;     import の時に表を作る関数・中身を読む macro)と、test_macro_use_record.py の := の有無の検(実物の defk と binding_forms)。
;;;     環境変数は版も file も覆わない process の状態: 歩みが環境変数の読み(os・posix・nt の environ・environb・getenv・getenvb
;;;     — 名で引く code と束縛の文・関数の中の import・値として当たる object)に当たった digest は照らせない — 覚えず、記録の行は印
;;;     records.ENVIRONMENT_READ で、照らしはいつも古いと判じる(環境変数から組んだ素の値の大域は値で入るので当たらない)。辿れない
;;;     値を持ち主の module の file で覆う時(既定値・閉包の cell・束縛を辿れない大域)は、持ち主の source が環境変数を読みうるなら
;;;     印にする — file は読み込みの時に読んだ環境変数を写さない。束縛の読み(bound_source)は Hy の import も import の先へ解く。
;;;     見ない読み: 標準 library の関数が中で読む環境変数・文字列で引く読み・module の表を通す読み・星の import(macro_use の docstring)。
;;;     印の判断は macro_use(作る歩みと照らす照らし)と定義点 records だけが持ち、読み込みの口は印を見分けない。
;;;     失敗ケース = tests/test_macro_use_record.py の「process をまたぐ覚え」の条 1〜3 と別の top の package の値の検(digest を
;;;     file の状態で覚える形・照らしの答えを package の file と環境で覚える形で赤)と、写しに無い拡張子の source の class の検
;;;     (agora-redesign card ki-38508e6e56fd とその設計検証の盲検 A・B)と、code の読みの鍵の検(別の file の macro が作る helper・
;;;     鍵の Hy の版・読みの関数が鍵の覆う module に在る事 — 設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の盲検 B と A の付録)と、
;;;     環境変数で決まる値を読む macro の形ごとの検(同じ設計検証の盲検 A の付録 3 — 依頼 lt-D9C8NM324RSPKZZVKC9MHA9464 — と、その
;;;     設計検証の盲検 A の Hy の import・既定値・閉包の cell の形)と、盲検 B の検(別の作業木で macro を替えた環境変数を読む使い手を
;;;     置き場の路でも compile し直す・印を records.py と macro_use.py の外で読まない)。
(defarchitecture doeff-hy
  :root "doeff_hy"
  :layers [(layer startup
             :summary "Python の起動の時点(.pth)で入る Hy の bytecode の見張り — Program の外"
             :knows "Python の import の仕組み・Hy の bytecode・共有の code の置き場の dir"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_hy_bytecode_guard]
             :exempt [(rule DOEFF004 "venv の Python が起動する時に .pth から入り、どの Program・handler よりも前に走るので、設定を Ask で受ける入口が無い(置き場の dir と置き場を切る旗を環境変数で読む)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm])])
