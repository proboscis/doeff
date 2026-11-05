//! An index that maps the module an import points to onto the layer and service location.
//!
//! The layer rules (import direction, service boundaries, location dependencies, the intent of the translation layer) look up the target of a written file's import by `module spelling -> location`. A whole run
//! builds a table listing every module in the layer population (`collect_layer_files`; modules outside the locations get their layer inferred from file contents, so it reads and
//! parses all the Hy files in the repo). A one-file run looks up only the modules that the written file's imports point to, so it uses an index that judges only the files of those modules:
//! it does not walk the tree (it probes for the path from the module's spelling with `ModuleProbe`), and the only files whose contents are read to infer a layer are those of the looked-up modules.
//!
//! The answer is the same as the whole table (`NamedIndex::site_of` is `collect_layer_files` and the construction of the whole table narrowed to one module):
//! - A file under a declared location (places) gets its layer from its path, and a file outside the declarations but under infer_root has it inferred from the tag's role. A file whose layer was decided by path is not inferred.
//! - If several files match the same module, the last one in (layer order, path order) is the one looked up.
//! - For the written file's own module, the location judged from its pre-save contents overrides other files.
//!
//! The location type `S` and how to decide one file's location (`Resolver`) belong to the caller; this file knows only the module spelling and how to find and order files.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;

use super::layers::LayerId;
use super::module_probe::ModuleProbe;

/// Decides one file's location (given the route the file came by, either the path-decided route or the route inferred from contents under infer_root, returns the location and layer).
pub(super) type Resolver<'a, S> = Box<dyn Fn(&str, &Path, Roads) -> Option<(LayerId, S)> + Send + Sync + 'a>;

/// The route a file came by (which of the two places walked by a whole run it is under).
#[derive(Debug, Clone, Copy)]
pub(super) struct Roads {
    /// Under the base of a place declaration: the layer is decided by path.
    pub(super) in_bases: bool,
    /// Under infer_root: the layer is inferred from the tag's role.
    pub(super) in_infer_root: bool,
}

/// Index of layer module locations.
pub(super) enum ModuleIndex<'a, S> {
    /// Whole run: a table of every module in the layer population.
    Whole(HashMap<String, S>),
    /// One-file run: judges only the modules that were looked up.
    Named(NamedIndex<'a, S>),
}

impl<'a, S: Clone> ModuleIndex<'a, S> {
    /// From the table of all modules (when modules overlap, the later one wins).
    pub(super) fn whole(entries: impl IntoIterator<Item = (String, S)>) -> ModuleIndex<'a, S> {
        ModuleIndex::Whole(entries.into_iter().collect())
    }

    /// An index that looks up only the modules that were looked up, starting from the written file (`own` = its module and the location judged from its pre-save contents). `bases` = the base dirs of the place declarations,
    /// `infer_root` = the root from which the layer of modules outside the declarations is inferred from the tag's role.
    pub(super) fn named(root: &Path, bases: Vec<String>, infer_root: Option<String>, own: (String, S), resolver: Resolver<'a, S>) -> ModuleIndex<'a, S> {
        ModuleIndex::Named(NamedIndex {
            own,
            probe: ModuleProbe::new(root, &["py", "hy", "hyk", "hyp"], &[]),
            bases,
            infer_root,
            resolver,
            memo: Mutex::new(HashMap::new()),
        })
    }

    /// The module's location (None if outside the layer population).
    pub(super) fn get(&self, module: &str) -> Option<S> {
        match self {
            ModuleIndex::Whole(table) => table.get(module).cloned(),
            ModuleIndex::Named(index) => index.get(module),
        }
    }
}

pub(super) struct NamedIndex<'a, S> {
    own: (String, S),
    /// Finds the files matching a module's spelling (Hy and Python files that can be in the layer population; the tree is not walked).
    probe: ModuleProbe,
    bases: Vec<String>,
    infer_root: Option<String>,
    resolver: Resolver<'a, S>,
    /// Answers for modules already looked up (the same module is not judged twice).
    memo: Mutex<HashMap<String, Option<S>>>,
}

/// Whether rel is under dir (empty = the root itself).
fn under(rel: &str, dir: &str) -> bool {
    dir.is_empty() || rel == dir || rel.strip_prefix(dir).is_some_and(|rest| rest.starts_with('/'))
}

impl<S: Clone> NamedIndex<'_, S> {
    fn get(&self, module: &str) -> Option<S> {
        if module == self.own.0 {
            return Some(self.own.1.clone());
        }
        if let Some(known) = self.memo.lock().expect("module index memo").get(module) {
            return known.clone();
        }
        let site = self.site_of(module);
        self.memo.lock().expect("module index memo").insert(module.to_string(), site.clone());
        site
    }

    /// Applies the way the whole table is built (under a place declaration, decide by path -> outside the declarations but under infer_root, infer from contents -> sort by (layer, path) and the last wins)
    /// only to the files matching one module.
    fn site_of(&self, module: &str) -> Option<S> {
        let mut sites: Vec<(LayerId, String, S)> = Vec::new();
        for (rel, path) in self.probe.files(module).iter() {
            let roads = Roads {
                in_bases: self.bases.iter().any(|base| under(rel, base)),
                in_infer_root: self.infer_root.as_deref().is_some_and(|infer_root| under(rel, infer_root)),
            };
            if let Some((layer, site)) = (self.resolver)(rel, path, roads) {
                sites.push((layer, rel.clone(), site));
            }
        }
        sites.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
        sites.pop().map(|(_, _, site)| site)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree(files: &[&str]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for rel in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "").unwrap();
        }
        dir
    }

    /// How to decide the location by route (returns the name of the layer number): under base it is "by-path", and only under infer_root it is "inferred".
    fn resolver<'a>() -> Resolver<'a, String> {
        Box::new(|rel, _path, roads| {
            if roads.in_bases {
                Some((LayerId(1), format!("by-path:{rel}")))
            } else if roads.in_infer_root {
                Some((LayerId(0), format!("inferred:{rel}")))
            } else {
                None
            }
        })
    }

    #[test]
    fn a_module_is_judged_only_when_asked_and_by_the_road_its_file_came_by() {
        let dir = tree(&["app/core/a.hy", "app/other/b.hy", "elsewhere/c.hy"]);
        let index = ModuleIndex::named(dir.path(), vec!["app/core".to_string()], Some("app".to_string()), ("app.core.me".to_string(), "me".to_string()), resolver());
        assert_eq!(index.get("app.core.a").as_deref(), Some("by-path:app/core/a.hy"));
        assert_eq!(index.get("app.other.b").as_deref(), Some("inferred:app/other/b.hy"));
        // A file outside the routes, and a missing module, are outside the population. The written file's module has its own location.
        assert_eq!(index.get("elsewhere.c"), None);
        assert_eq!(index.get("app.core.nothing"), None);
        assert_eq!(index.get("app.core.me").as_deref(), Some("me"));
    }

    #[test]
    fn when_one_module_has_several_files_the_last_in_layer_then_path_order_wins() {
        let dir = tree(&["app/core/m.hy", "app/core/m.py"]);
        let index = ModuleIndex::named(dir.path(), vec!["app".to_string()], None, ("x".to_string(), "x".to_string()), resolver());
        // Within the same layer the last in path order (m.py) wins: the same as the order of insertion into the HashMap after the whole table's `sort_by (layer, path)`.
        assert_eq!(index.get("app.core.m").as_deref(), Some("by-path:app/core/m.py"));
    }

    #[test]
    fn the_whole_table_lets_a_later_file_of_the_same_module_win() {
        let index: ModuleIndex<'_, &str> = ModuleIndex::whole([("m".to_string(), "first"), ("m".to_string(), "second")]);
        assert_eq!(index.get("m"), Some("second"));
        assert_eq!(index.get("n"), None);
    }
}
