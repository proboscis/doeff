//! The table of clause operations (`data/clause_ops.json`).
//!
//! The names of the operations written inside a defhandler clause (resume the continuation, finish with a value, forward outward, leave by exception) and the definition of how each ends have a single source: doeff-hy's table
//! `CLAUSE-OPS` (`packages/doeff-hy/src/doeff_hy/clause_ops.hy`). Rust cannot evaluate Hy, so the linter reads a file that has exported only the name, ending, and
//! arity (a generated file written by `scripts/export_clause_ops.py --write` — do not edit by hand). Names are not written in the linter's source:
//! adding a row to the table only requires rewriting the file, and the clause judgment (business_fakes.rs) follows. If the file disagrees with the table,
//! `tests/test_linter_clause_ops.py` turns red, and if a name is written directly in business_fakes.rs, the check of the same file turns red.
//!
//! Shape: `{"ops": [{"name": "…", "ending": "resumes" | "finishes" | "forwards" | "raises", "arity": number | null}]}`.
//! The reading of the ending is typed here (`Ending`), and business_fakes.rs holds how to judge for each ending. Rows with an ending this linter does not know, or a shape it cannot
//! judge, are rejected at read time (not silently counted).

use std::collections::BTreeSet;
use std::sync::OnceLock;

use serde::Deserialize;

/// What happens to the continuation after a clause ends (the same 4 as doeff-hy's `Ending`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ending {
    /// Resume the continuation with a value.
    Resumes,
    /// Discard the continuation and finish the scope where the handler was installed with a value.
    Finishes,
    /// Forward the received effect and the continuation to the outer handler.
    Forwards,
    /// Leave the clause by an exception.
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

/// One row of the table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClauseOp {
    /// The head name written inside a clause.
    pub name: String,
    pub ending: Ending,
    /// Number of arguments (`None` = not constrained).
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

/// The table of clause operations (row order is the same as the file).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ClauseOps {
    ops: Vec<ClauseOp>,
}

impl ClauseOps {
    /// Builds the table from JSON contents (unreadable rows are returned as a list of reasons).
    pub fn parse(text: &str) -> Result<ClauseOps, Vec<String>> {
        let file: TableFile = serde_json::from_str(text).map_err(|e| vec![format!("cannot read clause_ops.json: {}", e)])?;
        let mut problems = Vec::new();
        let mut seen = BTreeSet::new();
        let mut ops = Vec::new();
        for row in file.ops {
            if !seen.insert(row.name.clone()) {
                problems.push(format!("{} is listed twice in clause_ops.json", row.name));
                continue;
            }
            let Some(ending) = Ending::parse(&row.ending) else {
                problems.push(format!("this linter does not know ending {} of {} in clause_ops.json (add it to Ending in src/project/clause_ops.rs along with a judgment)", row.ending, row.name));
                continue;
            };
            // A forwarding operation is judged in only 2 shapes: "no arguments" or "one received effect" (business_fakes.rs).
            if ending == Ending::Forwards && !matches!(row.arity, Some(0 | 1)) {
                problems.push(format!("{} in clause_ops.json is a forwarding operation with arity {:?} — this linter judges only 0 or 1", row.name, row.arity));
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

    /// The row of the operation with the name (`None` for a name not in the table).
    pub fn named(&self, name: &str) -> Option<&ClauseOp> {
        self.ops.iter().find(|op| op.name == name)
    }

    /// The rows of the table (in file order).
    pub fn iter(&self) -> impl Iterator<Item = &ClauseOp> {
        self.ops.iter()
    }

    /// The table bundled into this binary (read only once).
    pub fn bundled() -> &'static ClauseOps {
        static BUNDLED: OnceLock<ClauseOps> = OnceLock::new();
        BUNDLED.get_or_init(|| {
            ClauseOps::parse(include_str!("../../data/clause_ops.json")).expect("the bundled clause_ops.json cannot be read (the check bundled_table_is_well_formed should be red)")
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The bundled table is readable and has all 4 endings (premise: the judgment checks cover every ending).
    #[test]
    fn bundled_table_is_well_formed() {
        let table = ClauseOps::parse(include_str!("../../data/clause_ops.json")).unwrap_or_else(|problems| panic!("{}", problems.join("\n")));
        for ending in [Ending::Resumes, Ending::Finishes, Ending::Forwards, Ending::Raises] {
            assert!(table.iter().any(|op| op.ending == ending), "no row for {:?} in the table", ending);
        }
        assert!(table.named("no-such-operation").is_none());
    }

    /// Unreadable rows are not silently counted but rejected with a reason.
    #[test]
    fn unreadable_rows_are_refused_with_a_reason() {
        let refuse = |text: &str| ClauseOps::parse(text).unwrap_err().join("\n");
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "teleports", "arity": 1}]}"#).contains("teleports"));
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "finishes", "arity": 1}, {"name": "a", "ending": "raises", "arity": null}]}"#).contains("twice"));
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "forwards", "arity": 2}]}"#).contains("only 0 or 1"));
        assert!(refuse(r#"{"ops": [{"name": "a", "ending": "forwards", "arity": null}]}"#).contains("only 0 or 1"));
        assert!(refuse("not json").contains("cannot read"));
    }
}
