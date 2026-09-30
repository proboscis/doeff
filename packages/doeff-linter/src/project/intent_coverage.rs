//! DOEFF165: intent の効果の網羅の表(agora-redesign #1561 K3 — 親 #1155「業務ロジックを『テストした』の定義」)。
//!
//! 各 service の intent の層(`:assembly-shape :intent-layer`)に宣言した効果(defeffect)ごとに 3 つの列を読む:
//!   * 手元の検から出すか — 検の file の定義から届く定義(効果の節 = 答え手を除く)が、その効果を名指す(作る)。
//!   * 模擬の答え手 — 模擬の根(模擬の環境・組み立ての層・模擬の組の関数)から届く効果の節。
//!   * 本番の答え手 — 本番の入口(本番の組の関数・defsystem・`__main__`・入口の文字列)から届く効果の節。
//! 到達の図は DOEFF143・158 と同じ物(`judge_business_fakes`)を使う。ここは列の値から欠けを決める純な判定だけを持つ。
//! 欠けが 1 つでもある効果を 1 件の違反にする(鍵の細目 `<service>::<効果>`)。K3 は報告だけ(重さ info)だった。K4(#1562)から
//! 重さ error・既定の段 critical で失敗にし、repo の登録簿に載った既知の欠けは warning に下がる(新しい欠けだけが赤)。

/// 効果 1 つの列の値(到達の図から読んだ事実)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectFacts {
    /// 効果の完全名(module + 名)。
    pub effect: String,
    /// 効果を宣言した file(根からの path)。
    pub rel: String,
    /// 宣言の file を置き場に持つ service(architecture.hy の defservice の名)— どの service の dir の下にも無ければ None。
    pub service: Option<String>,
    /// 手元の検から届く定義のうち、この効果を名指す物(答え手を除く・完全名・並べ済み)。
    pub emitters: Vec<String>,
    /// 模擬の根から届く答え手(`<handler>` の完全名・並べ済み)。
    pub simulated: Vec<String>,
    /// 本番の入口から届く答え手(並べ済み)。
    pub produced: Vec<String>,
}

/// 欠けの種類(閉じた 3 つ)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Gap {
    /// 手元の検から届く定義がこの効果を出さない(どの検もこの業務の操作を通らない)。
    NotEmittedByTest,
    /// 模擬の根から届く答え手が無い(模擬の環境で出すと答えが無い)。
    NoSimulatedAnswerer,
    /// 本番の入口から届く答え手が無い(本番で出すと答えが無い)。
    NoProducedAnswerer,
}

impl Gap {
    /// 人が読む 1 語句。
    pub fn words(self) -> &'static str {
        match self {
            Gap::NotEmittedByTest => "手元の検から出さない",
            Gap::NoSimulatedAnswerer => "模擬の答え手が無い",
            Gap::NoProducedAnswerer => "本番の答え手が無い",
        }
    }
}

/// 表の 1 行(効果 1 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub facts: EffectFacts,
    pub gaps: Vec<Gap>,
}

impl Row {
    /// 知らせの鍵の細目 `<service>::<効果>`(service の無い効果は `-`)。
    pub fn detail(&self) -> String {
        format!("{}::{}", self.facts.service.as_deref().unwrap_or("-"), self.facts.effect)
    }

    /// 3 列を 1 行で(`--explain` と知らせの本文)。
    pub fn columns(&self) -> String {
        let list = |names: &[String]| if names.is_empty() { "無し".to_string() } else { names.join("・") };
        format!(
            "手元の検から出す定義 = {} / 模擬の答え手 = {} / 本番の答え手 = {}",
            list(&self.facts.emitters),
            list(&self.facts.simulated),
            list(&self.facts.produced)
        )
    }

    /// 欠けの語句を並べた 1 行。
    pub fn gap_words(&self) -> String {
        self.gaps.iter().map(|g| g.words()).collect::<Vec<_>>().join("・")
    }
}

/// 列の値から欠けを決める。
pub fn gaps_of(facts: &EffectFacts) -> Vec<Gap> {
    let mut gaps = Vec::new();
    if facts.emitters.is_empty() {
        gaps.push(Gap::NotEmittedByTest);
    }
    if facts.simulated.is_empty() {
        gaps.push(Gap::NoSimulatedAnswerer);
    }
    if facts.produced.is_empty() {
        gaps.push(Gap::NoProducedAnswerer);
    }
    gaps
}

/// 表の全部の行(service・効果の順)。欠けの無い行も含む — 知らせにするのは欠けのある行だけ(呼び手が選ぶ)。
pub fn table(facts: Vec<EffectFacts>) -> Vec<Row> {
    let mut rows: Vec<Row> = facts.into_iter().map(|f| Row { gaps: gaps_of(&f), facts: f }).collect();
    rows.sort_by(|a, b| a.facts.service.cmp(&b.facts.service).then_with(|| a.facts.effect.cmp(&b.facts.effect)));
    rows
}

/// 効果の完全名を宣言した file の置き場から、それを持つ service を引く(dir の最も長い前方一致)。
pub fn service_of<'s>(rel: &str, services: &'s [(String, String)]) -> Option<&'s str> {
    services
        .iter()
        .filter(|(_, dir)| rel.starts_with(&format!("{}/", dir.trim_end_matches('/'))))
        .max_by_key(|(_, dir)| dir.len())
        .map(|(name, _)| name.as_str())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(service: &str, effect: &str, emitters: &[&str], simulated: &[&str], produced: &[&str]) -> EffectFacts {
        let owned = |xs: &[&str]| xs.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        EffectFacts {
            effect: effect.to_string(),
            rel: format!("app/{}/intent/effects.hy", service),
            service: Some(service.to_string()),
            emitters: owned(emitters),
            simulated: owned(simulated),
            produced: owned(produced),
        }
    }

    #[test]
    fn a_small_example_table_names_each_gap() {
        // 欠けのある小さな例: 網羅された効果 1・検から出さない効果 1・本番の答え手の無い効果 1・何も無い効果 1(別の service)。
        let rows = table(vec![
            facts("board", "app.board.intent.effects.MoveCard", &["app.board.core.move"], &["app.board.protocol.h"], &["app.board.protocol.h"]),
            facts("board", "app.board.intent.effects.ArchiveCard", &[], &["app.board.protocol.h"], &["app.board.protocol.h"]),
            facts("board", "app.board.intent.effects.ClaimCard", &["app.board.core.claim"], &["app.board.protocol.h"], &[]),
            facts("chat", "app.chat.intent.effects.Post", &[], &[], &[]),
        ]);
        let got: Vec<(String, Vec<Gap>)> = rows.iter().map(|r| (r.detail(), r.gaps.clone())).collect();
        assert_eq!(
            got,
            vec![
                ("board::app.board.intent.effects.ArchiveCard".to_string(), vec![Gap::NotEmittedByTest]),
                ("board::app.board.intent.effects.ClaimCard".to_string(), vec![Gap::NoProducedAnswerer]),
                ("board::app.board.intent.effects.MoveCard".to_string(), vec![]),
                (
                    "chat::app.chat.intent.effects.Post".to_string(),
                    vec![Gap::NotEmittedByTest, Gap::NoSimulatedAnswerer, Gap::NoProducedAnswerer]
                ),
            ]
        );
        // 3 列は名を並べ、空の列は「無し」と書く。
        let claim = &rows[1];
        assert_eq!(claim.columns(), "手元の検から出す定義 = app.board.core.claim / 模擬の答え手 = app.board.protocol.h / 本番の答え手 = 無し");
        assert_eq!(rows[3].gap_words(), "手元の検から出さない・模擬の答え手が無い・本番の答え手が無い");
    }

    #[test]
    fn a_fully_covered_effect_has_no_gap() {
        // 反例: 3 列が全部埋まった効果は欠け 0(知らせにならない)。
        let full = facts("board", "app.board.intent.effects.MoveCard", &["t"], &["s"], &["p"]);
        assert!(gaps_of(&full).is_empty());
    }

    #[test]
    fn the_service_is_the_longest_dir_prefix() {
        let services = vec![("board".to_string(), "app/board".to_string()), ("board-ui".to_string(), "app/board/ui".to_string())];
        assert_eq!(service_of("app/board/intent/effects.hy", &services), Some("board"));
        assert_eq!(service_of("app/board/ui/intent/effects.hy", &services), Some("board-ui"));
        // 反例: 名の頭が同じでも dir が違えば当たらない・どの dir の下にも無ければ None。
        assert_eq!(service_of("app/boardroom/intent/effects.hy", &services), None);
        assert_eq!(service_of("lib/x.hy", &services), None);
    }
}
