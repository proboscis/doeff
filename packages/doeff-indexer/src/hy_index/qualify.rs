//! 完全修飾名(版 4)— 定義の `qualified_name` と、呼び出しの `target`(呼び先の完全修飾名)。版 5 からは同じ名前の解決で、
//! 宣言した effect(`effects`)・型の注記の中の名(`param_types` / `answer_type` の `names`)・effect 節の解く effect(`handles`)の
//! `target` も埋める。
//!
//! 完全修飾名 = module の dotted 名 + 入れ物の名(在れば)+ 定義の名。区切りはどれも mangle した綴り
//! (`pkg.mod.Store.put_row`)。呼び出しの `target` は、その file の中身(module・definitions・imports)だけで決める
//! 名前の解決の結果で、他の file を見ない。だから `--root` の全体・`--file`・`--stdin` のどの実行でも同じ値になり、
//! 読む側は `target` と `qualified_name` の文字列の一致で呼び先の定義を引き、同じ一致の逆向きで呼び手を引く。
//! `target` は Hy の定義とは限らない(Python の関数・外の package の名前もその完全修飾名になる)— 索引の
//! `qualified_name` に一致すれば Hy の定義。
//!
//! 解決の順(doeff-runner の resolve.ts の a / b 段と、raw.rs の経由の証拠が使う規則と同じ):
//! 1. 同じ file の定義 — 修飾の無い名前は top level の定義、`q.name` は入れ物 q の中の定義。
//! 2. file の import(`require` は除く — macro は呼び先の定義にしない)— 最初に束ねた import を使う。
//!    `(import m [x])` の `x` は `m.x`、`(import m)` の `m.sub.f` は `m.sub.f`、`(import m [C])` の `C.f` は `m.C.f`。
//!    module そのものを呼ぶ形(`(import m)` の `(m)`)は呼び先の定義が無いので null。
//! 3. どれでもない(組み込み・special form・局所の束縛・引数・値の上の属性)は null。

use super::analyze::mangle;
use super::model::{Definition, HyFileIndex, Import, NameRef, TypeNote};

/// dotted の名前を区切りごとに mangle する(空の区切りは落とす)。
fn mangle_dotted(dotted: &str) -> String {
    dotted.split('.').filter(|part| !part.is_empty()).map(mangle).collect::<Vec<_>>().join(".")
}

/// 区切りを `.` で繋ぐ(空の区切り — root 直下の `__init__` の module 名 "" — は落とす)。
fn join(parts: &[&str]) -> String {
    parts.iter().filter(|part| !part.is_empty()).copied().collect::<Vec<_>>().join(".")
}

/// 定義の完全修飾名(module + 入れ物 + 名)。
pub fn qualified_name(module: &str, definition: &Definition) -> String {
    let module = mangle_dotted(module);
    match &definition.container {
        None => join(&[&module, &definition.mangled]),
        Some(container) => join(&[&module, &mangle(container), &definition.mangled]),
    }
}

/// file が package の `__init__` か(相対 import の基準が変わる)。
fn is_package_init(path: &str) -> bool {
    let base = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    matches!(base.as_str(), "__init__.hy" | "__init__.hyk" | "__init__.hyp")
}

/// 相対 import を書いた file の module を基準に絶対の dotted 名へ直す。
fn absolute_module(file_path: &str, file_module: &str, module: &str) -> String {
    let dots = module.len() - module.trim_start_matches('.').len();
    if dots == 0 {
        return module.to_string();
    }
    let base: Vec<&str> = file_module.split('.').filter(|p| !p.is_empty()).collect();
    let package: Vec<&str> = if is_package_init(file_path) { base } else { base[..base.len().saturating_sub(1)].to_vec() };
    let keep = package.len().saturating_sub(dots - 1);
    let mut parts: Vec<String> = package[..keep].iter().map(|s| s.to_string()).collect();
    let rest = &module[dots..];
    if !rest.is_empty() {
        parts.extend(rest.split('.').map(str::to_string));
    }
    parts.join(".")
}

/// import が file の中に作る名前(別名 > 名前 > module)。
fn bound_name(import: &Import) -> &str {
    import.alias.as_deref().or(import.name.as_deref()).unwrap_or(&import.module)
}

/// 名前の解決の場 — 1 つの file の module・定義・import(他の file は見ない)。
struct Scope<'a> {
    path: &'a str,
    module: &'a str,
    definitions: &'a [Definition],
    imports: &'a [Import],
}

impl Scope<'_> {
    /// 書かれた dotted の名(`ReadInput`・`intent.ReadInput`)の完全修飾名。
    fn resolve_written(&self, written: &str) -> Option<String> {
        let (qualifier, name) = match written.rsplit_once('.') {
            Some((qualifier, name)) if !qualifier.is_empty() && !name.is_empty() => (Some(qualifier), name),
            Some(_) | None => (None, written),
        };
        self.resolve(qualifier, &mangle(name))
    }

    /// 修飾(書かれた綴り)と mangle した名の完全修飾名(解決の順は module の説明のとおり)。
    fn resolve(&self, qualifier: Option<&str>, name: &str) -> Option<String> {
        let (path, module, definitions, imports) = (self.path, self.module, self.definitions, self.imports);
        call_target(path, module, definitions, imports, qualifier, name)
    }

    /// 名 1 つの target を埋める。
    fn fill(&self, name_ref: &mut NameRef) {
        name_ref.target = self.resolve_written(&name_ref.name);
    }

    /// 型の注記の中の名の target を埋める。
    fn fill_type(&self, note: &mut TypeNote) {
        for name_ref in &mut note.names {
            self.fill(name_ref);
        }
    }
}

/// 修飾と名の完全修飾名(解決の順は module の説明のとおり)。
fn call_target(
    path: &str,
    module: &str,
    definitions: &[Definition],
    imports: &[Import],
    qualifier: Option<&str>,
    name: &str,
) -> Option<String> {
    let own_module = mangle_dotted(module);
    match qualifier {
        None => {
            if definitions.iter().any(|d| d.container.is_none() && d.mangled == name) {
                return Some(join(&[&own_module, name]));
            }
            let import = imports.iter().filter(|imp| !imp.is_require).find(|imp| mangle_dotted(bound_name(imp)) == name)?;
            let imported = import.name.as_ref()?; // module そのものは呼び先の定義ではない
            let from = mangle_dotted(&absolute_module(path, module, &import.module));
            Some(join(&[&from, &mangle(imported)]))
        }
        Some(qualifier) => {
            let q = mangle_dotted(qualifier);
            let local = definitions
                .iter()
                .any(|d| d.mangled == name && d.container.as_deref().is_some_and(|c| mangle(c) == q));
            if local {
                return Some(join(&[&own_module, &q, name]));
            }
            imports.iter().filter(|imp| !imp.is_require).find_map(|imp| {
                let bound = mangle_dotted(bound_name(imp));
                let from = mangle_dotted(&absolute_module(path, module, &imp.module));
                match &imp.name {
                    // `(import m)` の `m.f`・`m.sub.f`
                    None => {
                        let rest = if q == bound { Some("") } else { q.strip_prefix(&format!("{}.", bound)) };
                        rest.map(|rest| join(&[&from, rest, name]))
                    }
                    // `(import pkg [sub])` の `sub.f` と `(import m [Class])` の `Class.f` — どちらも `from.sub.f`
                    Some(imported) => (q == bound).then(|| join(&[&from, &mangle(imported), name])),
                }
            })
        }
    }
}

/// file の索引に完全修飾名を埋める — 定義の `qualified_name`、呼び出しの `target`、定義の effect・型・effect 節の名の `target`。
pub fn link(file: &mut HyFileIndex) {
    for definition in &mut file.definitions {
        definition.qualified_name = qualified_name(&file.module, definition);
    }
    // 解決は埋める前の定義の写しの上で行う(定義の欄を書きながら同じ定義の列を読まないため)
    let definitions = file.definitions.clone();
    let scope = Scope { path: &file.path, module: &file.module, definitions: &definitions, imports: &file.imports };
    for call in &mut file.calls {
        call.target = scope.resolve(call.qualifier.as_deref(), &call.mangled);
    }
    for definition in &mut file.definitions {
        for effect in definition.effects.iter_mut().flatten() {
            scope.fill(effect);
        }
        for param in &mut definition.param_types {
            scope.fill_type(&mut param.type_note);
        }
        if let Some(answer) = &mut definition.answer_type {
            scope.fill_type(answer);
        }
        if let Some(handles) = &mut definition.handles {
            scope.fill(handles);
        }
    }
}
