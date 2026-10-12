# 関数の中の require の macro を、bytecode の包みの記録に入れる(依頼 lt-07HRM3MVKZXK2MYM4DEQ872YC1)

出所: 設計検証 lt-JYZ9N64ZE70GCBXPP33QDG3WF7(card ki-eafa369600cd)の範囲の外の所見 1。

## 何が起きていたか

Hy 1.3.1 の `hy.macros.macroexpand` は、compiler の局所の状態の macro の表(`compiler.local_state_stack[i]["macros"]`)を、
module の表(`_hy_macros`)より先に引く。関数の中の require はこの局所の表へ macro を入れる。包みは module の表だけを、引いた名を
覚える表(`macro_use.RecordingTable`)に替えていたので、局所の表から引かれた macro が記録に入らなかった。そのため macro を変えても、
使う側の .pyc は今に合うと判じられ、古い展開のまま走った(11 → 11 → 11)。

| 入れ方 | 直す前の記録 | 直した後 |
| --- | --- | --- |
| module の頭の require | 記録する | 変わらない |
| 関数の中の require | 記録しない(macro を変えても古い展開) | 展開で実行された macro を記録する(変えると作り直す) |

## 直し方

- `macro_use._watch_local_tables`: Hy の compiler が局所の状態を開く口(`HyASTCompiler.new_local_state`)を process で 1 度だけ
  包み、開いた局所の macro の表を、展開している module の `RecordingTable.local_tables` に覚えさせる。局所の表は dict のまま覚える
  (Hy の require は局所の表を `type(target) is dict` で見分けるので、引いた名を覚える表に替えられない)。
- `MacroRecording.record`: 局所の表の中の他の module の macro のうち、展開で実行された macro(`called_code` が拾った code)だけを、
  使った macro に入れる(拾えない環境では局所の表の全部)。関数の中で `*` を require しても、展開で実行されない macro の変更では
  冷えない。ただし「実行された」は引いた名より広い — 提供元が自分の macro を module の直下で呼ぶと、その macro は提供元の import の
  間に実行されて記録に入り、その変更でも使う側が作り直される(module の直下の `*` の require では入らない。多く入れても古いと
  判じやすくなるだけで、今の repo に関数の中の require は無いので費用は 0 — 盲検でない構成のレビューの所見)。
- `_whole_requires`: 局所の表も読む。関数の中で全部を require した使い手が呼ぶ関数と同じ名の macro が提供元に足されると、古いと判じる。
- Hy の内部の形(口の名と鍵 `records.LOCAL_MACRO_TABLE`)への依りは、包みの検が使い捨ての環境(uv.lock の版の Hy)で振る舞いとして
  測る(ADR-DOE-HY-008 の R7 に書き足した)。盲検 B の失敗例(検を同じ process に置くと、局所の状態の開き方を替えた Hy でも緑)は、
  検を使い捨ての環境に置いて塞いだ — 下の代役の測り。

記録の印(`records.RECORD_TAG`)は上げない。doeff・agora-controllers・agora の source に関数の中の require は無く(`git grep` で
文字列の中の 2 か所だけ)、今の .pyc の記録は直した後も同じなので、全部の module を 1 度作り直させる費用に見合わない。
→ **この判断は後の設計検証(design.md・盲検 A)で覆した**: 直す前の包みが作った記録は局所の macro の行を持たず、照らしは記録に
在る行しか照らさないので、直した包みを配った後も macro の変更を見逃す(repro-a-real-before.log)。記録の印を 7 へ上げた。

## 測った事(evidence/)

| log | 中身 | 結果 |
| --- | --- | --- |
| before-fix.log | 包みの src の差分を外し、足した検だけで走らせた | 4 failed(使い捨ての環境で `11 == 12` の失敗・記録の macro が空)、2 passed(包みなしの対照と、直す前から通る形) |
| after-local-require.log | 所見の再現 script(lt-JYZ9N64ZE70GCBXPP33QDG3WF7 の blind-b-files/scripts/probe_local_require.py) | `local-require 11 12 12 hy 1.3.1` |
| variant-base.log | Hy 1.3.1 を替えずに組み直した wheel で新しい検 | 2 passed |
| variant-localstate.log | Hy 1.3.1 の `local_state` が `new_local_state` を通さずに積む代役(盲検 B と同じ替え方)で新しい検 | 1 failed(before-hy-12)— Hy が口を替えると検が赤 |
| final-guard.log | test_bytecode_guard.py 通し | 30 passed |
| final-record.log | test_macro_use_record.py 通し | 87 passed |
| final-adr.log | ADR-DOE-HY-008 の検 | 8 passed |

測っていない事: repo 全体の検・型検査(全体の回で測る)。

## 構成のレビュー(設計に関わっていない subagent・Fable 5.1)

直すべき指摘は無し(入れ子の関数・fn・class・内包・局所の defmacro・eval-when-compile の中の require が記録に入る事を実測)。採った物:
records.LOCAL_MACRO_TABLE の注釈の精度(鍵が替わると黙らず落ちる)・上の「実行された」の条件・検に after-hy を足す
(evidence/final-guard-local.log — 3 passed)。採らない物: 局所の `*` の require の余計な記録の削り(削ると漏れの側に倒れ、今の
repo に該当 0)・同じ module の記録の表を 2 つの thread が同時に足す競合(同じ module の import は import の lock で直列で、起きるのは
import と解析器の展開が同じ module に重なる時だけ — 落ちても多くの場合は次の展開で拾い直す)・同じ関数の中で同じ名を別の macro に
付け替える形の漏れ(module の直下と同じ既存の限界)。

載せ直した版(origin/main 787554c36 の上)で test_bytecode_guard.py 37 passed(evidence/final-guard-r2.log)。
