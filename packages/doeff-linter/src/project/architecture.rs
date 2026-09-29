//! repo の一番上の `architecture.hy` — service と層の唯一の宣言を、実行せずに Hy の読み取り器(doeff-indexer)で読む。
//!
//! 形(細部は docs/SPECIFICATION.md の「architecture.hy」節):
//! ```hy
//! (defarchitecture agora-controllers
//!   :root "controllers"
//!   :layers [(layer core :summary "…" :knows "…" :does-not-know "…" :question "…" :roles [judgment program type]
//!                        :imports [core intent] :forbid-modules ["httpx"])
//!            (layer intent … :types-only true) (layer protocol …) (layer foundation …)
//!            (layer entry … :dependency-layers [intent protocol])]   ; 依存先のどの層を読んでよいか(既定 = :open-layers)
//!   :shared "shared"                 ; どの service からも読める置き場(root/shared/<層>/)
//!   :foundation "foundation"         ; service の外の層(root/foundation/)— :layers に同じ名の layer が要る
//!   :verification-environment "agora_sim"  ; 模擬の環境の置き場(root/<dir>/ 1 つ)— service ではない置き場で DOEFF114・115 にしない。他の規則は当たる
//!   :open-layers [intent]            ; 別の service から読んでよい層(Tach の interfaces に当たる)
//!   :roles {:judgment "業務の判断をする純粋な関数" …}
//!   :wire-modules ["controllers.foundation.record_client"]  ; JSON の送受信そのものを行う foundation の module(DOEFF120 が JsonValue を許す)
//!   :world-handlers [(world-handler "controllers.foundation.host:with-agora-process"  ; 外の世界に触れてよい定義の許可名簿(agora-redesign #1106)
//!                       :touches [http file clock env]           ; 触れる先(閉じた語 — WorldTouch)
//!                       :answers [HttpRequest ReadText]          ; 答える effect(省略可)
//!                       :wraps ["doeff_core_effects.os_file:os-file-handler"])]  ; 中で動かす doeff の実 I/O の handler(省略可)
//!   :exclude ["tests" "__pycache__" "conftest.py"]
//!   :shared "shared")
//! (defservice land-notice "着地の報せ" {:depends-on [messaging] :layers [core intent protocol entry]})
//! ```
//! 読めない形(重複した service・存在しない層の名・廃止した鍵)は、file の中の位置つきの理由の列で返す(設定の誤り)。
//! この binary の知らない鍵は誤りにせず、その鍵だけを読まずに知らせ(`Architecture::notices` → DOEFF100)として残す
//! (宣言は binary より先に進むことがある — agora-redesign #848)。

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
    /// この層の module が、:depends-on に宣言した依存先の service のどの層を読んでよいか(None = :open-layers)。
    /// 例: 組み立ての層 entry は依存先の intent と protocol(翻訳の handler)を読んで全体を組む(operator 2026-09-28 "A okay")。
    #[serde(skip)]
    pub dependency_layers: Option<Vec<String>>,
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
    /// 公開の契約の形(`:public-contract`)。書かない = in-process(他の service が `:depends-on` に載せて読める)。
    pub public_contract: PublicContract,
    /// architecture.hy の中の defservice の位置(DOEFF117 の知らせの位置)。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// service の公開の契約の形(`defservice` の `:public-contract`)。
/// `Http` の service は、公開の契約が HTTP の口だけで、他の service は `:depends-on` に載せて in-process で読まない
/// (置き場の状態を持つ service の近道を止める — agora-redesign #978 の設計 artifact-store v10)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum PublicContract {
    InProcess,
    Http,
}

/// 素の関数(deff)を許す理由の種類 1 つ(`(reason 名 "説明")`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ReasonKind {
    pub name: String,
    pub description: String,
    /// 受け入れない理由の型の直し方(`:fix "…"` — 受け入れる理由には無い)。
    pub fix: Option<String>,
}

/// 外の世界の触れる先の種類(`:world-handlers` の `:touches` の閉じた語)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum WorldTouch {
    Http,
    Db,
    File,
    Process,
    Clock,
    Env,
    Cluster,
    Network,
    Thread,
}

impl WorldTouch {
    pub const ALL: [WorldTouch; 9] = [
        WorldTouch::Http,
        WorldTouch::Db,
        WorldTouch::File,
        WorldTouch::Process,
        WorldTouch::Clock,
        WorldTouch::Env,
        WorldTouch::Cluster,
        WorldTouch::Network,
        WorldTouch::Thread,
    ];

    pub fn name(self) -> &'static str {
        match self {
            WorldTouch::Http => "http",
            WorldTouch::Db => "db",
            WorldTouch::File => "file",
            WorldTouch::Process => "process",
            WorldTouch::Clock => "clock",
            WorldTouch::Env => "env",
            WorldTouch::Cluster => "cluster",
            WorldTouch::Network => "network",
            WorldTouch::Thread => "thread",
        }
    }

    pub fn parse(text: &str) -> Option<WorldTouch> {
        WorldTouch::ALL.into_iter().find(|t| t.name() == text)
    }
}

/// 定義 1 つの名指し(`"module.path:名"` — module は `.` 区切り・名は Hy の綴りのまま)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct DefinitionRef {
    pub module: String,
    pub name: String,
}

impl DefinitionRef {
    /// `"a.b.c:名"` を読む(module の段が空・名が空・空白を含む綴りは None)。
    pub fn parse(text: &str) -> Option<DefinitionRef> {
        let (module, name) = text.split_once(':')?;
        let module_ok = !module.is_empty() && module.split('.').all(|segment| !segment.is_empty() && !segment.contains(char::is_whitespace) && !segment.contains('*'));
        let name_ok = !name.is_empty() && !name.contains(char::is_whitespace) && !name.contains(':');
        (module_ok && name_ok).then(|| DefinitionRef { module: module.to_string(), name: name.to_string() })
    }

    pub fn spelling(&self) -> String {
        format!("{}:{}", self.module, self.name)
    }

    /// module の綴りを mangle した dotted の綴り(索引の file の module・完全修飾名の module の部分と同じ形)。
    pub fn mangled_module(&self) -> String {
        mangle_dotted(&self.module)
    }

    /// 完全修飾名(索引の定義の `qualified_name`・呼び出しと参照の `target` と同じ綴り)。
    pub fn target(&self) -> String {
        format!("{}.{}", self.mangled_module(), doeff_indexer::hy_index::mangle(&self.name))
    }
}

/// dotted の綴りを段ごとに mangle する。
pub fn mangle_dotted(dotted: &str) -> String {
    dotted.split('.').filter(|part| !part.is_empty()).map(doeff_indexer::hy_index::mangle).collect::<Vec<_>>().join(".")
}

/// 外の世界に触れてよい定義 1 つ(`:world-handlers` の `(world-handler "module:名" :touches [..] :answers [..] :wraps [..])`)。
/// 名簿の定義の下でだけ実 I/O の答え手(Python の生の I/O と、:wraps に挙げた doeff の実 I/O の handler)が動く(agora-redesign #1106)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorldHandler {
    pub definition: DefinitionRef,
    pub touches: Vec<WorldTouch>,
    /// 答える effect の名(書かなくてよい)。
    pub answers: Vec<String>,
    /// 中で動かす doeff の実 I/O の handler(書かなくてよい)。
    pub wraps: Vec<DefinitionRef>,
    /// architecture.hy の中の位置。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// architecture.hy の全体。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Architecture {
    pub name: String,
    pub root: String,
    pub layers: Vec<ArchLayer>,
    pub shared: Option<String>,
    pub foundation: Option<String>,
    /// 模擬の環境の置き場(root の直下の dir 1 つ・`:verification-environment "agora_sim"`)— 本番の組み立てのまま handler だけを
    /// 差し替えて全 service を走らせる検証の環境は、全 service の core と entry を読むのでどの service にも属さない(R8 の scripts/ と
    /// 同じ理屈)。この dir の下の module は DOEFF114・115 で置き場所の違反にしない。ほかの規則は今までどおり当たる。
    /// 1 つだけ受ける(列は受けない — 何でも逃がせる欄にしない。:legacy を廃した理由と同じ)。
    pub verification_environment: Option<String>,
    pub open_layers: Vec<String>,
    pub services: Vec<ArchService>,
    /// 素の関数を許す理由の種類の閉じた一覧(DOEFF203 の受け入れる答え)。
    pub plain_callable_reasons: Vec<ReasonKind>,
    /// 受け入れない理由の型と直し方(DOEFF203 の受け入れない答え — 設定の読み込み・検の補助・組み立て …)。
    pub rejected_plain_callable_reasons: Vec<ReasonKind>,
    /// JSON の送受信そのものを行う module の綴りの pattern(`.` 区切りの module の綴り・`*` は段の中の任意の綴り・`**` は 0 個以上の段)。
    /// DOEFF120 は、ここに当たり、かつ foundation の層に在る module にだけ JsonValue を許す。
    pub wire_modules: Vec<String>,
    /// 外の世界に触れてよい定義の許可名簿(`:world-handlers` — 空 = 宣言していない)。
    pub world_handlers: Vec<WorldHandler>,
    /// 「縁」のテスト(外の世界に触れる handler に届くテスト)が持つ pytest の印の名(`:edge-mark "real_world"`)。
    /// 書けば DOEFF133 が、テストの届く先から導いた種類と印の有無の食い違いを出す(agora-redesign #1106 の R3)。
    pub edge_mark: Option<String>,
    /// 縁と数える触れる先(`:edge-touches [http db …]` — 書かなければ全部)。agora は file・env を入れない
    /// (一時 dir の file は手元 — agora-redesign #1142 の決定 B)。仕組みは linter・値は repo の宣言。
    pub edge_touches: Option<Vec<WorldTouch>>,
    #[serde(skip)]
    pub role_descriptions: BTreeMap<String, String>,
    #[serde(skip)]
    pub exclude: Vec<String>,
    #[serde(skip)]
    pub extensions: Option<Vec<String>>,
    /// 読んだ file。
    #[serde(skip)]
    pub path: PathBuf,
    /// この binary の知らない鍵(読まずに残りを読んだ — DOEFF100 で知らせる)。
    #[serde(skip)]
    pub notices: Vec<super::notice::ConfigNotice>,
}

impl Architecture {
    /// 層 reader の module が、依存先の service の中で読んでよい層(層の :dependency-layers、無ければ :open-layers)。
    pub fn dependency_layers_for(&self, reader: &str) -> &[String] {
        self.layers
            .iter()
            .find(|l| l.name == reader)
            .and_then(|l| l.dependency_layers.as_deref())
            .unwrap_or(&self.open_layers)
    }

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
        let mut parser = Parser { src: source, lines: &lines, path, problems: Vec::new(), unknown: Vec::new() };
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
        architecture.notices = parser
            .unknown
            .iter()
            .map(|(offset, key)| super::notice::ConfigNotice {
                file: path.to_path_buf(),
                key: key.clone(),
                kind: super::notice::NoticeKind::Key,
                range: crate::position::line_range(source, *offset),
            })
            .collect();
        parser.check(&architecture);
        if parser.problems.is_empty() {
            Ok(architecture)
        } else {
            Err(parser.problems)
        }
    }

    /// 許可名簿の定義の module(mangle した dotted の綴り)— 生の副作用を許す所(DOEFF106)。
    pub fn world_modules(&self) -> BTreeSet<String> {
        self.world_handlers.iter().map(|h| h.definition.mangled_module()).collect()
    }

    /// 縁と数える触れる先(`:edge-touches`・書かなければ全部)。
    pub fn counts_as_edge(&self, touches: &[WorldTouch]) -> bool {
        match &self.edge_touches {
            None => !touches.is_empty(),
            Some(edge) => touches.iter().any(|t| edge.contains(t)),
        }
    }

    /// 目録の doeff の実 I/O の handler の完全修飾名 → (綴り, それを :wraps に挙げた名簿の定義の綴りの列, 触れる先)。
    pub fn world_targets(&self, catalog: &super::world_catalog::WorldCatalog) -> BTreeMap<String, (String, Vec<String>, Vec<WorldTouch>)> {
        let wrapped = self.wrapped_targets();
        catalog
            .handlers
            .iter()
            .map(|(target, handler)| {
                let by = wrapped.get(target).map(|(_, by)| by.clone()).unwrap_or_default();
                (target.clone(), (handler.definition.spelling(), by, handler.touches.clone()))
            })
            .collect()
    }

    /// 許可名簿の定義の完全修飾名 → 綴り。
    pub fn world_definition_targets(&self) -> BTreeMap<String, String> {
        self.world_handlers.iter().map(|h| (h.definition.target(), h.definition.spelling())).collect()
    }

    /// :wraps に挙げた doeff の実 I/O の handler の完全修飾名 → (綴り, 挙げた名簿の定義の綴りの列)。
    pub fn wrapped_targets(&self) -> BTreeMap<String, (String, Vec<String>)> {
        let mut out: BTreeMap<String, (String, Vec<String>)> = BTreeMap::new();
        for handler in &self.world_handlers {
            for wrapped in &handler.wraps {
                out.entry(wrapped.target()).or_insert_with(|| (wrapped.spelling(), Vec::new())).1.push(handler.definition.spelling());
            }
        }
        out
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
    /// この binary の知らない鍵(byte の位置と、どの形の鍵か)。
    unknown: Vec<(usize, String)>,
}

/// 鍵と値の組の列(`:root "x" :layers [...]`)。
type Pairs<'f> = Vec<(&'f Form, &'f Form)>;

impl<'a> Parser<'a> {
    /// この binary の知らない鍵を知らせとして積む(誤りにせず、その鍵の値は読まない)。
    fn unknown_key(&mut self, key: &Form, owner: &str) {
        let text = self.text(key).to_string();
        self.unknown.push((key.span.start, format!("{} {}", owner, text)));
    }

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
            verification_environment: None,
            open_layers: Vec::new(),
            services: Vec::new(),
            plain_callable_reasons: Vec::new(),
            rejected_plain_callable_reasons: Vec::new(),
            wire_modules: Vec::new(),
            world_handlers: Vec::new(),
            edge_mark: None,
            edge_touches: None,
            role_descriptions: BTreeMap::new(),
            exclude: vec!["tests".into(), "__pycache__".into(), "conftest.py".into()],
            extensions: None,
            path: path.to_path_buf(),
            notices: Vec::new(),
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
                ":verification-environment" => arch.verification_environment = self.required_string(value, ":verification-environment"),
                ":open-layers" => {
                    arch.open_layers = self.names(value, ":open-layers");
                    open_given = true;
                }
                ":exclude" => arch.exclude = self.names(value, ":exclude"),
                ":extensions" => arch.extensions = Some(self.names(value, ":extensions")),
                ":plain-callable-reasons" => arch.plain_callable_reasons = self.reasons(value, ":plain-callable-reasons"),
                ":rejected-plain-callable-reasons" => {
                    arch.rejected_plain_callable_reasons = self.reasons(value, ":rejected-plain-callable-reasons")
                }
                ":wire-modules" => arch.wire_modules = self.module_patterns(value, ":wire-modules"),
                ":world-handlers" => arch.world_handlers = self.world_handlers(value),
                ":edge-touches" => {
                    let mut touches = Vec::new();
                    for word in self.names(value, ":edge-touches") {
                        match WorldTouch::parse(&word) {
                            Some(touch) if !touches.contains(&touch) => touches.push(touch),
                            Some(_) => self.problem(value, &format!(":edge-touches の {} が 2 度書かれている", word)),
                            None => self.problem(value, &format!(":edge-touches の {} は語の外", word)),
                        }
                    }
                    arch.edge_touches = Some(touches);
                }
                ":edge-mark" => {
                    arch.edge_mark = self.required_string(value, ":edge-mark");
                    let well_formed = arch.edge_mark.as_deref().is_some_and(|m| !m.is_empty() && m.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'));
                    if arch.edge_mark.is_some() && !well_formed {
                        self.problem(value, ":edge-mark は pytest の印の名(英数字と _ — 例 \"real_world\")");
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
                // :legacy は廃止(operator 2026-09-27 逐語 "we dont want 'legacy' stuff. we want anything all flagged")— 宣言の外の module は
                // 全部 DOEFF114・115 で出し、既存の分は登録簿で受ける。
                ":legacy" => self.problem(
                    key,
                    ":legacy は廃止した — 宣言の外の置き場所の module は全部 DOEFF114・115 で出す。既存の分は登録簿(registry)に載せる",
                ),
                _ => self.unknown_key(key, "defarchitecture"),
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

    /// module の綴りの pattern の列(`["controllers.foundation.record_client" "controllers.foundation.http.*"]`)を読む。
    /// `.` 区切りの module の綴りで、段は空にできない。path(`/`)と、段の中に `**` を混ぜた綴りは理由を積む。
    fn module_patterns(&mut self, value: &Form, what: &str) -> Vec<String> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!("{} は module の綴りの列 [\"a.b.c\" …]", what));
            return Vec::new();
        };
        let mut out = Vec::new();
        for item in items {
            let Some(pattern) = self.name(item) else {
                self.problem(item, &format!("{} の要素は module の綴り(記号か文字列)", what));
                continue;
            };
            let well_formed = !pattern.contains('/')
                && pattern.split('.').all(|segment| !segment.is_empty() && (segment == "**" || !segment.contains("**")));
            if !well_formed {
                self.problem(
                    item,
                    &format!("{} の {} は module の綴り(`.` 区切り・`*` は段の中の任意の綴り・`**` は 0 個以上の段)で書く — path の `/` と空の段は使えない", what, pattern),
                );
                continue;
            }
            if out.contains(&pattern) {
                self.problem(item, &format!("{} の {} が 2 度書かれている", what, pattern));
                continue;
            }
            out.push(pattern);
        }
        out
    }

    /// `"module:名"` の綴り 1 つを読む(読めなければ理由を積む)。
    fn definition_ref(&mut self, form: &Form, what: &str) -> Option<DefinitionRef> {
        let Some(text) = self.name(form) else {
            self.problem(form, &format!("{} は \"module:名\" の文字列", what));
            return None;
        };
        let found = DefinitionRef::parse(&text);
        if found.is_none() {
            self.problem(form, &format!("{} の {} は \"module.path:名\" の綴り(module は `.` 区切りで段が空でない・名は空でない)", what, text));
        }
        found
    }

    /// 許可名簿 `[(world-handler "module:名" :touches [..] :answers [..] :wraps [..]) …]` を読む。
    fn world_handlers(&mut self, value: &Form) -> Vec<WorldHandler> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, ":world-handlers は (world-handler \"module:名\" :touches [..]) の列");
            Vec::new()
        });
        let mut out: Vec<WorldHandler> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("world-handler"));
            let Some(parts) = parts else {
                self.problem(entry, ":world-handlers の要素は (world-handler \"module:名\" :touches [..] :answers [..]? :wraps [..]?)");
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "world-handler に \"module:名\" が無い");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "world-handler") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut handler = WorldHandler { definition, touches: Vec::new(), answers: Vec::new(), wraps: Vec::new(), range };
            let mut touches_given = false;
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":touches" => {
                        touches_given = true;
                        for word in self.names(field, ":touches") {
                            match WorldTouch::parse(&word) {
                                Some(touch) if handler.touches.contains(&touch) => {
                                    self.problem(field, &format!("world-handler {} の :touches の {} が 2 度書かれている", handler.definition.spelling(), word))
                                }
                                Some(touch) => handler.touches.push(touch),
                                None => {
                                    let words: Vec<&str> = WorldTouch::ALL.iter().map(|t| t.name()).collect();
                                    self.problem(field, &format!("world-handler {} の :touches の {} は語の外({} のどれか)", handler.definition.spelling(), word, words.join("・")))
                                }
                            }
                        }
                    }
                    ":answers" => handler.answers = self.names(field, ":answers"),
                    ":wraps" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                if let Some(wrapped) = self.definition_ref(item, ":wraps") {
                                    if handler.wraps.contains(&wrapped) {
                                        self.problem(item, &format!(":wraps の {} が 2 度書かれている", wrapped.spelling()));
                                    } else {
                                        handler.wraps.push(wrapped);
                                    }
                                }
                            }
                        }
                        None => self.problem(field, ":wraps は [\"module:名\" …] の列"),
                    },
                    _ => self.unknown_key(key, "world-handler"),
                }
            }
            if !touches_given || handler.touches.is_empty() {
                self.problem(entry, &format!("world-handler {} に :touches が無い(触れる先の無い定義は名簿に載せない)", handler.definition.spelling()));
            }
            if out.iter().any(|h| h.definition == handler.definition) {
                self.problem(entry, &format!("world-handler {} が 2 度宣言されている", handler.definition.spelling()));
                continue;
            }
            out.push(handler);
        }
        out
    }

    /// 理由の列 `[(reason 名 "説明" :fix "直し方"?) …]` を読む(同じ名が 2 度あれば理由を積む)。
    fn reasons(&mut self, value: &Form, what: &str) -> Vec<ReasonKind> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, &format!("{} は (reason 名 \"説明\") の列", what));
            Vec::new()
        });
        let mut out: Vec<ReasonKind> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("reason"));
            let Some(parts) = parts else {
                self.problem(entry, &format!("{} の要素は (reason 名 \"説明\" :fix \"直し方\"?)", what));
                continue;
            };
            let (Some(name), Some(description)) = (parts.get(1).and_then(|f| self.name(f)), parts.get(2).and_then(|f| self.string(f))) else {
                self.problem(entry, &format!("{} の要素は (reason 名 \"説明\" :fix \"直し方\"?)", what));
                continue;
            };
            let rest: Vec<&Form> = parts.iter().skip(3).copied().collect();
            let mut fix = None;
            for (key, text) in self.pairs(&rest) {
                match self.text(key) {
                    ":fix" => fix = self.string(text),
                    _ => self.unknown_key(key, "reason"),
                }
            }
            if out.iter().any(|r| r.name == name) {
                self.problem(entry, &format!("理由 {} が 2 度宣言されている", name));
                continue;
            }
            out.push(ReasonKind { name, description, fix });
        }
        out
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
            dependency_layers: None,
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
                ":dependency-layers" => layer.dependency_layers = Some(self.names(value, ":dependency-layers")),
                ":types-only" => match self.symbol(value) {
                    Some("True" | "true") => layer.types_only = true,
                    Some("False" | "false") => layer.types_only = false,
                    _ => self.problem(value, ":types-only は True か False"),
                },
                _ => self.unknown_key(key, "layer"),
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
        let mut service = ArchService {
            dir: hy_mangle(&name),
            name,
            description: None,
            depends_on: Vec::new(),
            layers: Vec::new(),
            public_contract: PublicContract::InProcess,
            range,
        };
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
                            ":public-contract" => match self.symbol(value) {
                                Some("http") => service.public_contract = PublicContract::Http,
                                _ => self.problem(value, ":public-contract は http だけ(書かない = in-process)"),
                            },
                            _ => self.unknown_key(key, "defservice"),
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
        let http_only: BTreeSet<&str> = arch
            .services
            .iter()
            .filter(|s| s.public_contract == PublicContract::Http)
            .map(|s| s.name.as_str())
            .collect();
        let file = self.path.display().to_string();
        let push = |problems: &mut Vec<String>, text: String| problems.push(format!("{}: {}", file, text));
        for layer in &arch.layers {
            for target in layer.imports.iter().flatten() {
                if !layers.contains(target.as_str()) {
                    push(&mut self.problems, format!("layer {} の :imports の {} は :layers に無い", layer.name, target));
                }
            }
            for target in layer.dependency_layers.iter().flatten() {
                if !layers.contains(target.as_str()) {
                    push(&mut self.problems, format!("layer {} の :dependency-layers の {} は :layers に無い", layer.name, target));
                }
            }
        }
        if let Some(foundation) = &arch.foundation {
            if !layers.contains(foundation.as_str()) {
                push(&mut self.problems, format!(":foundation {} と同じ名の層が :layers に無い", foundation));
            }
        }
        if let Some(place) = &arch.verification_environment {
            if place.is_empty() || place.contains('/') {
                push(&mut self.problems, format!(":verification-environment {:?} は root の直下の dir の名 1 つ(`/` を含まない)", place));
            }
            if arch.shared.as_deref() == Some(place.as_str()) || arch.foundation.as_deref() == Some(place.as_str()) {
                push(&mut self.problems, format!(":verification-environment {} は shared・foundation と同じ名にできない", place));
            }
            if arch.services.iter().any(|s| s.dir == *place) {
                push(&mut self.problems, format!(":verification-environment {} は宣言した service の dir と同じ名にできない", place));
            }
        }
        for open in &arch.open_layers {
            if !layers.contains(open.as_str()) {
                push(&mut self.problems, format!(":open-layers の {} は :layers に無い", open));
            }
        }
        // 送受信の module は foundation の層にだけ許す(DOEFF120)— foundation の無い宣言に :wire-modules を書いても何も許さないので誤り。
        if !arch.wire_modules.is_empty() && arch.foundation.is_none() {
            push(&mut self.problems, ":wire-modules を書くには :foundation が要る(JsonValue を許す送受信の module は foundation の層にだけ置く)".to_string());
        }
        // 名簿の定義は foundation の層にだけ置く(R2)— foundation の無い宣言に名簿を書いても置ける所が無いので誤り。
        if !arch.world_handlers.is_empty() && arch.foundation.is_none() {
            push(&mut self.problems, ":world-handlers を書くには :foundation が要る(外の世界に触れてよい定義は foundation の層にだけ置く)".to_string());
        }
        if arch.edge_mark.is_some() && arch.world_handlers.is_empty() {
            push(&mut self.problems, ":edge-mark を書くには :world-handlers が要る(縁のテストは名簿の定義に届くテスト)".to_string());
        }
        for handler in &arch.world_handlers {
            for wrapped in &handler.wraps {
                if arch.world_handlers.iter().any(|h| h.definition == *wrapped) {
                    push(
                        &mut self.problems,
                        format!("world-handler {} の :wraps の {} は名簿の定義 — :wraps には doeff の実 I/O の handler だけを書く", handler.definition.spelling(), wrapped.spelling()),
                    );
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
                if http_only.contains(dependency.as_str()) {
                    push(
                        &mut self.problems,
                        format!(
                            "service {} の :depends-on の {} は :public-contract http(公開の契約は HTTP の口だけ)— in-process の依存に載せず、HTTP で読む",
                            service.name, dependency
                        ),
                    );
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
  :roles {:judgment "業務の判断"})
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
        assert_eq!(section.paths["core"], PathPatterns::Many(vec!["app/*/core".into()]));
        assert_eq!(section.paths["foundation"], PathPatterns::Many(vec!["app/foundation".into()]));
        assert_eq!(section.types_only, vec!["intent"]);
        assert_eq!(arch.roles_section().describe["judgment"], "業務の判断");
    }

    #[test]
    fn misreadings_are_errors_with_positions() {
        let bad = r#"(defarchitecture s :root "app" :layers [(layer core :colour "x")] :nonsense 1 :legacy ["app/old"])
(defservice a {:layers [ghost] :uses [b]})
(defservice a {})
"#;
        let problems = Architecture::parse(bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in ["architecture.hy:1:", ":legacy は廃止した", "ghost", "service a が 2 度"] {
            assert!(problems.contains(needle), "{} が無い:\n{}", needle, problems);
        }
        assert!(!problems.contains(":colour") && !problems.contains(":nonsense"), "知らない鍵を誤りにした:\n{}", problems);
    }

    #[test]
    fn unknown_keys_are_notices_and_the_rest_is_read() {
        // この binary より新しい宣言(知らない鍵)は誤りにせず、その鍵だけを読まずに知らせる(agora-redesign #848)。
        let newer = GOOD
            .replace("(layer foundation)]", "(layer foundation :future-knob [x])]")
            .replace(":foundation foundation", ":foundation foundation\n  :brand-new-key 1")
            .replace("{:depends-on [custody] :layers [core intent]}", "{:depends-on [custody] :layers [core intent] :owners [me]}");
        let arch = Architecture::parse(&newer, Path::new("architecture.hy")).unwrap();
        assert_eq!(arch.services.len(), 2, "残りの宣言を読んでいない");
        assert_eq!(arch.services[0].depends_on, vec!["custody"]);
        let keys: Vec<&str> = arch.notices.iter().map(|n| n.key.as_str()).collect();
        assert_eq!(keys, vec!["layer :future-knob", "defarchitecture :brand-new-key", "defservice :owners"]);
        let lines: Vec<u32> = arch.notices.iter().map(|n| n.range.start.line).collect();
        let expect = |needle: &str| newer.lines().position(|l| l.contains(needle)).unwrap() as u32;
        assert_eq!(lines, vec![expect(":future-knob"), expect(":brand-new-key"), expect(":owners")]);
    }

    #[test]
    fn public_contract_http_is_read_and_cannot_be_depended_on() {
        // agora-redesign #978: 公開の契約が HTTP だけの service(:public-contract http)は、他の service の :depends-on に載せられない。
        let declared = GOOD.replace("{:depends-on [custody] :layers [core intent]}", "{:layers [core intent]}");
        let with_http = declared.replace("(defservice custody", "(defservice custody {:public-contract http})\n(defservice custody-old");
        let arch = Architecture::parse(&with_http, Path::new("architecture.hy")).unwrap();
        let custody = arch.services.iter().find(|s| s.name == "custody").unwrap();
        assert_eq!(custody.public_contract, PublicContract::Http);
        assert!(arch.services.iter().filter(|s| s.name != "custody").all(|s| s.public_contract == PublicContract::InProcess));
        assert!(arch.notices.is_empty(), "知らない鍵として知らせた: {:?}", arch.notices);

        let depended = GOOD.replace("(defservice custody", "(defservice custody {:public-contract http})\n(defservice custody-old");
        let problems = Architecture::parse(&depended, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":depends-on の custody は :public-contract http"), "依存を止めていない:\n{}", problems);

        let bad = GOOD.replace("(defservice custody", "(defservice custody {:public-contract grpc})\n(defservice custody-old");
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":public-contract は http だけ"), "語彙の外を通した:\n{}", problems);
    }

    #[test]
    fn wire_modules_are_dotted_module_patterns_and_need_a_foundation() {
        let good = GOOD.replace(":foundation foundation", ":foundation foundation :wire-modules [\"app.foundation.records_client\" app.foundation.http.*]");
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        assert_eq!(arch.wire_modules, vec!["app.foundation.records_client", "app.foundation.http.*"]);
        let bad = r#"(defarchitecture s :root "app" :layers [(layer core)] :wire-modules ["app/foundation/x.hy" "a..b" "a.x**" "ok.one" "ok.one"])"#;
        let problems = Architecture::parse(bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in ["app/foundation/x.hy は module の綴り", "a..b は module の綴り", "a.x** は module の綴り", "ok.one が 2 度", ":wire-modules を書くには :foundation が要る"] {
            assert!(problems.contains(needle), "{} が無い:\n{}", needle, problems);
        }
    }

    #[test]
    fn world_handlers_are_read_with_a_closed_vocabulary_of_touches() {
        let good = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http file clock]
                     :answers [HttpRequest ReadText]
                     :wraps ["doeff_core_effects.os_file:os-file-handler" "doeff_core_effects.http_handlers:http-production-handler"])
                   (world-handler "app.foundation.agent:claude-runtime" :touches [process])]"#,
        );
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        assert!(arch.notices.is_empty(), "知らない鍵として知らせた: {:?}", arch.notices);
        assert_eq!(arch.world_handlers.len(), 2);
        let host = &arch.world_handlers[0];
        assert_eq!(host.definition, DefinitionRef { module: "app.foundation.host".into(), name: "with-host".into() });
        assert_eq!(host.touches, vec![WorldTouch::Http, WorldTouch::File, WorldTouch::Clock]);
        assert_eq!(host.answers, vec!["HttpRequest", "ReadText"]);
        assert_eq!(host.wraps[0].spelling(), "doeff_core_effects.os_file:os-file-handler");
        assert!(arch.world_handlers[1].wraps.is_empty() && arch.world_handlers[1].answers.is_empty());
        let line = good.lines().position(|l| l.contains("app.foundation.host:with-host")).unwrap() as u32;
        assert_eq!(host.range.start.line, line);
    }

    #[test]
    fn world_handler_misreadings_are_errors() {
        let bad = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http smoke http])
                   (world-handler "app.foundation.host:with-host" :touches [file])
                   (world-handler "app..x:y" :touches [file])
                   (world-handler "app.foundation.pure:answer")
                   (world-handler "app.foundation.outer:outer" :touches [file] :wraps ["app.foundation.host:with-host" "nocolon"])
                   (defk nope)]"#,
        );
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in [
            ":touches の smoke は語の外",
            ":touches の http が 2 度",
            "world-handler app.foundation.host:with-host が 2 度宣言",
            "app..x:y は \"module.path:名\" の綴り",
            "world-handler app.foundation.pure:answer に :touches が無い",
            ":wraps の app.foundation.host:with-host は名簿の定義",
            ":wraps の nocolon は",
            ":world-handlers の要素は (world-handler",
        ] {
            assert!(problems.contains(needle), "{} が無い:\n{}", needle, problems);
        }
        let no_foundation = r#"(defarchitecture s :root "app" :layers [(layer core)] :world-handlers [(world-handler "app.x:y" :touches [file])])"#;
        let problems = Architecture::parse(no_foundation, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":world-handlers を書くには :foundation が要る"), "{}", problems);
    }
}
