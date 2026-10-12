//! import の先の module を層と service の置き場へ引く索引(card ki-a9959efd9cf6 の残り)。
//!
//! 層の規則(import の向き・service の境界・置き場の依存・翻訳の層の intent)は、書いた file の import の先を `module の綴り → 置き場` で引く。全体の実行は
//! 層の母集団の全 module を並べた表(`collect_layer_files` — 置き場の外の module は file の中身を読んで層を推すので、repo の Hy の file の全部を読んで
//! 解析する)を作る。1 file の実行が引くのは、書いた file の import の先の module だけなので、その module の file だけを判じる索引にする —
//! tree は歩かず(module の綴りから path を探る — `ModuleProbe`)、層を推すために中身を読むのも引かれた module の file だけ。
//!
//! 答えは全体の表と同じ(`NamedIndex::site_of` は `collect_layer_files` と全体の表の作り方を 1 つの module に絞った物):
//! - 置き場の宣言(places)の下の file は path で層を決め、宣言の外で infer_root の下の file はタグの role から推す。path で決まった file は推さない。
//! - 同じ module に当たる file が複数なら、(層の順・path の順)で並べた最後が引かれる。
//! - 書いた file は保存前の中身で判じた置き場が、自分の module について他の file を上書きする。
//!
//! 置き場の型 `S` と、file 1 つの置き場の決め方(`Resolver`)は呼び手が持つ — この file は module の綴りと file の探し方・並べ方だけを知る。

use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;

use super::layers::LayerId;
use super::module_probe::ModuleProbe;

/// file 1 つの置き場を決める(path で決める道に出会った file か、infer_root の下で中身から推す道に出会った file か — を受けて、置き場と層を返す)。
pub(super) type Resolver<'a, S> = Box<dyn Fn(&str, &Path, Roads) -> Option<(LayerId, S)> + Send + Sync + 'a>;

/// file が出会った道(全体の実行が歩く 2 つの所のどちらの下にあるか)。
#[derive(Debug, Clone, Copy)]
pub(super) struct Roads {
    /// 置き場の宣言の base の下 — path で層を決める。
    pub(super) in_bases: bool,
    /// infer_root の下 — タグの role から層を推す。
    pub(super) in_infer_root: bool,
}

/// 層の module の置き場の索引。
pub(super) enum ModuleIndex<'a, S> {
    /// 全体の実行 — 層の母集団の全 module の表。
    Whole(HashMap<String, S>),
    /// 1 file の実行 — 引かれた module だけを判じる。
    Named(NamedIndex<'a, S>),
}

impl<'a, S: Clone> ModuleIndex<'a, S> {
    /// 全部の module の表から(module の重なりは後ろが勝つ)。
    pub(super) fn whole(entries: impl IntoIterator<Item = (String, S)>) -> ModuleIndex<'a, S> {
        ModuleIndex::Whole(entries.into_iter().collect())
    }

    /// 書いた file(`own` = その module と、保存前の中身で判じた置き場)から、引かれた module だけを引く索引。`bases` = 置き場の宣言の base の dir、
    /// `infer_root` = 宣言の外の module の層をタグの role から推す root。
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

    /// module の置き場(層の母集団の外なら None)。
    pub(super) fn get(&self, module: &str) -> Option<S> {
        match self {
            ModuleIndex::Whole(table) => table.get(module).cloned(),
            ModuleIndex::Named(index) => index.get(module),
        }
    }
}

pub(super) struct NamedIndex<'a, S> {
    own: (String, S),
    /// module の綴りに当たる file を探る(層の母集団になりうる Hy と Python の file — tree は歩かない)。
    probe: ModuleProbe,
    bases: Vec<String>,
    infer_root: Option<String>,
    resolver: Resolver<'a, S>,
    /// 引いた module の答え(同じ module を 2 度判じない)。
    memo: Mutex<HashMap<String, Option<S>>>,
}

/// rel が dir(空 = 根そのもの)の下か。
fn under(rel: &str, dir: &str) -> bool {
    dir.is_empty() || rel == dir || rel.strip_prefix(dir).is_some_and(|rest| rest.starts_with('/'))
}

impl<S: Clone> NamedIndex<'_, S> {
    fn get(&self, module: &str) -> Option<S> {
        if module == self.own.0 {
            return Some(self.own.1.clone());
        }
        if let Some(known) = self.memo.lock().expect("module の索引の memo").get(module) {
            return known.clone();
        }
        let site = self.site_of(module);
        self.memo.lock().expect("module の索引の memo").insert(module.to_string(), site.clone());
        site
    }

    /// 全体の表の作り方(置き場の宣言の下は path で決める → 宣言の外で infer_root の下は中身から推す → (層・path)の順に並べて最後が勝つ)を、
    /// module 1 つに当たる file だけに当てる。
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

    /// 道に応じて置き場(層の番号の名)を返す決め方 — base の下は "by-path"、infer_root の下だけは "inferred"。
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
        // 道の外の file と、無い module は母集団の外。書いた file の module は自分の置き場。
        assert_eq!(index.get("elsewhere.c"), None);
        assert_eq!(index.get("app.core.nothing"), None);
        assert_eq!(index.get("app.core.me").as_deref(), Some("me"));
    }

    #[test]
    fn when_one_module_has_several_files_the_last_in_layer_then_path_order_wins() {
        let dir = tree(&["app/core/m.hy", "app/core/m.py"]);
        let index = ModuleIndex::named(dir.path(), vec!["app".to_string()], None, ("x".to_string(), "x".to_string()), resolver());
        // 同じ層なら path の順で最後(m.py)が勝つ — 全体の表の `sort_by (層・path)` の後に HashMap へ入れる順と同じ。
        assert_eq!(index.get("app.core.m").as_deref(), Some("by-path:app/core/m.py"));
    }

    #[test]
    fn the_whole_table_lets_a_later_file_of_the_same_module_win() {
        let index: ModuleIndex<'_, &str> = ModuleIndex::whole([("m".to_string(), "first"), ("m".to_string(), "second")]);
        assert_eq!(index.get("m"), Some("second"));
        assert_eq!(index.get("n"), None);
    }
}
