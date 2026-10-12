//! 1 file の実行(`--stdin --path` — 書き込み直後の hook とエディタ)が読む repo の範囲: 書いた file が名指す module(card ki-a9959efd9cf6 の残り)。
//!
//! 1 file の検査は、書いた file の中の名(呼び・型・定義)を import の束縛と module で `module.名` に解いて、repo の表(defk の名・record の欄・effect と型の
//! 世界)を引く。引く名は書いた file の中の記号を `smells::Scope::qualify` で解いた物だけなので、その module の file だけを読めば、repo の全部の file を
//! 読んだ時と答えが同じ。以前は repo の Hy の file を全部読んでいた(事実の種類ごとに file 1 つずつ・約 1 万回の open — 混んだ Pod で 2〜7 秒)。
//!
//! 名指す module = file 自身の module と、file の中の記号を解いた名の module(名を `.` で切った前の段もすべて — 属性の連なりの前の段が module かもしれない)の
//! うち、repo に在る物。無い module(repo の外の package)は表にも無いので落ちる。解き方を持つのは `Scope::qualify` の 1 か所で、ここは記号を集めて渡すだけ。

use std::collections::BTreeSet;
use std::path::PathBuf;

use doeff_indexer::hy_index::reader::{Node, Reader};

use super::facts::hy_bindings;
use super::hy_files::ModuleFiles;
use super::names::module_of;
use super::smells::{children, Hy, Scope};

/// Hy の file か(記号を読む対象 — Python の file は自分の module だけを名指す)。
fn is_hy(rel: &str) -> bool {
    [".hy", ".hyk", ".hyp"].iter().any(|extension| rel.ends_with(extension))
}

/// source の中の記号の綴り(重複なし — 呼び・型・引数の名・束縛の名の全部。keyword は含めない)。
fn symbols(source: &str) -> BTreeSet<String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut found = BTreeSet::new();
    let mut pending: Vec<&doeff_indexer::hy_index::reader::Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        if let Some(symbol) = hy.symbol(form) {
            found.insert(symbol.to_string());
        }
        if !matches!(form.node, Node::Symbol) {
            pending.extend(children(form));
        }
    }
    found
}

/// 解いた名 `a.b.c.d` の module の候補 — 名を `.` で切った前の段(`a.b.c`・`a.b`・`a`)。表の鍵は `module.名` で、名そのものが module になる引きは無い
/// (名そのものを探すと、局所の変数の名ごとに無い path を探すことになる)。
fn prefixes(qualified: &str) -> impl Iterator<Item = &str> {
    qualified.rmatch_indices('.').map(move |(at, _)| &qualified[..at])
}

/// 書いた file(`rel`・`source`)が名指す module の綴り(repo に在る物と、file 自身の module)。
pub fn of_file(known: &ModuleFiles, rel: &str, source: &str) -> BTreeSet<String> {
    let own = module_of(rel);
    let mut named = BTreeSet::from([own.clone()]);
    if !is_hy(rel) {
        return named;
    }
    let bindings = hy_bindings(source, &own);
    let scope = Scope { module: &own, bindings: &bindings };
    for symbol in symbols(source) {
        let qualified = scope.qualify(&symbol);
        named.extend(prefixes(&qualified).filter(|module| known.contains(module)).map(str::to_string));
    }
    named
}

/// 名指す module(の集まり)の file(根からの path と絶対 path — path の順)。`except` の file(書いた file 自身 — disk の中身でなく stdin の中身で読む物)は除く。
pub fn files_of<'a>(known: &ModuleFiles, modules: impl IntoIterator<Item = &'a String>, except: Option<&str>) -> Vec<(String, PathBuf)> {
    let mut files: Vec<(String, PathBuf)> =
        modules.into_iter().flat_map(|module| known.files(module).to_vec()).filter(|(rel, _)| Some(rel.as_str()) != except).collect();
    files.sort();
    files.dedup();
    files
}

#[cfg(test)]
mod tests {
    use super::*;

    /// repo に在る module(`a.b` → `a/b.hy`)の木と、その上の索引。
    fn repo(modules: &[&str]) -> (tempfile::TempDir, ModuleFiles) {
        let dir = tempfile::TempDir::new().unwrap();
        for module in modules {
            let path = dir.path().join(format!("{}.hy", module.replace('.', "/")));
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "").unwrap();
        }
        let known = ModuleFiles::new(dir.path(), known_extensions(), known_skipped());
        (dir, known)
    }

    fn known_extensions() -> &'static [&'static str] {
        doeff_indexer::hy_index::HY_EXTENSIONS
    }

    fn known_skipped() -> &'static [&'static str] {
        doeff_indexer::hy_index::SKIPPED_DIRS
    }

    #[test]
    fn a_file_names_its_own_module_and_the_modules_its_symbols_resolve_to() {
        let (_dir, known) = repo(&["app.core.me", "app.core.other", "app.core.unused", "app.store"]);
        let source = "(import app.store [Row])\n(import app.core.other)\n(defk f [x] (<- r (app.core.other.step x)) (Row))\n";
        let named = of_file(&known, "app/core/me.hy", source);
        assert_eq!(named.into_iter().collect::<Vec<_>>(), vec!["app.core.me", "app.core.other", "app.store"]);
    }

    #[test]
    fn a_module_that_is_not_in_the_repo_is_not_named_and_python_files_name_only_themselves() {
        let (_dir, known) = repo(&["app.core.me"]);
        let named = of_file(&known, "app/core/me.hy", "(import httpx)\n(defn f [] (httpx.get \"x\"))\n");
        assert_eq!(named.into_iter().collect::<Vec<_>>(), vec!["app.core.me"]);
        let python = of_file(&known, "app/core/me.py", "import app.core.other\n");
        assert_eq!(python.into_iter().collect::<Vec<_>>(), vec!["app.core.me"]);
    }

    #[test]
    fn the_written_file_itself_is_left_out_of_the_files_to_read() {
        let (_dir, known) = repo(&["app.a", "app.b"]);
        let modules: BTreeSet<String> = ["app.a".to_string(), "app.b".to_string()].into();
        let files = files_of(&known, &modules, Some("app/a.hy"));
        assert_eq!(files.into_iter().map(|(rel, _)| rel).collect::<Vec<_>>(), vec!["app/b.hy"]);
    }
}
