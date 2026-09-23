;; 設計判断を、正常例と反例を持つ実行可能な検査へつなぐ教材。
(require doeff-adr.macros [defadr rule law deftest]) ; 判断・規則・法則・テストの構文を読み込む。
(import doeff-adr.macros [fact counterexample]) ; 問題の事実と、許さない反例を値として記述する。
(import doeff [do :as _doeff-do]) ; deftestが生成するProgramで使うデコレータを用意する。

(defn title-valid [title] ; 文字列が空白以外の文字を含むかを判定する純粋関数。
  (> (len (.strip title)) 0)) ; "遊び方"はTrue、空白だけの文字列はFalseになる。

(defadr ADR-ARTICLE-TITLE ; 記事用の設計判断を、このモジュールの値として定義する。
  :title "空の見出しを受け付けない" ; 何を守る判断なのかを表示名にする。
  :status "accepted" ; 採用済みとして、実行可能な検査があることを契約検査に求める。
  :scope ["example_document.py"] ; この教材で想定する適用先を記録する。
  :problem [(fact "空の見出しでは文書を識別できない")] ; 判断が必要な理由を記録する。
  :decision [(rule R1 "空白だけの見出しも拒否する")] ; 空文字だけでなく空白も拒否する規則を選ぶ。
  :laws [(law nonempty-title ; 守る条件を、判断から参照できる法則として置く。
    :statement "受理する見出しは空白を除いて1文字以上" ; 正常な入力の条件を記述する。
    :counterexamples [(counterexample "空白だけの文字列を受理する")])] ; 禁止する具体例を記録する。
  :enforcement [(deftest test-title-contract ; 正常例と反例を実行するテスト関数を生成する。
    (assert (title-valid "遊び方")) ; 文字のある見出しを受理することを確認する。
    (assert (not (title-valid "   "))))]) ; 空白だけの見出しを拒否することを確認する。
