//! repo の一番上の `architecture.hy` — service と層の唯一の宣言を、実行せずに Hy の読み取り器(doeff-indexer)で読む。
//!
//! 形(細部は docs/SPECIFICATION.md の「architecture.hy」節):
//! ```hy
//! (defarchitecture agora-controllers
//!   :root "controllers"
//!   :layers [(layer core :summary "…" :knows "…" :does-not-know "…" :question "…" :roles [judgment program type]
//!                        :imports [core intent] :forbid-modules ["httpx"])
//!            (layer intent … :types-only true) (layer protocol …) (layer foundation …) (layer entry …)]
//!   :shared "shared"                 ; どの service からも読める置き場(root/shared/<層>/)
//!   :foundation "foundation"         ; service の外の層(root/foundation/)— :layers に同じ名の layer が要る
//!   :open-layers [intent]            ; 別の service から読んでよい層(Tach の interfaces に当たる)
//!   :roles {:judgment "業務の判断をする純粋な関数" …}
//!   :exclude ["tests" "__pycache__" "conftest.py"]
//!   :legacy ["controllers/agora_sim" (legacy "controllers/core" :layer core)])
//! (defservice land-notice "着地の報せ" {:depends-on [messaging] :layers [core intent protocol entry]})
//! ```
//! 読めない形(知らない鍵・重複した service・存在しない層の名)は、file の中の位置つきの理由の列で返す(設定の誤り)。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};
use doeff_indexer::hy_index::LineIndex;
use serde::Serialize;

use super::names::hy_mangle;
use super::settings::{normalize_dir, LayerDescription, LayersSection, PathPatterns, RolesSection};

/// 層 1 つの宣言。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ArchLayer {
    pub name: String,
    pub summary: Option<String>,
    pub knows: Option<String>,
    pub does_not_know: Option<String>,
    pub question: Option<String>,
    pub roles: Vec<String>,
    /// import してよい層(None = 制限しない)。
    #[serde(skip)]
    pub imports: Option<Vec<String>>,
    #[serde(skip)]
    pub forbid_modules: Vec<String>,
    #[serde(skip)]
    pub types_only: bool,
}

/// service 1 つの宣言。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ArchService {
    pub name: String,
    /// root の下の dir の名(名の `-` を `_` にした物)。
    pub dir: String,
    pub description: Option<String>,
    pub depends_on: Vec<String>,
    pub layers: Vec<String>,
    /// architecture.hy の中の defservice の位置(DOEFF117 の知らせの位置)。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 移行の途中の置き場 1 つ(まだ service → 層 に並んでいない dir)。層を添えれば、層の規則はその層として判じる。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LegacyPlace {
    pub dir: String,
    pub layer: Option<String>,
}

/// 素の関数(deff)を許す理由の種類 1 つ(`(reason 名 "説明")`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ReasonKind {
    pub name: String,
    pub description: String,
}

/// architecture.hy の全体。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Architecture {
    pub name: String,
    pub root: String,
    pub layers: Vec<ArchLayer>,
    pub shared: Option<String>,
    pub foundation: Option<String>,
    pub open_layers: Vec<String>,
    pub legacy: Vec<LegacyPlace>,
    pub services: Vec<ArchService>,
    /// 素の関数を許す理由の種類の閉じた一覧(DOEFF111・DOEFF203)。
    pub plain_callable_reasons: Vec<ReasonKind>,
    #[serde(skip)]
    pub role_descriptions: BTreeMap<String, String>,
    #[serde(skip)]
    pub exclude: Vec<String>,
    #[serde(skip)]
    pub extensions: Option<Vec<String>>,
    /// 読んだ file。
    #[serde(skip)]
    pub path: PathBuf,
}

impl Architecture {
    /// file を読んで宣言にする(読めない・形が違う時は位置つきの理由の列)。
    pub fn load(path: &Path) -> Result<Architecture, Vec<String>> {
        let source = std::fs::read_to_string(path).map_err(|e| vec![format!("{} を読めない: {}", path.display(), e)])?;
        Architecture::parse(&source, path)
    }

    /// source を宣言にする。
    pub fn parse(source: &str, path: &Path) -> Result<Architecture, Vec<String>> {
        let mut reader = Reader::new(source, 0, source.len());
        let forms = reader.read_all();
        let lines = LineIndex::new(source);
        let mut parser = Parser { src: source, lines: &lines, path, problems: Vec::new() };
        if !reader.issues.is_empty() {
            parser.problems.push(format!("{}: 括弧か文字列が閉じていない所がある", path.display()));
        }
        let mut architecture: Option<Architecture> = None;
        let mut services: Vec<ArchService> = Vec::new();
        for form in &forms {
            let Some(items) = parser.paren(form) else { continue };
            match items.first().and_then(|h| parser.symbol(h)) {
                Some("defarchitecture") => {
                    if architecture.is_some() {
                        parser.problem(form, "defarchitecture が 2 つある");
                    }
                    architecture = parser.architecture(form, &items, path);
                }
                Some("defservice") => {
                    if let Some(service) = parser.service(form, &items) {
                        if services.iter().any(|s| s.name == service.name) {
                            parser.problem(form, &format!("service {} が 2 度宣言されている", service.name));
                        }
                        services.push(service);
                    }
                }
                Some("require" | "import") => {}
                Some(other) => parser.problem(form, &format!("architecture.hy に置けない形 {}(defarchitecture と defservice だけ)", other)),
                None => {}
            }
        }
        let Some(mut architecture) = architecture else {
            parser.problems.push(format!("{}: defarchitecture が無い", path.display()));
            return Err(parser.problems);
        };
        architecture.services = services;
        parser.check(&architecture);
        if parser.problems.is_empty() {
            Ok(architecture)
        } else {
            Err(parser.problems)
        }
    }

    /// service の宣言を dir の名から引く。
    pub fn service_by_dir(&self, dir: &str) -> Option<&ArchService> {
        self.services.iter().find(|s| s.dir == dir)
    }

    /// 層の設定の節(TOML の [tool.doeff-linter.layers] と同じ形)に写す — 層の規則はこれで今どおり判じる。
    pub fn layers_section(&self) -> LayersSection {
        let root = normalize_dir(&self.root);
        let order: Vec<String> = self.layers.iter().map(|l| l.name.clone()).collect();
        let paths = self
            .layers
            .iter()
            .map(|layer| {
                let mut places = Vec::new();
                if self.foundation.as_deref() == Some(layer.name.as_str()) {
                    places.push(format!("{}/{}", root, layer.name));
                } else {
                    places.push(format!("{}/*/{}", root, layer.name));
                }
                places.extend(self.legacy.iter().filter(|l| l.layer.as_deref() == Some(layer.name.as_str())).map(|l| l.dir.clone()));
                (layer.name.clone(), PathPatterns::Many(places))
            })
            .collect();
        LayersSection {
            order,
            paths,
            exclude: self.exclude.clone(),
            extensions: self.extensions.clone(),
            allow_imports: self.layers.iter().filter_map(|l| l.imports.clone().map(|i| (l.name.clone(), i))).collect(),
            forbid_modules: self.layers.iter().filter(|l| !l.forbid_modules.is_empty()).map(|l| (l.name.clone(), l.forbid_modules.clone())).collect(),
            types_only: self.layers.iter().filter(|l| l.types_only).map(|l| l.name.clone()).collect(),
            function_definers: None,
            describe: self
                .layers
                .iter()
                .map(|l| {
                    (
                        l.name.clone(),
                        LayerDescription { summary: l.summary.clone(), knows: l.knows.clone(), does_not_know: l.does_not_know.clone(), question: l.question.clone() },
                    )
                })
                .collect(),
        }
    }

    /// role の設定の節に写す。
    pub fn roles_section(&self) -> RolesSection {
        let names: BTreeSet<String> = self.layers.iter().flat_map(|l| l.roles.iter().cloned()).collect();
        RolesSection {
            names: names.into_iter().collect(),
            by_layer: self.layers.iter().filter(|l| !l.roles.is_empty()).map(|l| (l.name.clone(), l.roles.clone())).collect(),
            describe: self.role_descriptions.clone(),
        }
    }
}

/// 読み取りの道具(位置つきの理由を積む)。
struct Parser<'a> {
    src: &'a str,
    lines: &'a LineIndex<'a>,
    path: &'a Path,
    problems: Vec<String>,
}

/// 鍵と値の組の列(`:root "x" :layers [...]`)。
type Pairs<'f> = Vec<(&'f Form, &'f Form)>;

impl<'a> Parser<'a> {
    /// form の位置つきで理由を積む(file:行:列 は 1 始まり)。
    fn problem(&mut self, form: &Form, reason: &str) {
        let at = self.lines.position(form.span.start);
        self.problems.push(format!("{}:{}:{}: {}", self.path.display(), at.line + 1, at.character + 1, reason));
    }

    /// form の綴り。
    fn text(&self, form: &Form) -> &'a str {
        self.src.get(form.span.start..form.span.end).unwrap_or("")
    }

    /// `( … )` の中身(読み捨てを除く)。
    fn paren<'f>(&self, form: &'f Form) -> Option<Vec<&'f Form>> {
        form.paren_items().map(|items| items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect())
    }

    /// `[ … ]` の中身。
    fn bracket<'f>(&self, form: &'f Form) -> Option<Vec<&'f Form>> {
        form.bracket_items().map(|items| items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect())
    }

    /// `{ … }` の中身。
    fn brace<'f>(&self, form: &'f Form) -> Option<Vec<&'f Form>> {
        match &form.node {
            Node::Seq { delim: Delim::Brace, items } => Some(items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect()),
            _ => None,
        }
    }

    /// 記号の綴り。
    fn symbol(&self, form: &Form) -> Option<&'a str> {
        matches!(form.node, Node::Symbol).then(|| self.text(form))
    }

    /// 文字列の値(普通の文字列と bracket 文字列)。
    fn string(&self, form: &Form) -> Option<String> {
        match &form.node {
            Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end).map(|t| t.replace("\\\"", "\"")),
            _ => None,
        }
    }

    /// 名(記号か文字列)。
    fn name(&self, form: &Form) -> Option<String> {
        self.symbol(form).map(str::to_string).or_else(|| self.string(form))
    }

    /// keyword と値の組の列を読む(keyword でない所・値の無い keyword は理由を積む)。
    fn pairs<'f>(&mut self, items: &[&'f Form]) -> Pairs<'f> {
        let mut out = Vec::new();
        let mut index = 0;
        while index < items.len() {
            let key = items[index];
            if !matches!(key.node, Node::Keyword) {
                self.problem(key, &format!("keyword を待っていた所に {} がある", self.text(key)));
                index += 1;
                continue;
            }
            match items.get(index + 1) {
                Some(value) => out.push((key, *value)),
                None => self.problem(key, &format!("{} に値が無い", self.text(key))),
            }
            index += 2;
        }
        out
    }

    /// 名の列(`[a b "c"]`)。
    fn names(&mut self, form: &Form, what: &str) -> Vec<String> {
        match self.bracket(form) {
            Some(items) => items
                .into_iter()
                .filter_map(|item| {
                    let name = self.name(item);
                    if name.is_none() {
                        self.problem(item, &format!("{} の要素は名(記号か文字列)", what));
                    }
                    name
                })
                .collect(),
            None => {
                self.problem(form, &format!("{} は [ … ] の列", what));
                Vec::new()
            }
        }
    }

    /// 文字列の値を要る鍵で読む。
    fn required_string(&mut self, form: &Form, what: &str) -> Option<String> {
        let value = self.string(form);
        if value.is_none() {
            self.problem(form, &format!("{} は文字列", what));
        }
        value
    }

    /// `(defarchitecture 名 :鍵 値 …)`。
    fn architecture(&mut self, form: &Form, items: &[&Form], path: &Path) -> Option<Architecture> {
        let name = items.get(1).and_then(|f| self.name(f));
        if name.is_none() {
            self.problem(form, "defarchitecture に名が無い");
        }
        let mut arch = Architecture {
            name: name.unwrap_or_default(),
            root: String::new(),
            layers: Vec::new(),
            shared: None,
            foundation: None,
            open_layers: Vec::new(),
            legacy: Vec::new(),
            services: Vec::new(),
            plain_callable_reasons: Vec::new(),
            role_descriptions: BTreeMap::new(),
            exclude: vec!["tests".into(), "__pycache__".into(), "conftest.py".into()],
            extensions: None,
            path: path.to_path_buf(),
        };
        let rest: Vec<&Form> = items.iter().skip(2).copied().collect();
        let mut open_given = false;
        for (key, value) in self.pairs(&rest) {
            match self.text(key) {
                ":root" => arch.root = self.required_string(value, ":root").unwrap_or_default(),
                ":layers" => {
                    let entries = self.bracket(value).unwrap_or_else(|| {
                        self.problem(value, ":layers は (layer …) の列");
                        Vec::new()
                    });
                    for entry in entries {
                        if let Some(layer) = self.layer(entry) {
                            if arch.layers.iter().any(|l| l.name == layer.name) {
                                self.problem(entry, &format!("層 {} が 2 度宣言されている", layer.name));
                            }
                            arch.layers.push(layer);
                        }
                    }
                }
                ":shared" => arch.shared = self.required_string(value, ":shared"),
                ":foundation" => arch.foundation = self.name(value),
                ":open-layers" => {
                    arch.open_layers = self.names(value, ":open-layers");
                    open_given = true;
                }
                ":exclude" => arch.exclude = self.names(value, ":exclude"),
                ":extensions" => arch.extensions = Some(self.names(value, ":extensions")),
                ":plain-callable-reasons" => {
                    let entries = self.bracket(value).unwrap_or_else(|| {
                        self.problem(value, ":plain-callable-reasons は (reason 名 \"説明\") の列");
                        Vec::new()
                    });
                    for entry in entries {
                        let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("reason"));
                        let reason = parts.and_then(|p| Some(ReasonKind { name: self.name(p.get(1)?)?, description: self.string(p.get(2)?)? }));
                        match reason {
                            Some(reason) if arch.plain_callable_reasons.iter().any(|r| r.name == reason.name) => {
                                self.problem(entry, &format!("理由の種類 {} が 2 度宣言されている", reason.name))
                            }
                            Some(reason) => arch.plain_callable_reasons.push(reason),
                            None => self.problem(entry, ":plain-callable-reasons の要素は (reason 名 \"説明\")"),
                        }
                    }
                }
                ":roles" => match self.brace(value) {
                    Some(entries) => {
                        for (role, text) in self.pairs(&entries) {
                            match self.string(text) {
                                Some(text) => {
                                    arch.role_descriptions.insert(self.text(role).trim_start_matches(':').to_string(), text);
                                }
                                None => self.problem(text, ":roles の値は role の説明の文字列"),
                            }
                        }
                    }
                    None => self.problem(value, ":roles は {:role \"説明\" …} の辞書"),
                },
                ":legacy" => {
                    let entries = self.bracket(value).unwrap_or_else(|| {
                        self.problem(value, ":legacy は置き場の列");
                        Vec::new()
                    });
                    for entry in entries {
                        if let Some(dir) = self.string(entry) {
                            arch.legacy.push(LegacyPlace { dir: normalize_dir(&dir), layer: None });
                            continue;
                        }
                        match self.paren(entry) {
                            Some(parts) if parts.first().and_then(|h| self.symbol(h)) == Some("legacy") => {
                                let dir = parts.get(1).and_then(|f| self.string(f));
                                let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
                                let mut layer = None;
                                for (k, v) in self.pairs(&rest) {
                                    match self.text(k) {
                                        ":layer" => layer = self.name(v),
                                        other => self.problem(k, &format!("legacy の知らない鍵 {}(:layer だけ)", other)),
                                    }
                                }
                                match dir {
                                    Some(dir) => arch.legacy.push(LegacyPlace { dir: normalize_dir(&dir), layer }),
                                    None => self.problem(entry, "(legacy \"dir\" :layer 層) の dir が無い"),
                                }
                            }
                            _ => self.problem(entry, ":legacy の要素は \"dir\" か (legacy \"dir\" :layer 層)"),
                        }
                    }
                }
                other => self.problem(key, &format!("defarchitecture の知らない鍵 {}", other)),
            }
        }
        // :open-layers を書かなければ、層 intent が在る時だけ intent を開く(既定)。
        if !open_given && arch.layers.iter().any(|l| l.name == "intent") {
            arch.open_layers = vec!["intent".to_string()];
        }
        if arch.root.is_empty() {
            self.problem(form, "defarchitecture に :root が無い");
        }
        if arch.layers.is_empty() {
            self.problem(form, "defarchitecture に :layers が無い");
        }
        Some(arch)
    }

    /// `(layer 名 :summary "…" :knows "…" :does-not-know "…" :question "…" :roles [..] :imports [..] :forbid-modules [..] :types-only true)`。
    fn layer(&mut self, form: &Form) -> Option<ArchLayer> {
        let Some(items) = self.paren(form).filter(|items| items.first().and_then(|h| self.symbol(h)) == Some("layer")) else {
            self.problem(form, ":layers の要素は (layer 名 …)");
            return None;
        };
        let Some(name) = items.get(1).and_then(|f| self.name(f)) else {
            self.problem(form, "layer に名が無い");
            return None;
        };
        let mut layer = ArchLayer {
            name,
            summary: None,
            knows: None,
            does_not_know: None,
            question: None,
            roles: Vec::new(),
            imports: None,
            forbid_modules: Vec::new(),
            types_only: false,
        };
        let rest: Vec<&Form> = items.iter().skip(2).copied().collect();
        for (key, value) in self.pairs(&rest) {
            match self.text(key) {
                ":summary" => layer.summary = self.required_string(value, ":summary"),
                ":knows" => layer.knows = self.required_string(value, ":knows"),
                ":does-not-know" => layer.does_not_know = self.required_string(value, ":does-not-know"),
                ":question" => layer.question = self.required_string(value, ":question"),
                ":roles" => layer.roles = self.names(value, ":roles"),
                ":imports" => layer.imports = Some(self.names(value, ":imports")),
                ":forbid-modules" => layer.forbid_modules = self.names(value, ":forbid-modules"),
                ":types-only" => match self.symbol(value) {
                    Some("True" | "true") => layer.types_only = true,
                    Some("False" | "false") => layer.types_only = false,
                    _ => self.problem(value, ":types-only は True か False"),
                },
                other => self.problem(key, &format!("layer の知らない鍵 {}", other)),
            }
        }
        Some(layer)
    }

    /// `(defservice 名 "説明"? {:depends-on [..] :layers [..]})`。
    fn service(&mut self, form: &Form, items: &[&Form]) -> Option<ArchService> {
        let Some(name) = items.get(1).and_then(|f| self.name(f)) else {
            self.problem(form, "defservice に名が無い");
            return None;
        };
        let range = self.lines.range(items[1].span.start, items[1].span.end);
        let mut service = ArchService { dir: hy_mangle(&name), name, description: None, depends_on: Vec::new(), layers: Vec::new(), range };
        for part in items.iter().skip(2) {
            if let Some(text) = self.string(part) {
                service.description = Some(text);
                continue;
            }
            match self.brace(part) {
                Some(entries) => {
                    for (key, value) in self.pairs(&entries) {
                        match self.text(key) {
                            ":depends-on" => service.depends_on = self.names(value, ":depends-on"),
                            ":layers" => service.layers = self.names(value, ":layers"),
                            ":dir" => service.dir = self.required_string(value, ":dir").unwrap_or_default(),
                            other => self.problem(key, &format!("defservice の知らない鍵 {}(:depends-on・:layers・:dir)", other)),
                        }
                    }
                }
                None => self.problem(part, "defservice の要素は説明の文字列か {:depends-on … :layers …} の辞書"),
            }
        }
        Some(service)
    }

    /// 名の食い違いを確かめる(存在しない層・service・foundation と shared の層)。
    fn check(&mut self, arch: &Architecture) {
        let layers: BTreeSet<&str> = arch.layers.iter().map(|l| l.name.as_str()).collect();
        let services: BTreeSet<&str> = arch.services.iter().map(|s| s.name.as_str()).collect();
        let file = self.path.display().to_string();
        let push = |problems: &mut Vec<String>, text: String| problems.push(format!("{}: {}", file, text));
        for layer in &arch.layers {
            for target in layer.imports.iter().flatten() {
                if !layers.contains(target.as_str()) {
                    push(&mut self.problems, format!("layer {} の :imports の {} は :layers に無い", layer.name, target));
                }
            }
        }
        if let Some(foundation) = &arch.foundation {
            if !layers.contains(foundation.as_str()) {
                push(&mut self.problems, format!(":foundation {} と同じ名の層が :layers に無い", foundation));
            }
        }
        for open in &arch.open_layers {
            if !layers.contains(open.as_str()) {
                push(&mut self.problems, format!(":open-layers の {} は :layers に無い", open));
            }
        }
        for legacy in &arch.legacy {
            if let Some(layer) = &legacy.layer {
                if !layers.contains(layer.as_str()) {
                    push(&mut self.problems, format!("legacy {} の :layer {} は :layers に無い", legacy.dir, layer));
                }
            }
        }
        for service in &arch.services {
            for layer in &service.layers {
                if !layers.contains(layer.as_str()) {
                    push(&mut self.problems, format!("service {} の :layers の {} は defarchitecture の :layers に無い", service.name, layer));
                }
                if arch.foundation.as_deref() == Some(layer.as_str()) {
                    push(&mut self.problems, format!("service {} の :layers に foundation の層 {} は置けない(service の外の層)", service.name, layer));
                }
            }
            for dependency in &service.depends_on {
                if !services.contains(dependency.as_str()) {
                    push(&mut self.problems, format!("service {} の :depends-on の {} は宣言した service に無い", service.name, dependency));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "判断" :roles [judgment] :imports [core intent])
           (layer intent :roles [intent] :types-only True)
           (layer foundation)]
  :shared "shared"
  :foundation foundation
  :roles {:judgment "業務の判断"}
  :legacy ["app/old" (legacy "app/core" :layer core)])
(defservice billing "請求" {:depends-on [custody] :layers [core intent]})
(defservice custody {:layers [core intent]})
"#;

    #[test]
    fn reads_the_declaration_and_maps_it_to_layer_settings() {
        let arch = Architecture::parse(GOOD, Path::new("architecture.hy")).unwrap();
        assert_eq!(arch.root, "app");
        assert_eq!(arch.services.len(), 2);
        assert_eq!(arch.services[0].depends_on, vec!["custody"]);
        assert_eq!(arch.services[0].description.as_deref(), Some("請求"));
        let section = arch.layers_section();
        assert_eq!(section.paths["core"], PathPatterns::Many(vec!["app/*/core".into(), "app/core".into()]));
        assert_eq!(section.paths["foundation"], PathPatterns::Many(vec!["app/foundation".into()]));
        assert_eq!(section.types_only, vec!["intent"]);
        assert_eq!(arch.roles_section().describe["judgment"], "業務の判断");
    }

    #[test]
    fn misreadings_are_errors_with_positions() {
        let bad = r#"(defarchitecture s :root "app" :layers [(layer core :colour "x")] :nonsense 1)
(defservice a {:layers [ghost] :uses [b]})
(defservice a {})
"#;
        let problems = Architecture::parse(bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in ["architecture.hy:1:", "layer の知らない鍵 :colour", "知らない鍵 :nonsense", "知らない鍵 :uses", "ghost", "service a が 2 度"] {
            assert!(problems.contains(needle), "{} が無い:\n{}", needle, problems);
        }
    }
}
