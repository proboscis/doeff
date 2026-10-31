//! 節の操作の表(`data/clause_ops.json`)。
//!
//! defhandler の節の中で書く操作(続きを再開する・値で終える・外へ渡す・例外で抜ける)の名と終わり方の定義元は、doeff-hy の表
//! `CLAUSE-OPS`(`packages/doeff-hy/src/doeff_hy/clause_ops.hy`)の 1 か所。Rust は Hy を評価できないので、linter は表から名・終わり方・
//! 引数の数だけを書き出した file を読む(`scripts/export_clause_ops.py --write` が書く生成物 — 手で直さない)。名を linter の source に
//! 書かない: 表に行を足せば file を書き直すだけで、節の判じ(business_fakes.rs)が追従する。file が表と食い違えば
//! `tests/test_linter_clause_ops.py` が、business_fakes.rs に名を直に書けば同 file の検が赤にする。
//!
//! 形: `{"ops": [{"name": "…", "ending": "resumes" | "finishes" | "forwards" | "raises", "arity": 数 | null}]}`。
//! 終わり方の読みはここで型にし(`Ending`)、判じ方は終わり方ごとに business_fakes.rs が持つ。この linter が知らない終わり方・判じられない
//! 形の行は読みの時に断る(黙って数えない)。

use std::collections::BTreeSet;
use std::sync::OnceLock;

use serde::Deserialize;

/// 節を終えた後に続きをどうするか(doeff-hy の `Ending` と同じ 4 つ)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ending {
    /// 続きを値で再開する。
    Resumes,
    /// 続きを捨て、handler を置いたスコープを値で終える。
    Finishes,
    /// 受けた効果と続きを外の handler へ渡す。
    Forwards,
    /// 例外で節を抜ける。
    Raises,
}

impl Ending {
    fn parse(word: &str) -> Option<Ending> {
        match word {
            "resumes" => Some(Ending::Resumes),
            "finishes" => Some(Ending::Finishes),
            "forwards" => Some(Ending::Forwards),
            "raises" => Some(Ending::Raises),
            _ => None,
        }
    }
}

/// 表の行 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClauseOp {
    /// 節の中で書く頭の名。
    pub name: String,
    pub ending: Ending,
    /// 引数の数(`None` = 問わない)。
    pub arity: Option<usize>,
}

#[derive(Deserialize)]
struct TableFile {
    ops: Vec<TableRow>,
}

#[derive(Deserialize)]
struct TableRow {
    name: String,
    ending: String,
    arity: Option<usize>,
}

/// 節の操作の表(行の順は file のまま)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ClauseOps {
    ops: Vec<ClauseOp>,
}

impl ClauseOps {
    /// JSON の中身を表にする(読めない行は理由の列)。
    pub fn parse(text: &str) -> Result<ClauseOps, Vec<String>> {
        let file: TableFile = serde_json::from_str(text).map_err(|e| vec![format!("clause_ops.json を読めない: {}", e)])?;
        let mut problems = Vec::new();
        let mut seen = BTreeSet::new();
        let mut ops = Vec::new();
        for row in file.ops {
            if !seen.insert(row.name.clone()) {
                problems.push(format!("clause_ops.json の {} が 2 度載っている", row.name));
                continue;
            }
            let Some(ending) = Ending::parse(&row.ending) else {
                problems.push(format!("clause_ops.json の {} の ending {} をこの linter は知らない(src/project/clause_ops.rs の Ending と判じを足す)", row.name, row.ending));
                continue;
            };
            // 渡す操作の判じは「引数なし」か「受けた効果 1 つ」の 2 形だけ(business_fakes.rs)。
            if ending == Ending::Forwards && !matches!(row.arity, Some(0 | 1)) {
                problems.push(format!("clause_ops.json の {} は渡す操作で arity が {:?} — この linter が判じるのは 0 か 1 だけ", row.name, row.arity));
                continue;
            }
            ops.push(ClauseOp { name: row.name, ending, arity: row.arity });
        }
        if problems.is_empty() {
            Ok(ClauseOps { ops })
        } else {
            Err(problems)
        }
    }

    /// 名の操作の行(表に無い名は `None`)。
    pub fn named(&self, name: &str) -> Option<&ClauseOp> {
        self.ops.iter().find(|op| op.name == name)
    }

    /// 表の行(file の順)。
    pub fn iter(&self) -> impl Iterator<Item = &ClauseOp> {
        self.ops.iter()
    }

    /// この binary に同梱した表(読みは 1 度だけ)。
    pub fn bundled() -> &'static ClauseOps {
        static BUNDLED: OnceLock<ClauseOps> = OnceLock::new();
        BUNDLED.get_or_init(|| {
            ClauseOps::parse(include_str!("../../data/clause_ops.json")).expect("同梱の clause_ops.json が読めない(検 bundled_table_is_well_formed が赤のはず)")
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 同梱の表は読め、終わり方が 4 つとも在る(判じの検が全部の終わり方を踏む前提)。
    #[test]
    fn bundled_table_is_well_formed() {
        let table = ClauseOps::parse(include_str!("../../data/clause_ops.json")).unwrap_or_else(|problems| panic!("{}", problems.join("\n")));
        for ending in [Ending::Resumes, Ending::Finishes, Ending::Forwards, Ending::Raises] {
            assert!(table.iter().any(|op| op.ending == ending), "{:?} の行が表に無い", ending);
        }
        assert!(table.named("no-such-operation").is_none());
    }

    /// 読めない行は黙って数えず、理由で断る。
    #[test]
    fn unreadable_rows_are_refused_with_a_reason() {
        let refuse = |text: &str| ClauseOps::parse(text).unwrap_err().join("\n");
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "teleports", "arity": 1}]}"#).contains("teleports"));
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "finishes", "arity": 1}, {"name": "a", "ending": "raises", "arity": null}]}"#).contains("2 度"));
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "forwards", "arity": 2}]}"#).contains("0 か 1"));
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "forwards", "arity": null}]}"#).contains("0 か 1"));
        assert!(refuse("not json").contains("読めない"));
    }
}
