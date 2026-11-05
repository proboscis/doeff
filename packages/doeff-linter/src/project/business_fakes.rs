//! DOEFF143: do not build fake handlers that answer business effects (#1367 / #1189 -- originally agora-controllers' temporary check
//! business_fakes_rules.hy, judgments A and D, #682, #715, #780).
//!
//! A fake = a clause for an effect, inside a definition that is reached from the mock root and not reached from the production entry. If that clause answers (rather than taps) a business effect (the effect's defining module
//! matches `:business-modules`) and is in neither the external-world effect table (`:external-effects`) nor the counterexample table (`:counterexamples`), it is critical.
//! Rot in the tables is also reported: a row of the counterexample table no longer matches (the rule is DOEFF176 -- counted separately from the number of fakes), no fake answers a row of the external-world table, and a row of the external-world table has
//! no answerer reachable from the production entry (except those listed with a reason in `:unserved`). A fake answering (rather than tapping) an effect of a lower layer (`:lower-layer-modules`)
//! is also DOEFF143 (the fake of a lower layer is only the one canonical copy outside the repo -- the original judgment C).
//!
//! DOEFF157 (the original judgment B): a test-only fake = a clause of a definition reached from a definition in a test file and reached from neither the production entry nor the mock root.
//! If it answers (rather than taps) a business effect or a lower-layer effect and is in neither the external-world table nor the counterexample table, it is critical.
//!
//! DOEFF158 (#1377 -- the original judgment G): among the clauses reached from the production entry, only one handler of the translation layer (`:translation-layer`) may answer (rather than tap) an effect of the intent layer (`:assembly-shape :intent-layer`).
//! Answerers outside the translation layer, and the second and later translation handlers answering the same effect, are reported.
//!
//! DOEFF206 (#3407, #3406): for effects of the intent layer, the production translation handler also answers in the verification environment (`:verification-environment`).
//! It reports where a clause in the verification environment's dir answers an intent effect on its own (any ending returns its own value -- even in just one branch), and where a row of the tables that let violations through (the external-world table,
//! the table that passes lower layers, the table of test-only fakes) lets an intent effect through. It is not lowered by the registry (violations of this kind are found by doeff-linter).
//!
//! Reachability walks forward from the roots over the same definition-edge graph as DOEFF133 and 136 (calls, references, nesting). Whole runs only (the repo-wide graph is needed).
//! The mock root, production entry, business modules, and table locations are read from the repo's declaration `:business-fakes`; no repo names are placed here.
//!
//! Clause judgment lives in a single place, `clause_shapes_in` (previously DOEFF143's broad reading and DOEFF206's narrow reading judged the same clause
//! separately). The clause shape `ClauseShape` has two fields: forwards = some ending forwards the received effect outward as is; answers = some
//! ending returns its own value. In this file, "tap" means a clause with forwards (observation, fault injection -- including a clause that reperforms in only one branch),
//! and the fake judgments (DOEFF143, 155, 156, 157, 158) exclude it. The hits of the counterexample table (the table checks of DOEFF143 and 157, 164, 167) and DOEFF206
//! require a table row for clauses that have answers.
//!
//! The names of the operations that make a clause ending are not written in this file. The source of truth for names and kinds of ending is one place, doeff-hy's table `CLAUSE-OPS`, and the linter reads
//! `data/clause_ops.json` (`clause_ops::ClauseOps`) exported from it. What this file holds is only the judgment for each kind of ending.

use std::collections::{BTreeMap, HashMap};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use regex::Regex;

use super::architecture::BusinessFakes;
use super::clause_ops::{self, ClauseOps};
use super::paths::glob_matches;
use super::names::hy_mangle;

/// The role of a file (decided from the type of the declaration's spelling).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileRole {
    /// Not read (:skip).
    Skipped,
    /// A test file (counted as neither the mock root nor production code).
    Test,
    /// A mock environment (neither a mock root nor production code).
    Simulation,
    /// The assembly layer (a mock root, and also production code).
    Assembly,
    /// Production code.
    Production,
}

pub fn role_of(rel: &str, decl: &BusinessFakes) -> FileRole {
    let any = |patterns: &[String]| patterns.iter().any(|p| glob_matches(p, rel));
    if any(&decl.skip) {
        FileRole::Skipped
    } else if any(&decl.tests) {
        FileRole::Test
    } else if any(&decl.simulation) {
        FileRole::Simulation
    } else if any(&decl.assembly) {
        FileRole::Assembly
    } else {
        FileRole::Production
    }
}

/// Whether a file is production code (production code or the assembly layer; only those that match when :production is written).
pub fn production_code(rel: &str, decl: &BusinessFakes) -> bool {
    matches!(role_of(rel, decl), FileRole::Production | FileRole::Assembly)
        && (decl.production.is_empty() || decl.production.iter().any(|p| glob_matches(p, rel)))
}

/// Whether a definition is in a set file (a definition, in a file matching :sets, whose name starts with prefix).
pub fn set_member(rel: &str, name: &str, prefix: Option<&str>, decl: &BusinessFakes) -> bool {
    prefix.is_some_and(|p| !p.is_empty() && name.starts_with(p)) && decl.sets.iter().any(|s| glob_matches(s, rel))
}

/// Whether a module is a business module (the spellings of `:business-modules` -- `a.b` is a.b and below it; a trailing `*` is prefix matching).
pub fn business_module(module: &str, decl: &BusinessFakes) -> bool {
    listed(module, &decl.business_modules)
}

/// Whether a module is a lower-layer module (`:lower-layer-modules` -- the spelling is the same as `:business-modules`).
pub fn lower_layer_module(module: &str, decl: &BusinessFakes) -> bool {
    listed(module, &decl.lower_layer_modules)
}

fn listed(module: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|p| match p.strip_suffix('*') {
        Some(prefix) => module.starts_with(prefix),
        None => module == p || module.starts_with(&format!("{}.", p)),
    })
}

/// Whether an effect is a business effect -- the defining module is a business module, and that file is neither a test file nor a mock environment (effects that tests or mock scenarios
/// defined themselves are not business vocabulary).
pub fn business_effect(effect: &str, decl: &BusinessFakes) -> bool {
    let module = module_of_effect(effect);
    business_module(module, decl) && !matches!(role_of(&format!("{}.hy", module.replace('.', "/")), decl), FileRole::Test | FileRole::Simulation)
}

/// Full name of an effect -> the defining module.
pub fn module_of_effect(effect: &str) -> &str {
    effect.rsplit_once('.').map(|(m, _)| m).unwrap_or("")
}

fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    &source[form.span.start..form.span.end]
}

fn head_symbol<'s>(source: &'s str, items: &[Form]) -> Option<&'s str> {
    items.first().filter(|f| matches!(f.node, Node::Symbol)).map(|f| text(source, f))
}

fn read_forms(source: &str) -> Vec<Form> {
    Reader::new(source, 0, source.len()).read_all()
}

/// The shape of one effect clause -- the single judgment shared by DOEFF143, 155, 156, 157, 158, 164, 167, and 206.
/// Collects all the clause endings (`resume`, `transfer`, `finish`, `reperform`, `pass`, and the automatic reperform when the `:when` condition is false) and divides them into two fields.
/// One clause may have both (fault injection or observation that reperforms in only one branch). How to treat it is decided on the rule side: whether a table row is required is seen by `answers`, and
/// whether it is excluded from the fake judgment as fault injection or observation is seen by `forwards`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClauseShape {
    /// Some ending forwards the received effect outward as is -- `resume` / `transfer` of a name bound by a reperform (`(<- name [type] reperform)`, `(setv name (yield reperform))`)
    /// (including a name re-bound with `(:= name bound-name)` and the like) as is; `(resume None)` in a clause that reperformed the same effect (an observation that
    /// discards the answer); `(reperform effect-name-or-alias)`; `(pass)`; and a `:when` clause. A reperform also counts `(with_handlers [...] reperform)`, which has another handler answer.
    /// `(resume (if condition A B))` is divided into the endings of each branch.
    pub forwards: bool,
    /// Some ending returns its own value -- `resume` / `transfer` of a value that is not a bound name, `finish`, `reperform` of another effect, and the answer of a reperform under another handler
    /// (the clause chooses the handler that makes the answer). A clause with no forwards ending at all also counts as a clause that answers on its own. `raise` is not counted as an ending
    /// (an observation that rethrows a caught exception cannot be told apart from a fault of its own).
    pub answers: bool,
}

impl ClauseShape {
    /// The shape of a clause that could not be read (a file that cannot be read, or a clause head that does not match the index) -- counted as a clause that answers on its own (do not err toward excluding it).
    pub const ANSWERING: ClauseShape = ClauseShape { forwards: false, answers: true };
}

/// The shape of a clause body (the sequence of forms after the clause head and the argument list). The operations that make clause endings are read by the name, kind of ending, and argument count in the table `ops`.
fn clause_shape(source: &str, params: &[&str], body: &[Form], head: &str, ops: &ClauseOps) -> ClauseShape {
    /// The name of the received effect and its aliases (the X of `(val name X)` or `(setv name X)` is the effect or an alias -- collected from the whole body).
    fn aliases<'s>(source: &'s str, form: &Form, out: &mut Vec<&'s str>) -> bool {
        let Node::Seq { items, .. } = &form.node else { return false };
        let mut grew = false;
        if let (Some("val" | "setv"), [_, name, value]) = (head_symbol(source, items), items.as_slice()) {
            let (name, value) = (text(source, name), text(source, value));
            if matches!(items[1].node, Node::Symbol) && matches!(items[2].node, Node::Symbol) && out.contains(&value) && !out.contains(&name) {
                out.push(name);
                grew = true;
            }
        }
        items.iter().fold(grew, |grew, item| aliases(source, item, out) || grew)
    }
    // The received effect is bound to the name `effect`. If the clause's fields have the name `effect` (such as `(RecordAs [principal effect] …)`), that name is the field's value,
    // not the received effect.
    let mut effect_names: Vec<&str> = if params.contains(&"effect") { Vec::new() } else { vec!["effect"] };
    while body.iter().fold(false, |grew, form| aliases(source, form, &mut effect_names) || grew) {}
    /// Whether an expression is a reperform -- the effect's name or alias, a `(head …)` call that lists the clause's received arguments as they are (`:field` words are not counted), `(yield reperform)`,
    /// `(Perform reperform)` (the way to write a relay that reperforms without opening the declaration -- `(<- name (Perform effect))`, ADR-DOE-CORE-EFFECTS-003 R4), and
    /// a reperform under another handler `(with_handlers [...] reperform)` (the spellings `with-handlers` and `with-handler` are the same).
    /// If it is a reperform, Some(whether it is under another handler).
    fn reissue(source: &str, form: &Form, head: &str, params: &[&str], effect_names: &[&str]) -> Option<bool> {
        match &form.node {
            Node::Symbol => effect_names.contains(&text(source, form)).then_some(false),
            Node::Seq { delim: Delim::Paren, items } => match head_symbol(source, items) {
                Some(h) if h == head => {
                    let args: Vec<&str> = items[1..].iter().map(|f| text(source, f)).filter(|t| !t.starts_with(':')).collect();
                    (args == params).then_some(false)
                }
                Some("yield" | "Perform") if items.len() == 2 => reissue(source, &items[1], head, params, effect_names),
                Some("with_handlers" | "with-handlers" | "with_handler" | "with-handler") if items.len() == 3 => {
                    reissue(source, &items[2], head, params, effect_names).map(|_| true)
                }
                _ => None,
            },
            _ => None,
        }
    }
    /// (forward?, answers on its own?) of the values a name can point to -- the answer of a reperform is (true, whether under another handler); any other value is (false, true).
    /// After branches merge, the OR of the per-branch values.
    type Held = (bool, bool);
    const OTHER: Held = (false, true);
    /// One clause ending.
    enum Ending {
        /// Forwards the received effect outward as is (`(reperform effect)`, `(pass)`).
        Forward,
        /// Its own value.
        Own,
        /// `resume` / `transfer` of a symbol value -- the value pointed to by the binding that reaches that ending (the nearest binding before the ending in the same branch).
        Value(Held),
        /// `(resume None)` (in a clause that reperformed the same effect, an observation that discards the answer and returns None -- counted as a forwarding ending).
        Nothing,
    }
    /// What is collected by walking the body.
    #[derive(Default)]
    struct Walked<'s> {
        /// The bindings that reach the place being walked -- names and the values each name can point to. Divided per branch of `if`, `when`, `unless`, and `cond`, and merged after the branches
        /// (names are not mixed across branches -- the judgment does not change with how names are given).
        bound: Vec<(&'s str, Held)>,
        /// Whether the same effect was reperformed somewhere in the body (including `(<- reperform)` not bound to a name).
        reissued: bool,
        endings: Vec<Ending>,
    }
    impl<'s> Walked<'s> {
        fn held(&self, name: &str) -> Option<Held> {
            self.bound.iter().find(|(bound, _)| *bound == name).map(|&(_, held)| held)
        }
        /// Rebinds a name (covers the previous binding).
        fn bind(&mut self, name: &'s str, held: Held) {
            self.bound.retain(|(bound, _)| *bound != name);
            self.bound.push((name, held));
        }
    }
    /// The values of the endings of an operation that resumes -- `(if condition A B)` is divided into the endings of each branch.
    fn value_endings(source: &str, form: &Form, walked: &mut Walked) {
        match &form.node {
            Node::Symbol if text(source, form) == "None" => walked.endings.push(Ending::Nothing),
            Node::Symbol => {
                let held = walked.held(text(source, form)).unwrap_or(OTHER);
                walked.endings.push(Ending::Value(held));
            }
            Node::Seq { delim: Delim::Paren, items } if head_symbol(source, items) == Some("if") && items.len() == 4 => {
                value_endings(source, &items[2], walked);
                value_endings(source, &items[3], walked);
            }
            _ => walked.endings.push(Ending::Own),
        }
    }
    /// Walks the branches of a branching expression one by one from the previous bindings and merges the bindings after the branches (the same name is the OR of the per-branch values). If there is a path that passes through no branch
    /// (`when`, an `if` without else, a `cond` not ending in `True`), the previous bindings are also merged in as the bindings of that path.
    fn branches<'s>(
        source: &'s str,
        alternatives: &[&[Form]],
        falls_through: bool,
        reissue: &dyn Fn(&Form) -> Option<bool>,
        ops: &ClauseOps,
        walked: &mut Walked<'s>,
    ) {
        let before = walked.bound.clone();
        let mut joined = if falls_through { before.clone() } else { Vec::new() };
        for forms in alternatives {
            walked.bound = before.clone();
            forms.iter().for_each(|form| walk(source, form, reissue, ops, walked));
            for (name, (forwards, answers)) in std::mem::take(&mut walked.bound) {
                match joined.iter_mut().find(|(bound, _)| *bound == name) {
                    Some((_, held)) => *held = (held.0 || forwards, held.1 || answers),
                    None => joined.push((name, (forwards, answers))),
                }
            }
        }
        walked.bound = joined;
    }
    fn walk<'s>(source: &'s str, form: &Form, reissue: &dyn Fn(&Form) -> Option<bool>, ops: &ClauseOps, walked: &mut Walked<'s>) {
        let Node::Seq { delim, items } = &form.node else { return };
        if *delim == Delim::Paren {
            // `:=` is in keyword form, not a symbol, in the reader, so look at it by the head's spelling.
            let word = head_symbol(source, items).or_else(|| items.first().map(|f| text(source, f)).filter(|t| *t == ":="));
            let op = word.and_then(|word| ops.named(word));
            match (word, op, items.as_slice()) {
                // (<- name [type] reperform), (<- reperform)
                // The name is bound after walking the value, and binding a value that is not a reperform also covers the previous binding.
                (Some("<-"), _, [_, .., last]) => {
                    let chosen = reissue(last);
                    walked.reissued |= chosen.is_some();
                    items.iter().for_each(|item| walk(source, item, reissue, ops, walked));
                    if items.len() >= 3 && matches!(items[1].node, Node::Symbol) {
                        walked.bind(text(source, &items[1]), chosen.map_or(OTHER, |chosen| (true, chosen)));
                    }
                    return;
                }
                // A re-binding from name to name `(:= name name)` inherits the value of the name put in (such as `(:= answer closed)`).
                (Some(":=" | "setv" | "val"), _, [_, name, value]) if matches!(name.node, Node::Symbol) => {
                    walk(source, value, reissue, ops, walked);
                    let held = match value.paren_items() {
                        _ if matches!(value.node, Node::Symbol) => walked.held(text(source, value)).unwrap_or(OTHER),
                        // (setv name (yield reperform))
                        Some(inner) if head_symbol(source, inner) == Some("yield") => match reissue(value) {
                            Some(chosen) => {
                                walked.reissued = true;
                                (true, chosen)
                            }
                            None => OTHER,
                        },
                        _ => OTHER,
                    };
                    walked.bind(text(source, name), held);
                    return;
                }
                // Branching expressions -- the condition is walked over the previous bindings, and bindings are split per branch.
                (Some("if"), _, [_, test, then]) => {
                    walk(source, test, reissue, ops, walked);
                    branches(source, &[std::slice::from_ref(then)], true, reissue, ops, walked);
                    return;
                }
                (Some("if"), _, [_, test, then, otherwise]) => {
                    walk(source, test, reissue, ops, walked);
                    branches(source, &[std::slice::from_ref(then), std::slice::from_ref(otherwise)], false, reissue, ops, walked);
                    return;
                }
                (Some("when" | "unless"), _, [_, test, body @ ..]) => {
                    walk(source, test, reissue, ops, walked);
                    branches(source, &[body], true, reissue, ops, walked);
                    return;
                }
                // (cond condition branch …), and the old spelling (cond [condition branch …] …). If the last condition is `True`, `else`, or `:else`, there is no path that passes through no branch.
                (Some("cond"), _, [_, arms @ ..]) if !arms.is_empty() => {
                    let bracketed: Option<Vec<&[Form]>> = arms.iter().map(Form::bracket_items).collect();
                    let (alternatives, last_test): (Vec<&[Form]>, Option<&Form>) = match bracketed {
                        Some(arms) => {
                            let last_test = arms.last().and_then(|arm| arm.first());
                            (arms, last_test)
                        }
                        None => {
                            arms.iter().step_by(2).for_each(|test| walk(source, test, reissue, ops, walked));
                            (arms.iter().skip(1).step_by(2).map(std::slice::from_ref).collect(), arms.len().checked_sub(2).map(|i| &arms[i]))
                        }
                    };
                    let falls_through = !last_test.is_some_and(|test| matches!(text(source, test), "True" | "else" | ":else"));
                    branches(source, &alternatives, falls_through, reissue, ops, walked);
                    return;
                }
                // Clause operations -- the name, kind of ending, and argument count are read from the table `ops`, and judged per kind of ending.
                (_, Some(op), [_, args @ ..]) => match (op.ending, op.arity, args) {
                    // An operation that resumes the continuation with a value is judged by the value's contents if it is a single value.
                    (clause_ops::Ending::Resumes, Some(1), [value]) => {
                        value_endings(source, value, walked);
                        return;
                    }
                    // A resume whose value has a different shape, and an operation that ends with a value, are its own value.
                    (clause_ops::Ending::Resumes | clause_ops::Ending::Finishes, _, _) => {
                        walked.endings.push(Ending::Own);
                        return;
                    }
                    // A forwarding operation with no arguments forwards the received effect as is. With arguments it is not counted as an ending and the inside continues to be read.
                    (clause_ops::Ending::Forwards, Some(0), []) => {
                        walked.endings.push(Ending::Forward);
                        return;
                    }
                    (clause_ops::Ending::Forwards, Some(0), _) => {}
                    // A forwarding operation that takes the received effect as an argument is a forwarding ending only when passed a reperform expression (any other value is its own).
                    (clause_ops::Ending::Forwards, _, _) => {
                        walked.endings.push(if args.len() == 1 && reissue(&args[0]).is_some() { Ending::Forward } else { Ending::Own });
                        return;
                    }
                    // An operation that exits by an exception is not counted as an ending (an observation that rethrows a caught exception cannot be told apart from a fault of its own) -- the inside continues to be read.
                    (clause_ops::Ending::Raises, _, _) => {}
                },
                _ => {}
            }
        }
        items.iter().for_each(|item| walk(source, item, reissue, ops, walked));
    }
    let reissue = |form: &Form| reissue(source, form, head, params, &effect_names);
    let mut walked = Walked::default();
    body.iter().for_each(|form| walk(source, form, &reissue, ops, &mut walked));
    let Walked { reissued, endings, .. } = walked;
    // For each ending, (forward?, answers on its own?). The answer of a reperform under another handler forwards the received effect outward while the clause chooses the handler that makes the answer, so both.
    let judged = |ending: &Ending| match ending {
        Ending::Forward => (true, false),
        Ending::Own => (false, true),
        Ending::Nothing => (reissued, !reissued),
        &Ending::Value(held) => held,
    };
    // A clause with `:when condition` is automatically reperformed by doeff-hy's handle when the condition is false (Clause guards in doeff-hy handle.hy).
    let guarded = body.first().is_some_and(|form| text(source, form) == ":when");
    let forwards = guarded || endings.iter().any(|ending| judged(ending).0);
    let answers = !forwards || endings.iter().any(|ending| judged(ending).1);
    ClauseShape { forwards, answers }
}

/// The shape of each defhandler clause of a file (key = (handler name, clause head name)). If there are two clauses with the same head in the same handler, both fields are merged by OR
/// (forwards if either forwards; answers if either answers). The table of clause operations is the bundled one (`ClauseOps::bundled`).
pub fn clause_shapes_in(source: &str) -> HashMap<(String, String), ClauseShape> {
    clause_shapes_with(source, ClauseOps::bundled())
}

/// The form of `clause_shapes_in` that takes the table of clause operations (a test checks that adding a row to the table makes the judgment follow).
pub fn clause_shapes_with(source: &str, ops: &ClauseOps) -> HashMap<(String, String), ClauseShape> {
    fn visit(source: &str, forms: &[Form], ops: &ClauseOps, out: &mut HashMap<(String, String), ClauseShape>) {
        for form in forms {
            let Node::Seq { delim, items } = &form.node else { continue };
            if *delim == Delim::Paren && head_symbol(source, items) == Some("defhandler") && items.len() >= 2 {
                let handler = text(source, &items[1]).to_string();
                for clause in &items[2..] {
                    let Some(parts) = clause.paren_items() else { continue };
                    let Some(head) = head_symbol(source, parts) else { continue };
                    let Some(params) = parts.get(1).and_then(Form::bracket_items) else { continue };
                    let params: Vec<&str> = params.iter().map(|p| text(source, p)).collect();
                    let shape = clause_shape(source, &params, &parts[2..], head, ops);
                    let merged = out.entry((handler.clone(), head.to_string())).or_insert(ClauseShape { forwards: false, answers: false });
                    merged.forwards |= shape.forwards;
                    merged.answers |= shape.answers;
                }
            }
            visit(source, items, ops, out);
        }
    }
    let mut out = HashMap::new();
    visit(source, &read_forms(source), ops, &mut out);
    out
}

/// The line range of the process entry clause `(when (= __name__ "__main__") …)` (0-based, both ends included).
pub fn main_guard_lines(source: &str) -> Vec<(u32, u32)> {
    let line_of = |at: usize| source[..at].bytes().filter(|b| *b == b'\n').count() as u32;
    read_forms(source)
        .iter()
        .filter(|form| {
            let Some(items) = form.paren_items() else { return false };
            if head_symbol(source, items) != Some("when") {
                return false;
            }
            let Some(test) = items.get(1).and_then(Form::paren_items) else { return false };
            let mut words: Vec<&str> = test.iter().map(|f| text(source, f).trim_matches('"')).collect();
            words.sort();
            words == ["=", "__main__", "__name__"]
        })
        .map(|form| (line_of(form.span.start), line_of(form.span.end)))
        .collect()
}

/// The entry strings in the body (`"<module>:<name>"` -- the module's head is `:entry-string-modules`) -> the definition's full name (the mangle of module.name).
pub fn entry_names(source: &str, decl: &BusinessFakes) -> Vec<String> {
    if decl.entry_string_modules.is_empty() {
        return Vec::new();
    }
    let heads = decl.entry_string_modules.iter().map(|m| regex::escape(m)).collect::<Vec<_>>().join("|");
    let Ok(pattern) = Regex::new(&format!(r"\b((?:{})(?:\.[A-Za-z_][A-Za-z0-9_]*)+):([A-Za-z_][A-Za-z0-9_-]*)", heads)) else {
        return Vec::new();
    };
    pattern.captures_iter(source).map(|c| format!("{}.{}", &c[1], hy_mangle(&c[2]))).collect()
}

/// The X of `isinstance(<something>, X)` in Python source (resolved to a full name through imports -- `from m import X` and `m.X` of `import m`).
pub fn python_isinstance_effects(source: &str, importer: &str) -> Vec<String> {
    use rustpython_ast::{Expr, Mod, Stmt};
    let Ok(Mod::Module(module)) = rustpython_parser::parse(source, rustpython_parser::Mode::Module, "<business-fakes>") else { return Vec::new() };
    let mut names: HashMap<String, String> = HashMap::new();
    for stmt in &module.body {
        match stmt {
            Stmt::ImportFrom(i) => {
                let level = i.level.map(|l| l.to_usize()).unwrap_or(0);
                let m = super::names::absolute_module(importer, &format!("{}{}", ".".repeat(level), i.module.as_ref().map(|m| m.as_str()).unwrap_or("")));
                for alias in &i.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                    names.insert(local, format!("{}.{}", m, alias.name));
                }
            }
            Stmt::Import(i) => {
                for alias in &i.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                    names.insert(local, alias.name.to_string());
                }
            }
            _ => {}
        }
    }
    fn spelling(expr: &Expr) -> Option<String> {
        match expr {
            Expr::Name(n) => Some(n.id.to_string()),
            Expr::Attribute(a) => spelling(&a.value).map(|head| format!("{}.{}", head, a.attr)),
            _ => None,
        }
    }
    let mut out = Vec::new();
    let mut resolve = |expr: &Expr| {
        let targets: Vec<&Expr> = match expr {
            Expr::Tuple(t) => t.elts.iter().collect(),
            other => vec![other],
        };
        for target in targets {
            let Some(text) = spelling(target) else { continue };
            let (head, rest) = text.split_once('.').map(|(h, r)| (h.to_string(), Some(r.to_string()))).unwrap_or((text.clone(), None));
            if let Some(full) = names.get(&head) {
                out.push(match rest {
                    Some(rest) => format!("{}.{}", full, rest),
                    None => full.clone(),
                });
            }
        }
    };
    let mut stack: Vec<&Expr> = Vec::new();
    fn exprs_of<'a>(stmts: &'a [Stmt], out: &mut Vec<&'a Expr>) {
        for stmt in stmts {
            match stmt {
                Stmt::If(s) => {
                    out.push(&s.test);
                    exprs_of(&s.body, out);
                    exprs_of(&s.orelse, out);
                }
                Stmt::While(s) => {
                    out.push(&s.test);
                    exprs_of(&s.body, out);
                }
                Stmt::FunctionDef(f) => exprs_of(&f.body, out),
                Stmt::AsyncFunctionDef(f) => exprs_of(&f.body, out),
                Stmt::ClassDef(c) => exprs_of(&c.body, out),
                Stmt::For(s) => exprs_of(&s.body, out),
                Stmt::With(s) => exprs_of(&s.body, out),
                Stmt::Try(s) => {
                    exprs_of(&s.body, out);
                    exprs_of(&s.orelse, out);
                    exprs_of(&s.finalbody, out);
                }
                Stmt::Return(r) => out.extend(r.value.as_deref()),
                Stmt::Expr(e) => out.push(&e.value),
                Stmt::Assign(a) => out.push(&a.value),
                _ => {}
            }
        }
    }
    exprs_of(&module.body, &mut stack);
    while let Some(expr) = stack.pop() {
        match expr {
            Expr::Call(call) => {
                if matches!(call.func.as_ref(), Expr::Name(n) if n.id.as_str() == "isinstance") && call.args.len() == 2 {
                    resolve(&call.args[1]);
                }
                stack.extend(call.args.iter());
            }
            Expr::BoolOp(b) => stack.extend(b.values.iter()),
            Expr::UnaryOp(u) => stack.push(&u.operand),
            Expr::IfExp(i) => stack.extend([i.test.as_ref(), i.body.as_ref(), i.orelse.as_ref()]),
            _ => {}
        }
    }
    out.sort();
    out.dedup();
    out
}

/// One clause that answers an effect (the index's effect clause, and its shape).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Clause {
    pub node: usize,
    pub rel: String,
    pub handler: String,
    /// The canonical name of the effect's defining origin (defining module + name) -- read by keys, table checks, and layer judgments. An effect that could not be resolved stays as spelled (that it could not be resolved is
    /// reported in the findings).
    pub effect: String,
    /// The spelling the clause's file wrote through import (the index's name -- shown in notice sentences and details).
    pub written: String,
    /// The clause's shape (computed once by `clause_shapes_in` -- all the rules read the same judgment).
    pub shape: ClauseShape,
}

impl Clause {
    /// The key of the counterexample table (`<path>::<handler>::<effect>`).
    pub fn key(&self) -> String {
        format!("{}::{}::{}", self.rel, self.handler, self.effect)
    }

    /// Whether this clause counts as a hit of the counterexample table -- not reached from the production entry, has an ending that answers on its own (even in one branch only), and its key is in the table.
    /// The table checks of DOEFF143 and 157 and the counterexample clauses of DOEFF164 and 167 use the same condition.
    pub fn counterexample_in(&self, produced: bool, counterexamples: &BTreeMap<String, String>) -> bool {
        self.shape.answers && !produced && counterexamples.contains_key(&self.key())
    }
}

/// The material for judgment (sets computed from the graph) and the judgment results.
pub struct Inputs<'a> {
    pub clauses: &'a [Clause],
    /// Whether the clause is reached from the mock root (the index is in Clause order).
    pub simulated: &'a [bool],
    /// Whether the clause is reached from the production entry.
    pub produced: &'a [bool],
    /// Whether the clause is reached from a definition in a test file.
    pub tested: &'a [bool],
    /// Whether the clause's effect is an effect of the intent layer.
    pub intent_effect: &'a [bool],
    /// Whether the clause is in a file of the translation layer.
    pub translation_file: &'a [bool],
    pub external: &'a BTreeMap<String, String>,
    pub counterexamples: &'a BTreeMap<String, String>,
    pub unserved: &'a BTreeMap<String, String>,
    /// Effects answered by Python handlers in production code (`isinstance(effect, X)` -- outside the index graph, so they count as production answerers regardless of reachability).
    pub python_answered: &'a std::collections::BTreeSet<String>,
}

/// One judgment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// A fake of a business effect (index into Clause).
    Fake(usize),
    /// A second fake of a lower-layer effect (DOEFF143).
    LowerLayerFake(usize),
    /// A fake reached only from tests (DOEFF157).
    TestOnlyFake(usize),
    /// A handler outside the translation layer answers an intent effect (DOEFF158).
    IntentAnsweredOutside(usize),
    /// Two or more translation handlers answer the same intent effect (DOEFF158 -- the clause index, and the number of answering handlers).
    IntentAnsweredTwice(usize, usize),
    /// A row of the counterexample table no longer matches.
    StaleCounterexample(String),
    /// No fake answers a row of the external-world table.
    UnusedExternal(String),
    /// A row of the external-world table has no production answerer.
    UnservedExternal(String),
    /// DOEFF206: a row of a table that lets violations through (the external-world table, the table that passes lower layers, the table of test-only fakes) lets an intent-layer effect through (the index is in `PassRow` order).
    IntentPassedByTable(usize),
    /// DOEFF206: a handler clause inside the verification environment's dir has an ending that returns its own value for an intent-layer effect, and the counterexample table has no row (index into Clause).
    IntentAnsweredInVerification(usize),
}

/// One row of a table that lets violations through (the material for DOEFF206). The key is the effect's full name for the external-world table, and `<path>::<handler>::<effect>` for the others.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PassRow {
    /// The table's dir (the declaration's spelling).
    pub table: String,
    pub key: String,
    /// The row's file (spelled from the root).
    pub origin: String,
}

impl PassRow {
    /// The effect the row lets through (after the last `::` of the key -- a key without `::` is the effect's full name as is).
    pub fn effect(&self) -> &str {
        self.key.rsplit_once("::").map(|(_, effect)| effect).unwrap_or(&self.key)
    }
}

/// The material for the DOEFF206 judgment.
pub struct IntentFakeInputs<'a> {
    pub clauses: &'a [Clause],
    /// Whether the clause's effect is an intent-layer effect (in Clause order).
    pub intent_effect: &'a [bool],
    /// Whether the clause is in a file inside the verification environment's dir.
    pub in_verification: &'a [bool],
    pub counterexamples: &'a BTreeMap<String, String>,
    pub rows: &'a [PassRow],
    /// Whether the row's effect is an intent-layer effect (in PassRow order).
    pub row_intent: &'a [bool],
}

/// DOEFF206: intent-layer effects are answered by the production translation handler even in mocks -- reports the clauses the verification environment answers on its own and the table rows that let them through.
/// A clause with even one ending that answers on its own (`ClauseShape::answers` -- including a clause that reperforms in only some branches) is excluded only when the counterexample table has its key
/// (`<path>::<handler>::<effect>`) (it is not lowered by the registry -- decided on the rule side).
pub fn judge_intent_fakes(inputs: &IntentFakeInputs) -> Vec<Verdict> {
    let IntentFakeInputs { clauses, intent_effect, in_verification, counterexamples, rows, row_intent } = inputs;
    let rows = rows.iter().enumerate().filter(|(i, _)| row_intent[*i]).map(|(i, _)| Verdict::IntentPassedByTable(i));
    let answers = clauses
        .iter()
        .enumerate()
        .filter(|(i, clause)| in_verification[*i] && intent_effect[*i] && clause.shape.answers && !counterexamples.contains_key(&clause.key()))
        .map(|(i, _)| Verdict::IntentAnsweredInVerification(i));
    rows.chain(answers).collect()
}

pub fn judge(inputs: &Inputs, decl: &BusinessFakes) -> Vec<Verdict> {
    let Inputs { clauses, simulated, produced, tested, intent_effect, translation_file, external, counterexamples, unserved, python_answered } = inputs;
    let fake = |i: usize| simulated[i] && !produced[i];
    let test_only = |i: usize| tested[i] && !simulated[i] && !produced[i];
    let mut out = Vec::new();
    let mut hit_keys = std::collections::BTreeSet::new();
    let mut answered = std::collections::BTreeSet::new();
    let mut served: std::collections::BTreeSet<&str> = python_answered.iter().map(String::as_str).collect();
    // Intent effect -> the translation handler that answers it (`<path>::<handler>`) and its first clause.
    let mut translators: BTreeMap<&str, BTreeMap<(&str, &str), usize>> = BTreeMap::new();
    for (i, clause) in clauses.iter().enumerate() {
        // If a clause not reached from production has an ending that answers on its own and its key is in the counterexample table, it counts as a table hit whichever effect it answers -- a broken handler
        // that answers a foundation effect (record, clock, outside party) can also be listed in the counterexample table. A clause that reperforms in only one branch
        // also counts as a hit (it is excluded from the fake judgment below, but a table row is required -- the same reading as DOEFF206, 164, and 167).
        if clause.counterexample_in(produced[i], counterexamples) {
            hit_keys.insert(clause.key());
        }
        // A clause with an ending that forwards the received effect outward as is is excluded from the fake judgment as fault injection or observation.
        if clause.shape.forwards {
            continue;
        }
        if produced[i] {
            served.insert(clause.effect.as_str());
            if intent_effect[i] {
                if translation_file[i] {
                    translators.entry(clause.effect.as_str()).or_default().entry((clause.rel.as_str(), clause.handler.as_str())).or_insert(i);
                } else {
                    out.push(Verdict::IntentAnsweredOutside(i));
                }
            }
        }
        let outside = !external.contains_key(&clause.effect);
        let business = outside && business_effect(&clause.effect, decl);
        let lower = lower_layer_module(module_of_effect(&clause.effect), decl);
        if test_only(i) {
            // A deliberately broken counterexample handler (including stand-ins for a lower layer's location) is counted in the counterexample table; anything else is a test-only fake.
            if business || (outside && lower) {
                let key = clause.key();
                if counterexamples.contains_key(&key) {
                    hit_keys.insert(key);
                } else {
                    out.push(Verdict::TestOnlyFake(i));
                }
            }
            continue;
        }
        if !fake(i) {
            continue;
        }
        answered.insert(clause.effect.as_str());
        if lower {
            out.push(Verdict::LowerLayerFake(i));
        }
        if !business {
            continue;
        }
        let key = clause.key();
        hit_keys.insert(key.clone());
        if !counterexamples.contains_key(&key) {
            out.push(Verdict::Fake(i));
        }
    }
    for handlers in translators.values().filter(|h| h.len() > 1) {
        out.extend(handlers.values().map(|&i| Verdict::IntentAnsweredTwice(i, handlers.len())));
    }
    for key in counterexamples.keys() {
        if !hit_keys.contains(key) {
            out.push(Verdict::StaleCounterexample(key.clone()));
        }
    }
    for effect in external.keys() {
        if !answered.contains(effect.as_str()) {
            out.push(Verdict::UnusedExternal(effect.clone()));
        }
        if !served.contains(effect.as_str()) && !unserved.contains_key(effect) {
            out.push(Verdict::UnservedExternal(effect.clone()));
        }
    }
    out
}

/// One clause of the counterexample table (the material for DOEFF164): the service that defines the clause's effect (None for a foundation effect), and the deftests that reach the clause (indices of the graph's clauses).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CounterexampleCase {
    pub owner: Option<String>,
    pub tests: std::collections::BTreeSet<usize>,
}

/// One service that has an entry layer (the material for DOEFF164): the name, and the deftests that reach the definitions of that entry layer (indices of the graph's clauses).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceCase {
    pub name: String,
    pub entry_tests: std::collections::BTreeSet<usize>,
}

/// One service without counterexamples (the judgment of DOEFF164). candidates = the number of counterexample clauses that answer that service's effects or foundation effects.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissingCounterexample {
    pub service: usize,
    pub candidates: usize,
}

/// DOEFF164: for each service, among the counterexample clauses, selects those whose effect's owner is that service or the foundation (candidates), and if even one deftest that reaches a candidate
/// reaches that service's entry, "a counterexample exists". Returns the services with none (also returns services with 0 candidates).
pub fn services_without_counterexample(cases: &[CounterexampleCase], services: &[ServiceCase]) -> Vec<MissingCounterexample> {
    services
        .iter()
        .enumerate()
        .filter_map(|(index, service)| {
            let candidates: Vec<&CounterexampleCase> =
                cases.iter().filter(|case| case.owner.as_deref().is_none_or(|owner| owner == service.name)).collect();
            let covered = candidates.iter().any(|case| !case.tests.is_disjoint(&service.entry_tests));
            (!covered).then_some(MissingCounterexample { service: index, candidates: candidates.len() })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decl() -> BusinessFakes {
        BusinessFakes {
            simulation: vec!["app/sim/**".into()],
            assembly: vec!["app/*/entry/**".into()],
            tests: vec!["**/tests/**".into()],
            skip: vec!["**/adr/**".into()],
            production: vec![],
            sets: vec!["handler_sets.hy".into()],
            simulation_prefix: Some("emulated".into()),
            production_prefix: Some("production".into()),
            entry_string_modules: vec!["app".into()],
            entry_string_files: vec!["deploy/**".into()],
            business_modules: vec!["app.orders".into(), "app.intent*".into()],
            lower_layer_modules: vec!["lib.records.effects".into()],
            external_effects: None,
            counterexamples: None,
            unserved: None,
            lower_layer_passages: None,
            test_only_fakes: None,
        }
    }

    /// The clause shape used in the judgment tests -- a forwards clause only forwards the received effect (does not answer); any other clause only answers on its own.
    fn shape(forwards: bool) -> ClauseShape {
        ClauseShape { forwards, answers: !forwards }
    }

    fn clause(i: usize, effect: &str, forwards: bool) -> Clause {
        Clause { node: i, rel: "app/sim/fake.hy".into(), handler: "fake".into(), effect: effect.into(), written: effect.into(), shape: shape(forwards) }
    }

    #[test]
    fn roles_follow_the_declaration() {
        let d = decl();
        assert_eq!(role_of("app/sim/x.hy", &d), FileRole::Simulation);
        assert_eq!(role_of("app/orders/entry/x.hy", &d), FileRole::Assembly);
        assert_eq!(role_of("app/orders/tests/x.hy", &d), FileRole::Test);
        assert_eq!(role_of("app/orders/adr/x.hy", &d), FileRole::Skipped);
        assert_eq!(role_of("app/orders/core/x.hy", &d), FileRole::Production);
        assert!(set_member("app/orders/handler_sets.hy", "emulated-orders", d.simulation_prefix.as_deref(), &d));
        assert!(!set_member("app/orders/core.hy", "emulated-orders", d.simulation_prefix.as_deref(), &d));
        assert!(business_module("app.orders.intent.rows", &d));
        assert!(business_module("app.intent_rows", &d));
        assert!(!business_module("app.ordersx", &d));
        assert!(business_effect("app.orders.intent.rows.ReadRow", &d));
        assert!(!business_effect("app.orders.tests.world.ReadWorld", &d)); // an effect the test defined itself
        assert!(!business_effect("app.sim.script.Step", &d)); // an effect of the mock scenario (and not in a business module)
    }

    #[test]
    fn entries_are_read() {
        let source = r#"
(defhandler fake
  (ReadRow [key] (<- row (ReadRow key)) (resume row))
  (WriteRow [row] (resume None))
  (Tick [n] (<- seen effect) (resume seen))
  (Log [line] (reperform effect))
  (Drop [n] (<- other effect2) (resume other)))
(defhandler fake2
  (Stop [] (resume None)))
(when (= __name__ "__main__")
  (main))
"#;
        assert_eq!(main_guard_lines(source), vec![(9, 10)]);
        assert_eq!(entry_names("env = \"app.orders.envs:make-env\"", &decl()), vec!["app.orders.envs.make_env".to_string()]);
        let py = "from app.screen.effects import Log\nimport app.clock as c\ndef dispatch(effect, k):\n    if isinstance(effect, Log):\n        return 1\n    if isinstance(effect, (c.Now, str)):\n        return 2\n";
        assert_eq!(python_isinstance_effects(py, "app.screen.entry.values"), vec!["app.clock.Now".to_string(), "app.screen.effects.Log".to_string()]);
        let relative = "from ..effects import Log\ndef d(effect):\n    return isinstance(effect, Log)\n";
        assert_eq!(python_isinstance_effects(relative, "app.screen.entry.values"), vec!["app.screen.effects.Log".to_string()]);
    }

    /// Clause shape judgment: for each ending, separates "forwards the received effect as is" from "its own value", giving the clause's two fields.
    #[test]
    fn clause_shapes_split_forwarding_and_answering_endings() {
        let source = r#"
(defhandler h
  (ReadRow [key] (<- row (ReadRow key)) (resume row))
  (Tick [n] (<- seen int effect) (resume seen))
  (Log [line] (setv answer (yield effect)) (resume answer))
  (Relayed [line] (<- answer (Perform effect)) (resume answer))
  (RelayedAs [line] (val request effect) (<- answer (Perform request)) (resume answer))
  (RelayedChanged [line] (<- answer (Perform (Other 2))) (resume answer))
  (Bend [n] (<- seen effect) (resume (+ seen 1)))
  (Swap [n] (<- seen effect) (<- other (Swap 2)) (resume other))
  (Keyed [key n] (<- row (Keyed :key key :n n)) (resume row))
  (Aliased [n] (val request effect) (<- seen datetime (GetTime)) (<- answer request) (resume answer))
  (Again [n] (reperform effect))
  (Renamed [n] (setv asked effect) (reperform asked))
  (Other [n] (reperform (Other 2)))
  (Old [n] (pass))
  (Guarded [n] :when (> n 0) (<- seen effect) (resume seen))
  (Elsewhere [key] (<- found (handler-for key)) (<- answer object (with_handlers [found] (Elsewhere key))) (resume answer))
  (Handed [key] (<- answer (with-handlers [found] effect)) (transfer answer))
  (Ended [n] (finish n))
  (Raised [n] (raise (ValueError n)))
  (Seen [n] (<- _seen effect) (<- (Note n)) (resume None))
  (Told [n] (<- (Told n)) (resume None))
  (Silent [n] (resume None))
  (Kept [n] (var answer None) (try (<- got effect) (:= answer got) (except [e Exception] (raise e))) (resume answer))
  (Wrapped [principal effect] (<- answer (with_handlers [found] effect)) (resume answer))
  (Trimmed [n] (<- answer effect) (resume (if n (trim answer) answer)))
  (Twice [n] (resume 0))
  (Twice [n] (reperform effect))
  (Own [n] (resume n)))
"#;
        let shapes = clause_shapes_in(source);
        let at = |head: &str| shapes.get(&("h".to_string(), head.to_string())).map(|s| (s.forwards, s.answers));
        let passes = Some((true, false));
        let answers = Some((false, true));
        let both = Some((true, true));
        assert_eq!(at("ReadRow"), passes);
        assert_eq!(at("Tick"), passes);
        assert_eq!(at("Log"), passes);
        assert_eq!(at("Relayed"), passes); // a relay that does not open the declaration (Perform effect) is a reperform, the same as (yield effect)
        assert_eq!(at("RelayedAs"), passes); // a relay through an alias
        assert_eq!(at("RelayedChanged"), answers); // a Perform of a different effect is not a reperform
        assert_eq!(at("Bend"), answers); // resume with the answer changed
        assert_eq!(at("Swap"), answers); // an answer reperformed with changed arguments (not a reperform of the same effect)
        assert_eq!(at("Keyed"), passes); // even with a :field word, a reperform that lists the received arguments as they are
        assert_eq!(at("Aliased"), passes); // a reperform of an alias of the received effect
        assert_eq!(at("Again"), passes); // (reperform effect)
        assert_eq!(at("Renamed"), passes); // a reperform of an alias
        assert_eq!(at("Other"), answers); // a reperform of another value is its own answer
        assert_eq!(at("Old"), passes); // (pass)
        assert_eq!(at("Guarded"), passes); // the automatic reperform when :when is false, and a resume of a reperform's answer
        assert_eq!(at("Elsewhere"), both); // has another handler answer -- the effect goes outward, but the clause chooses the handler that makes the answer
        assert_eq!(at("Handed"), both); // transfer of the answer of a reperform under another handler
        assert_eq!(at("Ended"), answers); // finish
        assert_eq!(at("Raised"), answers); // raise is not counted as an ending, and there is no forwarding ending either
        assert_eq!(at("Seen"), passes); // (resume None) of an observation that reperforms and discards the answer
        assert_eq!(at("Told"), passes); // (resume None) after a reperform not bound to a name
        assert_eq!(at("Silent"), answers); // (resume None) without a reperform is its own answer
        assert_eq!(at("Kept"), passes); // re-binds a reperform's answer with (:= name bound-name) and resumes
        assert_eq!(at("Wrapped"), answers); // the field named effect is not the received effect
        assert_eq!(at("Trimmed"), both); // one of the branches of (resume (if condition A B)) is its own value
        assert_eq!(at("Twice"), both); // two clauses with the same head are ORed per field
        assert_eq!(at("Own"), answers);
    }

    /// Failure case: a bound name is looked up by the binding that reaches that ending (the nearest binding before the ending in the same branch).
    /// A clause that binds the same name in two branches to different reperforms (a reperform that forwards as is, and a reperform with rewritten arguments) is judged to have the same shape as the same clause with the names changed
    /// -- if names were collected into one flat table for the whole clause, the rewriting branch's `(resume answer)` would hit another branch's binding, and answers would be false.
    #[test]
    fn clause_shapes_do_not_depend_on_how_bound_names_are_spelled() {
        let shape = |body: &str| {
            let source = format!("(defhandler h (HttpRequest [method url headers body] {}))", body);
            let shape = clause_shapes_in(&source)[&("h".to_string(), "HttpRequest".to_string())];
            (shape.forwards, shape.answers)
        };
        let branched = |rewritten: &str, passed: &str, form: &str| {
            let rewrite = format!(
                "(do (<- {r} (| HttpResponse HttpFailed) (HttpRequest method url :headers headers :body changed)) (resume {r}))",
                r = rewritten
            );
            let pass = format!("(do (<- {p} (| HttpResponse HttpFailed) effect) (resume {p}))", p = passed);
            match form {
                "cond" => format!("(<- to str (posted-field body)) (cond (= to D) {} True {})", rewrite, pass),
                "cond-brackets" => format!("(cond [(= method \"GET\") {}] [True {}])", rewrite, pass),
                "if" => format!("(if (= method \"GET\") {} {})", rewrite, pass),
                "when" => format!("(when (= method \"GET\") {}) {}", rewrite, pass),
                _ => unreachable!(),
            }
        };
        for form in ["cond", "cond-brackets", "if", "when"] {
            let renamed = shape(&branched("tampered", "answer", form));
            assert_eq!(renamed, (true, true), "{}: the rewriting branch answers on its own and the forwarding branch forwards", form);
            assert_eq!(shape(&branched("answer", "answer", form)), renamed, "{}: the same name answer in both branches", form);
            assert_eq!(shape(&branched("answer", "tampered", form)), renamed, "{}: the same shape even with the names swapped", form);
        }
        // Looked up by the nearest binding before the ending -- a name re-bound later covers the earlier binding, and a binding after the ending does not reach the ending.
        assert_eq!(shape("(<- answer effect) (<- answer (HttpRequest method url :body changed)) (resume answer)"), (false, true));
        assert_eq!(shape("(<- answer (HttpRequest method url :body changed)) (<- answer effect) (resume answer)"), (true, false));
        assert_eq!(shape("(<- seen effect) (resume answer) (<- answer effect)"), (false, true));
        // After branches, the per-branch bindings are merged (a value from either branch can reach).
        assert_eq!(shape("(<- answer effect) (when (= method \"GET\") (<- answer (Other 2))) (resume answer)"), (true, true));
    }

    /// The operation names the clause judgment reads come from the bundled table's rows: for each table row, the clause takes the shape matching its kind of ending. Adding a row to the table
    /// makes this test step on the added row too.
    #[test]
    fn clause_shapes_follow_every_row_of_the_table() {
        let table = ClauseOps::bundled();
        for op in table.iter() {
            let word = &op.name;
            let args = |n: usize| vec!["0"; n].join(" ");
            let shape_of = |body: String| {
                let source = format!("(defhandler h (Probe [n] {}))", body);
                let shape = clause_shapes_with(&source, table)[&("h".to_string(), "Probe".to_string())];
                (shape.forwards, shape.answers)
            };
            // Write an operation of any kind of ending beside a branch that forwards the received effect -- if the operation's ending is its own value, it also has answers.
            let beside_forward = shape_of(format!("(if n (reperform effect) ({} {}))", word, args(op.arity.unwrap_or(1))));
            match op.ending {
                clause_ops::Ending::Resumes | clause_ops::Ending::Finishes => {
                    assert_eq!(beside_forward, (true, true), "{} ends the clause with its own value", word);
                }
                clause_ops::Ending::Raises => {
                    assert_eq!(beside_forward, (true, false), "{} is not counted as an ending", word);
                }
                clause_ops::Ending::Forwards => {
                    assert_eq!(beside_forward, (true, op.arity != Some(0)), "{}: if its argument count is not 0, an ending that forwards another value is its own", word);
                    // A clause written only in the way that forwards the received effect as is has no answers.
                    let alone = match op.arity {
                        Some(0) => format!("({})", word),
                        _ => format!("({} effect)", word),
                    };
                    assert_eq!(shape_of(alone), (true, false), "a clause of only {} just forwards", word);
                }
            }
        }
    }

    /// Just adding a row to the table makes the judgment follow (reproduction): adding a row for an operation that throws an exception into the continuation (a resuming kind of ending) makes a clause that previously
    /// was not counted as an ending have its own value. Nothing is added on the judgment side. The added name is a fabricated one that does not overlap names that enter the real table later.
    #[test]
    fn a_row_added_to_the_table_is_followed_by_the_judgement() {
        let source = "(defhandler h (Probe [n] (if n (reperform effect) (inject-fault (ValueError n)))))";
        let key = ("h".to_string(), "Probe".to_string());
        let bundled = ClauseOps::bundled();
        assert!(bundled.named("inject-fault").is_none(), "this test adds an operation whose name is not in the table");
        // An operation whose name is not in the table is not counted as a clause ending.
        assert_eq!(clause_shapes_with(source, bundled)[&key], ClauseShape { forwards: true, answers: false });
        // Re-judge the same clause with a table made by adding one row to text of the same shape as the bundled file.
        let mut file: serde_json::Value = serde_json::from_str(include_str!("../../data/clause_ops.json")).unwrap();
        file["ops"].as_array_mut().unwrap().push(serde_json::json!({"name": "inject-fault", "ending": "resumes", "arity": 1}));
        let extended = ClauseOps::parse(&file.to_string()).unwrap();
        assert_eq!(clause_shapes_with(source, &extended)[&key], ClauseShape { forwards: true, answers: true });
        // Even after adding a row, the judgment of the original table's operations does not change.
        assert_eq!(
            clause_shapes_with("(defhandler h (Probe [n] (finish n)))", &extended)[&key],
            ClauseShape { forwards: false, answers: true }
        );
    }

    /// The operation names the clause judgment reads are not written in this file outside the table -- adding a name without going through the table turns red. What counts is string literals
    /// (`"name"`); the test module and comments are not counted.
    #[test]
    fn clause_operation_names_are_not_written_outside_the_table() {
        let source = include_str!("business_fakes.rs");
        let code = &source[..source.find("#[cfg(test)]").expect("read up to before the test module")];
        let written: Vec<&str> = ClauseOps::bundled()
            .iter()
            .map(|op| op.name.as_str())
            .filter(|name| code.lines().filter(|line| !line.trim_start().starts_with("//")).any(|line| line.contains(&format!("\"{}\"", name))))
            .collect();
        assert!(written.is_empty(), "wrote the table's operation names as literals: {:?}", written);
    }

    /// A broken handler that reperforms in only one branch (the form of kn-w37's 7th case before the rewrite) is judged to have the same shape {forwards, answers} even in the 3 ways of writing,
    /// and DOEFF143, 164, and 206 give the same answer: if the counterexample table has a row none of them hit, and if not, only 206 hits.
    #[test]
    fn one_branch_forwarding_is_judged_alike_by_every_rule() {
        let source = r#"
(defhandler a (Place [n] (if n (resume 0) (do (<- got effect) (resume got)))))
(defhandler b (Place [n] :when n (resume 0)))
(defhandler c (Place [n] (if n (resume 0) (reperform effect))))
"#;
        let shapes = clause_shapes_in(source);
        for handler in ["a", "b", "c"] {
            let shape = shapes[&(handler.to_string(), "Place".to_string())];
            assert_eq!(shape, ClauseShape { forwards: true, answers: true }, "shape of {}", handler);
            let at = Clause { node: 0, rel: "app/sim/fake.hy".into(), handler: handler.into(), effect: "app.orders.intent.Place".into(), written: "app.orders.intent.Place".into(), shape };
            let row: BTreeMap<String, String> = [(at.key(), "counterexample".to_string())].into();
            let clauses = vec![at];
            let (yes, no, empty) = (vec![true], vec![false], BTreeMap::new());
            let python_answered = std::collections::BTreeSet::new();
            for (table, expected_206) in [(&row, vec![]), (&empty, vec![Verdict::IntentAnsweredInVerification(0)])] {
                let verdicts = judge(
                    &Inputs {
                        clauses: &clauses,
                        simulated: &yes,
                        produced: &no,
                        tested: &no,
                        intent_effect: &yes,
                        translation_file: &no,
                        external: &empty,
                        counterexamples: table,
                        unserved: &empty,
                        python_answered: &python_answered,
                    },
                    &decl(),
                );
                assert_eq!(verdicts, vec![], "DOEFF143 hit {} (table rows: {})", handler, table.len());
                let intent = judge_intent_fakes(&IntentFakeInputs {
                    clauses: &clauses,
                    intent_effect: &yes,
                    in_verification: &yes,
                    counterexamples: table,
                    rows: &[],
                    row_intent: &[],
                });
                assert_eq!(intent, expected_206, "DOEFF206 judgment of {} (table rows: {})", handler, table.len());
                // The condition for the counterexample clauses of DOEFF164 and 167 (mod.rs reads the same Clause::counterexample_in).
                let counted = clauses.iter().filter(|c| c.counterexample_in(false, table)).count();
                assert_eq!(counted, table.len(), "number of counterexample clauses of DOEFF164: {}", handler);
            }
        }
    }

    #[test]
    fn fakes_of_business_effects_are_red_and_the_tables_do_not_rot() {
        let clauses = vec![
            clause(0, "app.orders.intent.WriteRow", false),       // a fake of a business effect -> red
            clause(1, "app.orders.intent.ReadRow", true),         // tap -> not counted
            clause(2, "app.clock.Now", false),                    // not business
            clause(3, "app.orders.intent.Send", false),           // in the external-world table
            clause(4, "app.orders.intent.Broken", false),         // in the counterexample table
            clause(5, "app.orders.intent.Shared", false),         // also reached from production -> not a fake
            // a deliberately broken stand-in for a lower layer in a test file -> hits a row of the counterexample table (not a fake)
            clause(6, "lib.records.effects.ReadRow", false),
            clause(7, "lib.records.effects.WriteRow", false),     // a mock fake answers a lower layer -> DOEFF143
            clause(8, "app.orders.intent.Ship", false),           // a business fake reached only from tests -> DOEFF157
            clause(9, "lib.records.effects.Scan", false),         // a lower-layer fake reached only from tests -> DOEFF157
            clause(10, "app.clock.Now", false),                   // reached only from tests but neither business nor lower layer
            clause(11, "app.orders.intent.Ship", true),           // a tap reached only from tests
        ];
        let n = clauses.len();
        let mut simulated = vec![true; n];
        let mut produced = vec![false; n];
        let mut tested = vec![false; n];
        simulated[8..].iter_mut().for_each(|s| *s = false);
        tested[8..].iter_mut().for_each(|t| *t = true);
        (simulated[6], tested[6]) = (false, true);
        tested[5] = true; // if also reached from production, it is not a test-only fake
        produced[5] = true;
        let intent_effect = vec![false; n];
        let translation_file = vec![false; n];
        let external: BTreeMap<String, String> =
            [("app.orders.intent.Send".to_string(), "external party".to_string()), ("app.gone.Old".to_string(), "old".to_string())].into();
        let counterexamples: BTreeMap<String, String> = [
            ("app/sim/fake.hy::fake::app.orders.intent.Broken".to_string(), "counterexample".to_string()),
            ("app/sim/fake.hy::fake::app.orders.intent.Gone".to_string(), "old counterexample".to_string()),
            ("app/sim/fake.hy::fake::lib.records.effects.ReadRow".to_string(), "lower-layer counterexample".to_string()),
        ]
        .into();
        let unserved: BTreeMap<String, String> = [("app.orders.intent.Send".to_string(), "write in #1".to_string())].into();
        let python_answered = std::collections::BTreeSet::new();
        let verdicts = judge(
            &Inputs {
                clauses: &clauses,
                simulated: &simulated,
                produced: &produced,
                tested: &tested,
                intent_effect: &intent_effect,
                translation_file: &translation_file,
                external: &external,
                counterexamples: &counterexamples,
                unserved: &unserved,
                python_answered: &python_answered,
            },
            &decl(),
        );
        assert_eq!(
            verdicts,
            vec![
                Verdict::Fake(0),
                Verdict::LowerLayerFake(7),
                Verdict::TestOnlyFake(8),
                Verdict::TestOnlyFake(9),
                Verdict::StaleCounterexample("app/sim/fake.hy::fake::app.orders.intent.Gone".into()),
                Verdict::UnusedExternal("app.gone.Old".into()),
                Verdict::UnservedExternal("app.gone.Old".into()),
            ]
        );
    }

    #[test]
    fn intent_effects_are_answered_by_one_translation_handler() {
        let at = |i: usize, rel: &str, handler: &str, forwards: bool| Clause {
            node: i,
            rel: rel.into(),
            handler: handler.into(),
            effect: "app.orders.intent.Place".into(),
            written: "app.orders.intent.Place".into(),
            shape: shape(forwards),
        };
        let clauses = vec![
            at(0, "app/orders/protocol/a.hy", "translate", false), // the first translation handler
            at(1, "app/orders/protocol/b.hy", "translate2", false), // the second for the same effect -> report both
            at(2, "app/orders/entry/f.hy", "foundation", false),   // outside the translation layer -> report
            at(3, "app/orders/entry/f.hy", "observe", true),       // taps are not counted
            at(4, "app/sim/fake.hy", "fake", false),               // not reached from production -> outside DOEFF158
            at(5, "app/orders/protocol/a.hy", "translate", false), // a second clause of the same handler counts as one
        ];
        let n = clauses.len();
        let produced = vec![true, true, true, true, false, true];
        let simulated = vec![false, false, false, false, true, false];
        let tested = vec![false; n];
        let intent_effect = vec![true; n];
        let translation_file = vec![true, true, false, false, false, true];
        let external = [("app.orders.intent.Place".to_string(), "external".to_string())].into();
        let empty = BTreeMap::new();
        let python_answered = std::collections::BTreeSet::new();
        let verdicts = judge(
            &Inputs {
                clauses: &clauses,
                simulated: &simulated,
                produced: &produced,
                tested: &tested,
                intent_effect: &intent_effect,
                translation_file: &translation_file,
                external: &external,
                counterexamples: &empty,
                unserved: &empty,
                python_answered: &python_answered,
            },
            &decl(),
        );
        assert_eq!(verdicts, vec![Verdict::IntentAnsweredOutside(2), Verdict::IntentAnsweredTwice(0, 2), Verdict::IntentAnsweredTwice(1, 2)]);
    }

    /// A broken handler that answers a foundation effect (neither business nor a lower layer) can also be listed in the counterexample table.
    /// A clause reached from production is not a counterexample, so even if it is in the table it does not hit (rot).
    #[test]
    fn counterexamples_of_foundation_effects_are_hits() {
        let clauses = vec![
            clause(0, "app.clock.Now", false),   // a broken handler of the mock foundation (the type of turn_counterexamples)
            clause(1, "app.clock.Tick", false),  // a broken handler of the foundation reached only from tests
            clause(2, "app.clock.Sleep", false), // also reached from production -> not a counterexample
        ];
        let simulated = vec![true, false, true];
        let tested = vec![false, true, false];
        let produced = vec![false, false, true];
        let flags = vec![false; clauses.len()];
        let key = |effect: &str| format!("app/sim/fake.hy::fake::{}", effect);
        let counterexamples: BTreeMap<String, String> =
            ["app.clock.Now", "app.clock.Tick", "app.clock.Sleep"].iter().map(|e| (key(e), "foundation counterexample".to_string())).collect();
        let empty = BTreeMap::new();
        let python_answered = std::collections::BTreeSet::new();
        let verdicts = judge(
            &Inputs {
                clauses: &clauses,
                simulated: &simulated,
                produced: &produced,
                tested: &tested,
                intent_effect: &flags,
                translation_file: &flags,
                external: &empty,
                counterexamples: &counterexamples,
                unserved: &empty,
                python_answered: &python_answered,
            },
            &decl(),
        );
        assert_eq!(verdicts, vec![Verdict::StaleCounterexample(key("app.clock.Sleep"))]);
    }

    /// DOEFF164: exists if even one deftest that reaches a candidate (the effect's owner is that service or the foundation) reaches the entry.
    #[test]
    fn services_without_counterexample_are_found() {
        let tests = |nodes: &[usize]| nodes.iter().copied().collect::<std::collections::BTreeSet<usize>>();
        let case = |owner: Option<&str>, nodes: &[usize]| CounterexampleCase { owner: owner.map(str::to_string), tests: tests(nodes) };
        let service = |name: &str, nodes: &[usize]| ServiceCase { name: name.into(), entry_tests: tests(nodes) };
        let cases = vec![
            case(Some("orders"), &[2]), // a counterexample of orders -- reaches orders' entry
            case(Some("orders"), &[3]), // a counterexample of an orders effect that reaches billing's entry is not counted as a billing counterexample
            case(None, &[5]),           // a counterexample of a foundation effect -- counted as a counterexample of the service it reached (stock)
            case(Some("billing"), &[9]), // a counterexample of a billing effect, but that test does not reach billing's entry
        ];
        let services = vec![service("orders", &[1, 2]), service("billing", &[3]), service("empty", &[]), service("stock", &[5])];
        assert_eq!(
            services_without_counterexample(&cases, &services),
            vec![MissingCounterexample { service: 1, candidates: 2 }, MissingCounterexample { service: 2, candidates: 1 }]
        );
    }
}
