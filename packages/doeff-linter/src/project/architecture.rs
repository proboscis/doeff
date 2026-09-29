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

/// 決めた材料だけで判じる定義 1 つ(`:blind-definitions` の `(blind "module:名" :forbid-words [..] :no-imports True :allow-requires [..]
/// :why "…")` — DOEFF141・agora-redesign #1368)。定義から呼び出しと名指しで推移的に届く repo の Hy の定義(入れ子を含む)の本体に
/// :forbid-words の綴りが現れない(註は除く・部分一致)こと、:no-imports なら定義の module が import と require を持たない
/// (:allow-requires に挙げた module の require だけは macro の読み込みなので許す)ことを求める。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BlindDefinition {
    pub definition: DefinitionRef,
    pub forbid_words: Vec<String>,
    pub no_imports: bool,
    pub allow_requires: Vec<String>,
    /// なぜその語を読まないか(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 呼んでよい頭を決めた定義 1 つ(`:allowed-heads` の `(allowed-heads "module:名" :heads [..] :why "…")` — DOEFF147・
/// agora-redesign #1372・#1413)。定義の form の中(入れ子を含む・文字列と註を除く)の `( … )` の頭の綴りが :heads に在ることを求める。
/// 例外を受け止める境界の外で動く定義(境界そのもの・断りを組む 1 点)に、例外を上げうる呼びを入れないための宣言。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AllowedHeads {
    pub definition: DefinitionRef,
    /// 呼んでよい頭の綴り(記号と keyword — `defk`・`:`・`<-`・`.get` など、form の頭に書く綴りのまま)。
    pub heads: Vec<String>,
    /// なぜこの頭だけか(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 頭を呼んでよい場所 1 つ(`:call-sites` の `(site "module:名" :count N :parent "頭" :branch "名")`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CallSiteSite {
    pub definition: DefinitionRef,
    /// この定義の中の呼びの数(書かなければ数えない)。
    pub count: Option<usize>,
    /// この定義の中の呼びは、直ぐ外の form の頭がこの綴りの物の直の要素(例: `(try-handler (serve config))` — 最も内側)。
    pub parent: Option<String>,
    /// この定義の中の呼びは、cond・when・if の分岐のうち条件の form にこの記号が在る枝の中。
    pub branch: Option<String>,
    /// architecture.hy の中の位置。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 頭を呼んでよい場所と回数を決めた綴り 1 つ(`:call-sites` の `(call-site "頭" :files [..] :except [..] :sites [(site …) …] :why "…")` —
/// DOEFF159・agora-redesign #1372・#1414)。:files の Hy の file(:except を除く)の中の `(頭 …)` の呼びは、どれも :sites の定義の中に在る。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CallSite {
    pub head: String,
    /// 探す file(repo の根からの path の glob — :retired-words と同じ形)。
    pub files: Vec<String>,
    pub except: Vec<String>,
    pub sites: Vec<CallSiteSite>,
    /// なぜここだけか(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 系の値と系を回す入口(`:systems`)。defsystem の定義は書かなくても系の値。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Systems {
    /// 系の値を組む道具(この呼び出しの引数の中の土台は、系の値として運ばれるだけで、この場では回らない)。
    pub carriers: Vec<DefinitionRef>,
    /// 系の値を回す入口(ここに届く検だけが、系の値の中の土台を回す)。
    pub runners: Vec<DefinitionRef>,
}

/// テストの形の決まり(`:test-forms`)— 綴りの型は repo の根からの path の glob(`**` は 0 個以上の段・`/` の無い型は file の名)。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct TestForms {
    /// テストの file(この中の Python の `def test_*` と module ごとの skip を出す)。
    pub tests: Vec<String>,
    /// pytest の外で走る検査の script(在るだけで出す)。
    pub check_scripts: Vec<String>,
    /// deftest を自分で回す runner(在るだけで出す)。
    pub runners: Vec<String>,
}

/// 使わないと決めた綴りをどこで探すか(`:in` — 閉じた 3 つ)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum WordPlace {
    /// file の行の全部(註・文字列・文書を含む — 既定)。
    Lines,
    /// 定義の名だけ(Hy の `def…` の形と `setv`・`val`・`var` の左辺・Python の def と class の名)。
    Names,
    /// file の名だけ(最後の `.` より前 — dir の名と中身は見ない)。退役した名の file を置き直さないため(agora-redesign #1369)。
    Paths,
}

/// 使わないと決めた綴りの群 1 つ(`:retired-words` の `(retired-words "名" :words [..] :patterns [..] :files [..] …)` — DOEFF150)。
/// :files と :except は repo の根からの path の glob で、根に錨を下ろす(`**` は 0 個以上の段・`*` は段の中の任意の綴り・
/// `/` の無い型は根の直下の file だけ)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RetiredWords {
    /// 群の名(知らせと、:patterns の当たりの登録簿の鍵の細目)。
    pub name: String,
    /// 語として単独で在る綴り(前後が英字・`_`・`-` でない所 — 別の語の一部は数えない)。登録簿の鍵の細目は語そのもの。
    pub words: Vec<String>,
    /// 行ごとに当てる正規表現(読めることは読む時に確かめる)。
    pub patterns: Vec<String>,
    pub files: Vec<String>,
    pub except: Vec<String>,
    /// この綴りを含む行は数えない(規則そのものを述べる行 — :in lines の時だけ効く)。
    pub rule_lines: Vec<String>,
    pub place: WordPlace,
    /// 代わりに使う語・直し方(知らせの文に入れる)。
    pub instead: String,
}

/// 使わないと決めた呼びの群 1 つ(`:retired-calls` の `(retired-calls "名" :calls [..] :files [..] :except [..] :instead "…")` — DOEFF151)。
/// Hy の file の `(名 …)` の形の呼びだけを数える(註・文字列・`#_` で読み捨てた form は数えない)。glob は :retired-words と同じ。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RetiredCalls {
    pub name: String,
    /// 呼びの頭の綴り(書かれたとおり — `time.time` は `(time.time …)` に当たる)。登録簿の鍵の細目は呼びの綴り。
    pub calls: Vec<String>,
    pub files: Vec<String>,
    pub except: Vec<String>,
    pub instead: String,
}

/// handler の引数の決まり(`:handler-arguments {:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark "…" :value-types [..]}`)
/// — DOEFF142 の母集団と、repo の語(店の名・残す理由の註の印・値として扱う外の型)。client・可変の入れ物・値の型の既定は linter が持つ
/// (Python の一般の名だけ)。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct HandlerArguments {
    /// 判じる file の綴りの型(repo の根からの glob)。
    pub files: Vec<String>,
    /// 外す file の綴りの型。
    pub exclude: Vec<String>,
    /// 型の注記の無い引数を店と読む名。
    pub store_names: Vec<String>,
    /// 型の注記の無い引数を店と読む名の末尾。
    pub store_suffixes: Vec<String>,
    /// handler の本文にこの綴りの註が在れば、その handler の引数は数えない(引数に残す理由の印)。
    pub keep_mark: Option<String>,
    /// repo の索引に無くても値として扱う型の名(外の package の frozen の型)。
    pub value_types: Vec<String>,
}

/// file 1 つで判じる規則の母集団(`{:files [..] :except [..]}` — DOEFF144 の :typed-values・DOEFF145 の :record-stubs)。
/// glob は :retired-words と同じく repo の根に錨を下ろす(`**` は 0 個以上の段・`*` は段の中の任意の綴り・`/` の無い型は根の直下の file)。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct FileSelection {
    pub files: Vec<String>,
    pub except: Vec<String>,
}

/// 判定を 1 か所に閉じ込めた語彙の群 1 つ(`:single-point-vocabulary` の
/// `(vocabulary-scope "名" :patterns [r"…"] :files [..] :except [..] :instead "…")` — DOEFF146)。
/// :files と :except は repo の根からの path の glob(:retired-words と同じ形)。:except の file だけがこの語彙を読める
/// (そこが判定の 1 点)— 他の :files に当たった file がこの語彙を読むと、判定の第 2 の点になる。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VocabularyScope {
    /// 群の名(登録簿の鍵の細目)。
    pub name: String,
    /// 行ごとに当てる正規表現(読めることは読む時に確かめる)。
    pub patterns: Vec<String>,
    pub files: Vec<String>,
    /// 判定の 1 点(この語彙を読んでよい file — 空にしない)。
    pub except: Vec<String>,
    /// 直し方(知らせの文に入れる — 例「slice.hy の答えを読む」)。
    pub instead: String,
}

/// 書いてよい file を決めた綴りの群 1 つ(`:confined-spellings` の
/// `(confined-spelling "名" :patterns [r"…"] :files [..] :except [..] :why "…")` — DOEFF148・agora-redesign #1373・#1436)。
/// :files と :except は repo の根からの path の glob。:except の file だけがこの綴りを書ける(空なら :files のどこにも書かない)。
/// 註を落とした本文を読み、文字列の中は数える(DOEFF146 は文字列を数えない — 外の口の動詞や route の綴りは文字列に在る)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ConfinedSpelling {
    /// 群の名(登録簿の鍵の細目)。
    pub name: String,
    /// 本文に当てる正規表現(読めることは読む時に確かめる)。
    pub patterns: Vec<String>,
    pub files: Vec<String>,
    /// この綴りを書いてよい file(空 = :files のどこにも書かない)。
    pub except: Vec<String>,
    /// なぜこの file だけか(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置(:files に当たる file が無い時の当たりの位置)。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// effect の宣言の全体 1 つ(`:effect-census` の `(effect-census "名" :files [..] :effects [..] :base "EffectBase" :why "…")` —
/// DOEFF162・agora-redesign #1373・#1438)。:files の Hy・Python の file で :base を継ぐ class の宣言を集め、:effects の一覧と比べる。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EffectCensus {
    /// 宣言の名(登録簿の鍵の細目)。
    pub name: String,
    pub files: Vec<String>,
    /// effect の一覧(class の名)。
    pub effects: Vec<String>,
    /// effect の class が継ぐ基底の名(書かなければ EffectBase)。
    pub base: String,
    /// なぜ一覧で閉じるか(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置(宣言の無い effect の当たりの位置)。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 型の欄を持つ class の顔ぶれ 1 つ(`:field-holders` の
/// `(field-holders "名" :type "T" :files [..] :classes [..] :holders [..] :why "…")` — DOEFF149・agora-redesign #1374)。
/// :files の Python の file の module の直下の class のうち、欄の注記に :type の綴りが語として在る class を :holders と比べる。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FieldHolders {
    /// 宣言の名(登録簿の鍵の細目)。
    pub name: String,
    /// 欄の注記の中で数える型の綴り。
    pub type_name: String,
    pub files: Vec<String>,
    /// 数える class(空 = :files の module の直下の全部)。
    pub classes: Vec<String>,
    /// その型の欄を持ってよい class の一覧(空 = どの class も持たない)。
    pub holders: Vec<String>,
    /// なぜこの顔ぶれか(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置(一覧の class の欠けの当たりの位置)。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 決めた数(閉じた 2 つ — `:count N` はちょうど N・`:at-least N` は N 以上)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum WantedCount {
    Exactly(usize),
    AtLeast(usize),
}

impl WantedCount {
    /// 数えた数 found がこの決まりに合うか。
    pub fn accepts(self, found: usize) -> bool {
        match self {
            WantedCount::Exactly(n) => found == n,
            WantedCount::AtLeast(n) => found >= n,
        }
    }

    /// 知らせの文の綴り(「ちょうど 3」「1 以上」)。
    pub fn spelling(self) -> String {
        match self {
            WantedCount::Exactly(n) => format!("ちょうど {}", n),
            WantedCount::AtLeast(n) => format!("{} 以上", n),
        }
    }
}

/// 数を決めた綴り 1 つ(`:counted-spellings` の
/// `(counted-spelling "名" :pattern r"…" :files [..] :within [..] :count N :why "…")` — DOEFF161・agora-redesign #1373・#1437)。
/// :files に当たる file の本文(Hy と Python は註を落とす・文字列は数える)で :pattern の当たりを数える。:within があれば名指した定義ごとに数える。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CountedSpelling {
    /// 宣言の名(登録簿の鍵の細目)。
    pub name: String,
    pub pattern: String,
    pub files: Vec<String>,
    /// 数える定義の名(空 = :files の全体で 1 つの数)。
    pub within: Vec<String>,
    pub wanted: WantedCount,
    /// なぜこの数か(知らせの文に入れる)。
    pub why: String,
    /// architecture.hy の中の位置(数える所が無い時の当たりの位置)。
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// 偽の handler の決まり(`:business-fakes {…}` — DOEFF143)。file の綴りの型は repo の根からの glob。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct BusinessFakes {
    /// 模擬の環境の file(定義は模擬の根・本番の code ではない)。
    pub simulation: Vec<String>,
    /// 組み立ての層の file(定義は模擬の根で、本番の code でもある)。
    pub assembly: Vec<String>,
    /// 検の file(模擬の根にも本番の code にも数えない)。
    pub tests: Vec<String>,
    /// 読まない file。
    pub skip: Vec<String>,
    /// 本番の入口を探す file(本番の code と宣言の道具 — 書かなければ検・模擬・読まない file の外の全部)。
    pub production: Vec<String>,
    /// 組の file(名が :simulation-prefix で始まる定義は模擬の根・:production-prefix で始まる定義は本番の入口)。
    pub sets: Vec<String>,
    pub simulation_prefix: Option<String>,
    pub production_prefix: Option<String>,
    /// 本番の入口を `"<module>:<名>"` の文字列で指す時の module の頭(本番の code と :entry-string-files の本文から探す)。
    pub entry_string_modules: Vec<String>,
    /// 入口の文字列を探す宣言の file(配備の宣言・package の宣言)。
    pub entry_string_files: Vec<String>,
    /// 業務の効果を定義する module の綴り(`a.b` は a.b とその下・末尾 `*` は前方一致)。
    pub business_modules: Vec<String>,
    /// 外の世界の効果の表の dir(1 鍵 1 file・1 行目が効果の完全名・2 行目から理由)。
    pub external_effects: Option<String>,
    /// わざと壊した反例の handler の表の dir(鍵 = `<path>::<handler>::<効果>`)。
    pub counterexamples: Option<String>,
    /// 本番の答え手がまだ無い外の世界の効果の表の dir(鍵 = 効果の完全名)。
    pub unserved: Option<String>,
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
    /// 置き場の決まった module にだけ依存してよい層(`:placed-dependencies [core intent protocol]` — 空 = 宣言していない)。
    /// 書けば DOEFF140 が、service と shared のこの層の module が root の下の層の置き場の外の module を import するのを出す
    /// (agora-redesign #1188 — 移す前の置き場への依存を移す変更で消し、新しく足さない)。
    pub placed_dependencies: Vec<String>,
    /// 決めた材料だけで判じる定義(`:blind-definitions [(blind "module:名" :forbid-words [..] :no-imports True …) …]` — 空 = 宣言していない)。
    /// 書けば DOEFF141 が、その定義から呼び出しと名指しで届く定義の本体の使わない語と、定義の module の import を出す(agora-redesign #1368)。
    pub blind_definitions: Vec<BlindDefinition>,
    /// 呼んでよい頭を決めた定義(`:allowed-heads [(allowed-heads "module:名" :heads [..] :why "…") …]` — 空 = 宣言していない)。
    /// 書けば DOEFF147 が、その定義の中の一覧の外の頭を出す(agora-redesign #1413)。
    pub allowed_heads: Vec<AllowedHeads>,
    /// 頭を呼んでよい場所と回数(`:call-sites [(call-site "頭" :files [..] :sites [(site "module:名" :count 1) …] :why "…") …]` — 空 = 宣言
    /// していない)。書けば DOEFF159 が、場所の外の呼び・回数の食い違い・外の form と分岐の食い違いを出す(agora-redesign #1414)。
    pub call_sites: Vec<CallSite>,
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
    /// 許可名簿の規則(DOEFF106・131)を層の置き場の外の file にも当てる dir(repo の根からの綴りの列・`:raw-io-roots ["controllers" "services"]`)。
    /// 書かなければ `:root` だけ(agora-redesign #1147 — :root の外の services/ も名簿で縛る)。
    pub raw_io_roots: Option<Vec<String>>,
    /// 「縁」のテスト(外の世界に触れる handler に届くテスト)が持つ pytest の印の名(`:edge-mark "real_world"`)。
    /// 書けば DOEFF133 が、テストの届く先から導いた種類と印の有無の食い違いを出す(agora-redesign #1106 の R3)。
    pub edge_mark: Option<String>,
    /// 渡された値を実行せずに読むだけの定義(`:static-readers ["module:名" …]` — 例: 土台の閉じを解析器で読む ClosureCase・open-foundations)。
    /// この呼び出しの引数の中の参照は、DOEFF133・136 の「届く」の辺にしない(値として読むだけで、実行しない — agora-redesign #1279)。
    pub static_readers: Vec<DefinitionRef>,
    /// 系の値と系を回す入口(`:systems {:carriers [..] :runners [..]}` — agora-redesign #1390)。系の値(defsystem の定義と :carriers の
    /// 呼び出しの引数)の中から外の世界に届く検は、:runners のどれかにも届く時だけ縁(系の値を読むだけの検は手元)。
    pub systems: Option<Systems>,
    /// 縁と数える触れる先(`:edge-touches [http db …]` — 書かなければ全部)。agora は file・env を入れない
    /// (一時 dir の file は手元 — agora-redesign #1142 の決定 B)。仕組みは linter・値は repo の宣言。
    pub edge_touches: Option<Vec<WorldTouch>>,
    /// テストの形の決まり(`:test-forms {:tests [..] :check-scripts [..] :runners [..]}` — どれも file の綴りの型の列)。
    /// 書けば DOEFF135 が deftest 以外のテストの形を出す(agora-redesign #1106 の R6・#1144)。
    pub test_forms: Option<TestForms>,
    /// 使わないと決めた綴り(`:retired-words [(retired-words …) …]` — 空 = 宣言していない)。書けば DOEFF150 が当たりを出す
    /// (agora-redesign #1193)。語の表は repo の宣言にだけ在り、linter は持たない。
    pub retired_words: Vec<RetiredWords>,
    /// 使わないと決めた呼び(`:retired-calls [(retired-calls …) …]` — 空 = 宣言していない)。書けば DOEFF151 が当たりを出す(#1193)。
    pub retired_calls: Vec<RetiredCalls>,
    /// 判定を 1 か所に閉じ込めた語彙(`:single-point-vocabulary [(vocabulary-scope …) …]` — 空 = 宣言していない)。書けば
    /// DOEFF146 が :except の外でこの語彙を読む file を出す(agora-redesign #1192・#1371)。
    pub single_point_vocabulary: Vec<VocabularyScope>,
    /// 書いてよい file を決めた綴り(`:confined-spellings [(confined-spelling …) …]` — 空 = 宣言していない)。書けば DOEFF148 が
    /// :except の外でこの綴りを書く file を出す(agora-redesign #1373・#1436)。
    pub confined_spellings: Vec<ConfinedSpelling>,
    /// 数を決めた綴り(`:counted-spellings [(counted-spelling …) …]` — 空 = 宣言していない)。書けば DOEFF161 が、:files(か :within の
    /// 定義)の中の当たりの数が決めた数でない所を出す(agora-redesign #1373・#1437)。
    pub counted_spellings: Vec<CountedSpelling>,
    /// effect の宣言の全体(`:effect-census [(effect-census …) …]` — 空 = 宣言していない)。書けば DOEFF162 が、一覧に無い effect の
    /// 宣言・2 度の宣言・宣言の無い一覧の effect を出す(agora-redesign #1373・#1438)。
    pub effect_census: Vec<EffectCensus>,
    /// 型の欄を持つ class の顔ぶれ(`:field-holders [(field-holders …) …]` — 空 = 宣言していない)。書けば DOEFF149 が、一覧に無い
    /// 持ち手と、欄を持たない一覧の class を出す(agora-redesign #1374)。
    pub field_holders: Vec<FieldHolders>,
    /// handler の引数の決まり(書けば DOEFF142 が defhandler の引数の client・可変の店を出す — agora-redesign #1189 / #1366)。
    pub handler_arguments: Option<HandlerArguments>,
    /// 公開面の型の注記を読む file(`:typed-values {:files [..] :except [..]}`)。書けば DOEFF144 が、欄・戻り値・:post の型の素の写像・
    /// 素の組と、組の literal の答えを出す(agora-redesign #1191)。
    pub typed_values: Option<FileSelection>,
    /// 型の宣言(.pyi)を読む file(`:record-stubs {:files [..] :except [..]}`)。書けば DOEFF145 が、同じ dir の同じ名の .hy の kw-only の
    /// record を kw_only=True 無しの @dataclass で宣言する .pyi を出す(#1191)。
    pub record_stubs: Option<FileSelection>,
    /// 偽の handler の決まり(書けば DOEFF143 が業務の効果に答える偽の handler を出す — agora-redesign #1375)。
    pub business_fakes: Option<BusinessFakes>,
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
            placed_dependencies: Vec::new(),
            blind_definitions: Vec::new(),
            allowed_heads: Vec::new(),
            call_sites: Vec::new(),
            services: Vec::new(),
            plain_callable_reasons: Vec::new(),
            rejected_plain_callable_reasons: Vec::new(),
            wire_modules: Vec::new(),
            world_handlers: Vec::new(),
            raw_io_roots: None,
            edge_mark: None,
            static_readers: Vec::new(),
            systems: None,
            edge_touches: None,
            test_forms: None,
            retired_words: Vec::new(),
            retired_calls: Vec::new(),
            single_point_vocabulary: Vec::new(),
            confined_spellings: Vec::new(),
            counted_spellings: Vec::new(),
            effect_census: Vec::new(),
            field_holders: Vec::new(),
            handler_arguments: None,
            typed_values: None,
            record_stubs: None,
            business_fakes: None,
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
                ":placed-dependencies" => arch.placed_dependencies = self.names(value, ":placed-dependencies"),
                ":blind-definitions" => arch.blind_definitions = self.blind_definitions(value),
                ":allowed-heads" => arch.allowed_heads = self.allowed_heads(value),
                ":call-sites" => arch.call_sites = self.call_sites(value),
                ":exclude" => arch.exclude = self.names(value, ":exclude"),
                ":extensions" => arch.extensions = Some(self.names(value, ":extensions")),
                ":plain-callable-reasons" => arch.plain_callable_reasons = self.reasons(value, ":plain-callable-reasons"),
                ":rejected-plain-callable-reasons" => {
                    arch.rejected_plain_callable_reasons = self.reasons(value, ":rejected-plain-callable-reasons")
                }
                ":wire-modules" => arch.wire_modules = self.module_patterns(value, ":wire-modules"),
                ":world-handlers" => arch.world_handlers = self.world_handlers(value),
                ":raw-io-roots" => {
                    let roots = self.names(value, ":raw-io-roots");
                    for root in &roots {
                        if root.is_empty() || root.starts_with('/') || root.split('/').any(|p| p == "..") {
                            self.problem(value, &format!(":raw-io-roots の {} は repo の根からの dir の綴り(/ で始めない・.. を含まない)", root));
                        }
                    }
                    arch.raw_io_roots = Some(roots);
                }
                ":test-forms" => arch.test_forms = self.test_forms(value),
                ":retired-words" => arch.retired_words = self.retired_words(value),
                ":retired-calls" => arch.retired_calls = self.retired_calls(value),
                ":single-point-vocabulary" => arch.single_point_vocabulary = self.single_point_vocabulary(value),
                ":confined-spellings" => arch.confined_spellings = self.confined_spellings(value),
                ":counted-spellings" => arch.counted_spellings = self.counted_spellings(value),
                ":effect-census" => arch.effect_census = self.effect_census(value),
                ":field-holders" => arch.field_holders = self.field_holders(value),
                ":handler-arguments" => arch.handler_arguments = self.handler_arguments(value),
                ":typed-values" => arch.typed_values = self.file_selection(value, ":typed-values"),
                ":record-stubs" => arch.record_stubs = self.file_selection(value, ":record-stubs"),
                ":business-fakes" => arch.business_fakes = self.business_fakes(value),
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
                ":static-readers" => arch.static_readers = self.definition_refs(value, ":static-readers"),
                ":systems" => match self.brace(value) {
                    Some(entries) => {
                        let mut systems = Systems::default();
                        for (key, list) in self.pairs(&entries) {
                            match self.text(key) {
                                ":carriers" => systems.carriers = self.definition_refs(list, ":systems の :carriers"),
                                ":runners" => systems.runners = self.definition_refs(list, ":systems の :runners"),
                                other => self.problem(key, &format!(":systems の知らない鍵 {}(:carriers と :runners だけ)", other)),
                            }
                        }
                        if systems.runners.is_empty() {
                            self.problem(value, ":systems に :runners が無い(系を回す入口が無いと、系の中から届く検がどれも手元になる)");
                        }
                        arch.systems = Some(systems);
                    }
                    None => self.problem(value, ":systems は {:carriers [..] :runners [..]} の辞書"),
                },
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

    /// `["module:名" …]` の列を読む(2 度書いた名・読めない綴りは理由を積む)。
    fn definition_refs(&mut self, value: &Form, what: &str) -> Vec<DefinitionRef> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!("{} は [\"module:名\" …] の列", what));
            return Vec::new();
        };
        let mut out: Vec<DefinitionRef> = Vec::new();
        for item in items {
            if let Some(found) = self.definition_ref(item, what) {
                if out.contains(&found) {
                    self.problem(item, &format!("{} の {} が 2 度書かれている", what, found.spelling()));
                } else {
                    out.push(found);
                }
            }
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

    /// `{:tests [..] :check-scripts [..] :runners [..]}` を読む(:tests は要る)。
    fn test_forms(&mut self, value: &Form) -> Option<TestForms> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":test-forms は {:tests [..] :check-scripts [..] :runners [..]} の辞書");
            return None;
        };
        let mut forms = TestForms::default();
        let mut tests_given = false;
        for (key, list) in self.pairs(&entries) {
            match self.text(key) {
                ":tests" => {
                    tests_given = true;
                    forms.tests = self.names(list, ":test-forms :tests");
                }
                ":check-scripts" => forms.check_scripts = self.names(list, ":test-forms :check-scripts"),
                ":runners" => forms.runners = self.names(list, ":test-forms :runners"),
                _ => self.unknown_key(key, ":test-forms"),
            }
        }
        if !tests_given || forms.tests.is_empty() {
            self.problem(value, ":test-forms に :tests(テストの file の綴りの型)が無い");
        }
        Some(forms)
    }

    /// 正規表現の文字列(`r"…"` か bracket 文字列 — 普通の文字列は `\` の書き方が Hy と正規表現で二重になるので、`\` を含むなら理由を積む)。
    fn pattern(&mut self, form: &Form, what: &str) -> Option<String> {
        match &form.node {
            Node::Str { kind: StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end).map(str::to_string),
            Node::Str { kind: StrKind::Plain, body } => {
                let text = self.src.get(body.start..body.end).unwrap_or("");
                if text.contains('\\') {
                    self.problem(form, &format!("{} の正規表現は r\"…\" の文字列で書く(普通の文字列の \\ は Hy と正規表現で二重に読まれる)", what));
                    return None;
                }
                Some(text.to_string())
            }
            _ => {
                self.problem(form, &format!("{} の要素は正規表現の文字列(r\"…\")", what));
                None
            }
        }
    }

    /// repo の根からの path の glob の列を読む(`/` で始まる・`..` を含む綴りは理由を積む)。
    fn path_globs(&mut self, value: &Form, what: &str) -> Vec<String> {
        let globs = self.names(value, what);
        for glob in &globs {
            if glob.is_empty() || glob.starts_with('/') || glob.split('/').any(|p| p == "..") {
                self.problem(value, &format!("{} の {} は repo の根からの path の glob(/ で始めない・.. を含まない)", what, glob));
            }
        }
        globs
    }

    /// `{:files [..] :except [..]}` を読む(:files は要る — 空なら理由を積んで宣言しなかったことにする)。
    fn file_selection(&mut self, value: &Form, what: &str) -> Option<FileSelection> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, &format!("{} は {{:files [..] :except [..]}} の辞書", what));
            return None;
        };
        let mut selection = FileSelection::default();
        for (key, list) in self.pairs(&entries) {
            match self.text(key) {
                ":files" => selection.files = self.path_globs(list, &format!("{} :files", what)),
                ":except" => selection.except = self.path_globs(list, &format!("{} :except", what)),
                _ => self.unknown_key(key, what),
            }
        }
        if selection.files.is_empty() {
            self.problem(value, &format!("{} に :files(読む file の glob)が無い", what));
            return None;
        }
        Some(selection)
    }

    /// `[(retired-words "名" :words [..] :patterns [r"…"] :files [..] :except [..] :rule-lines [..] :in lines|names|paths :instead "…") …]` を読む
    /// (:files と :instead と、:words か :patterns のどちらかは要る)。
    fn retired_words(&mut self, value: &Form) -> Vec<RetiredWords> {
        let shape = "(retired-words \"名\" :words [..] :patterns [r\"…\"] :files [..] :except [..]? :rule-lines [..]? :in lines|names|paths? :instead \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":retired-words は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<RetiredWords> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("retired-words"));
            let Some(name) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!(":retired-words の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let mut group = RetiredWords {
                name,
                words: Vec::new(),
                patterns: Vec::new(),
                files: Vec::new(),
                except: Vec::new(),
                rule_lines: Vec::new(),
                place: WordPlace::Lines,
                instead: String::new(),
            };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":words" => group.words = self.names(field, ":words"),
                    ":patterns" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                let Some(pattern) = self.pattern(item, ":patterns") else { continue };
                                match regex::Regex::new(&pattern) {
                                    Ok(_) => group.patterns.push(pattern),
                                    Err(error) => self.problem(item, &format!("retired-words {} の正規表現 {} を読めない: {}", group.name, pattern, error)),
                                }
                            }
                        }
                        None => self.problem(field, ":patterns は [r\"…\" …] の列"),
                    },
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":rule-lines" => group.rule_lines = self.names(field, ":rule-lines"),
                    ":in" => match self.name(field).as_deref() {
                        Some("lines") => group.place = WordPlace::Lines,
                        Some("names") => group.place = WordPlace::Names,
                        Some("paths") => group.place = WordPlace::Paths,
                        _ => self.problem(field, ":in は lines か names か paths"),
                    },
                    ":instead" => group.instead = self.required_string(field, ":instead").unwrap_or_default(),
                    _ => self.unknown_key(key, "retired-words"),
                }
            }
            if group.words.is_empty() && group.patterns.is_empty() {
                self.problem(entry, &format!("retired-words {} に :words も :patterns も無い", group.name));
            }
            if group.words.iter().any(|w| w.trim().is_empty()) {
                self.problem(entry, &format!("retired-words {} の :words に空の語がある", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("retired-words {} に :files が無い(探す file の無い群は置かない)", group.name));
            }
            if group.instead.trim().is_empty() {
                self.problem(entry, &format!("retired-words {} に :instead(代わりに使う語・直し方)が無い", group.name));
            }
            if group.place != WordPlace::Lines && !group.rule_lines.is_empty() {
                self.problem(entry, &format!("retired-words {} の :rule-lines は :in lines の時だけ効く", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("retired-words {} が 2 度宣言されている", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// `[(confined-spelling "名" :patterns [r"…"] :files [..] :except [..] :why "…") …]` を読む(:patterns・:files・:why は要る —
    /// :except は書かなくてよい = :files のどこにも書かない綴り)。
    fn confined_spellings(&mut self, value: &Form) -> Vec<ConfinedSpelling> {
        let shape = "(confined-spelling \"名\" :patterns [r\"…\"] :files [..] :except [..] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":confined-spellings は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<ConfinedSpelling> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("confined-spelling"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!(":confined-spellings の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut group = ConfinedSpelling { name, patterns: Vec::new(), files: Vec::new(), except: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":patterns" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                let Some(pattern) = self.pattern(item, ":patterns") else { continue };
                                match regex::Regex::new(&pattern) {
                                    Ok(_) => group.patterns.push(pattern),
                                    Err(error) => self.problem(item, &format!("confined-spelling {} の正規表現 {} を読めない: {}", group.name, pattern, error)),
                                }
                            }
                        }
                        None => self.problem(field, ":patterns は [r\"…\" …] の列"),
                    },
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":why" => group.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "confined-spelling"),
                }
            }
            if group.patterns.is_empty() {
                self.problem(entry, &format!("confined-spelling {} に :patterns が無い", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("confined-spelling {} に :files が無い(探す file の無い群は置かない)", group.name));
            }
            if group.why.trim().is_empty() {
                self.problem(entry, &format!("confined-spelling {} に :why(なぜこの file だけか)が無い", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("confined-spelling {} が 2 度宣言されている", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// `[(effect-census "名" :files [..] :effects [..] :base "EffectBase" :why "…") …]` を読む(:files・:effects・:why は要る —
    /// :base は書かなければ EffectBase)。
    fn effect_census(&mut self, value: &Form) -> Vec<EffectCensus> {
        let shape = "(effect-census \"名\" :files [..] :effects [..] :base \"EffectBase\" :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":effect-census は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<EffectCensus> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("effect-census"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!(":effect-census の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut census =
                EffectCensus { name, files: Vec::new(), effects: Vec::new(), base: "EffectBase".to_string(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":files" => census.files = self.path_globs(field, ":files"),
                    ":effects" => census.effects = self.names(field, ":effects"),
                    ":base" => census.base = self.required_string(field, ":base").unwrap_or_default(),
                    ":why" => census.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "effect-census"),
                }
            }
            if census.files.is_empty() {
                self.problem(entry, &format!("effect-census {} に :files が無い(effect を宣言する file を挙げる)", census.name));
            }
            if census.effects.is_empty() {
                self.problem(entry, &format!("effect-census {} に :effects が無い(effect の一覧を挙げる)", census.name));
            }
            if census.base.trim().is_empty() {
                self.problem(entry, &format!("effect-census {} の :base が空", census.name));
            }
            if census.why.trim().is_empty() {
                self.problem(entry, &format!("effect-census {} に :why(なぜ一覧で閉じるか)が無い", census.name));
            }
            if out.iter().any(|c| c.name == census.name) {
                self.problem(entry, &format!("effect-census {} が 2 度宣言されている", census.name));
                continue;
            }
            out.push(census);
        }
        out
    }

    /// `[(field-holders "名" :type "T" :files [..] :classes [..] :holders [..] :why "…") …]` を読む(:type・:files・:holders・:why は要る —
    /// :holders は空の列でよい = どの class も持たない。:classes は書かなくてよい = module の直下の全部。:classes を書けば :holders は
    /// その中の名)。
    fn field_holders(&mut self, value: &Form) -> Vec<FieldHolders> {
        let shape = "(field-holders \"名\" :type \"T\" :files [..] :classes [..] :holders [..] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":field-holders は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<FieldHolders> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("field-holders"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!(":field-holders の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut type_name = String::new();
            let mut files: Vec<String> = Vec::new();
            let mut classes: Vec<String> = Vec::new();
            let mut holders: Option<Vec<String>> = None;
            let mut why = String::new();
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":type" => type_name = self.required_string(field, ":type").unwrap_or_default(),
                    ":files" => files = self.path_globs(field, ":files"),
                    ":classes" => classes = self.names(field, ":classes"),
                    ":holders" => holders = Some(self.names(field, ":holders")),
                    ":why" => why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "field-holders"),
                }
            }
            let mut complete = true;
            if type_name.trim().is_empty() {
                self.problem(entry, &format!("field-holders {} に :type(数える型の綴り)が無い", name));
                complete = false;
            }
            if files.is_empty() {
                self.problem(entry, &format!("field-holders {} に :files が無い(数える file の無い宣言は置かない)", name));
                complete = false;
            }
            if holders.is_none() {
                self.problem(entry, &format!("field-holders {} に :holders が無い(持ってよい class が無いなら [] と書く)", name));
                complete = false;
            }
            if why.trim().is_empty() {
                self.problem(entry, &format!("field-holders {} に :why(なぜこの顔ぶれか)が無い", name));
            }
            let holders = holders.unwrap_or_default();
            if !classes.is_empty() {
                for holder in holders.iter().filter(|h| !classes.contains(*h)) {
                    self.problem(entry, &format!("field-holders {} の :holders の {} が :classes に無い", name, holder));
                    complete = false;
                }
            }
            if out.iter().any(|d| d.name == name) {
                self.problem(entry, &format!("field-holders {} が 2 度宣言されている", name));
                continue;
            }
            if complete {
                out.push(FieldHolders { name, type_name, files, classes, holders, why, range });
            }
        }
        out
    }

    /// `[(counted-spelling "名" :pattern r"…" :files [..] :within [..] :count N :why "…") …]` を読む(:pattern・:files・:why と、
    /// :count か :at-least のどちらか 1 つは要る — :within は書かなくてよい = :files の全体で数える)。
    fn counted_spellings(&mut self, value: &Form) -> Vec<CountedSpelling> {
        let shape = "(counted-spelling \"名\" :pattern r\"…\" :files [..] :within [..] :count N | :at-least N :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":counted-spellings は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<CountedSpelling> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("counted-spelling"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!(":counted-spellings の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut pattern: Option<String> = None;
            let mut files: Vec<String> = Vec::new();
            let mut within: Vec<String> = Vec::new();
            let mut wanted: Vec<WantedCount> = Vec::new();
            let mut why = String::new();
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":pattern" => {
                        if let Some(text) = self.pattern(field, ":pattern") {
                            match regex::Regex::new(&text) {
                                Ok(_) => pattern = Some(text),
                                Err(error) => self.problem(field, &format!("counted-spelling {} の正規表現 {} を読めない: {}", name, text, error)),
                            }
                        }
                    }
                    ":files" => files = self.path_globs(field, ":files"),
                    ":within" => within = self.names(field, ":within"),
                    key @ (":count" | ":at-least") => match self.text(field).parse::<usize>() {
                        Ok(n) => wanted.push(if key == ":count" { WantedCount::Exactly(n) } else { WantedCount::AtLeast(n) }),
                        Err(_) => self.problem(field, &format!("counted-spelling {} の {} は 0 以上の整数", name, key)),
                    },
                    ":why" => why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "counted-spelling"),
                }
            }
            let mut complete = true;
            if pattern.is_none() {
                self.problem(entry, &format!("counted-spelling {} に :pattern が無い", name));
                complete = false;
            }
            if files.is_empty() {
                self.problem(entry, &format!("counted-spelling {} に :files が無い(数える file の無い宣言は置かない)", name));
                complete = false;
            }
            if wanted.len() != 1 {
                self.problem(entry, &format!("counted-spelling {} には :count か :at-least のどちらか 1 つを書く", name));
                complete = false;
            }
            if why.trim().is_empty() {
                self.problem(entry, &format!("counted-spelling {} に :why(なぜこの数か)が無い", name));
            }
            if out.iter().any(|d| d.name == name) {
                self.problem(entry, &format!("counted-spelling {} が 2 度宣言されている", name));
                continue;
            }
            if let (true, Some(pattern), Some(&wanted)) = (complete, pattern, wanted.first()) {
                out.push(CountedSpelling { name, pattern, files, within, wanted, why, range });
            }
        }
        out
    }

    /// `[(vocabulary-scope "名" :patterns [r"…"] :files [..] :except [..] :instead "…") …]` を読む
    /// (:patterns・:files・:except・:instead は要る — :except が無ければ「判定の 1 点」が無い宣言になる)。
    fn single_point_vocabulary(&mut self, value: &Form) -> Vec<VocabularyScope> {
        let shape = "(vocabulary-scope \"名\" :patterns [r\"…\"] :files [..] :except [..] :instead \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":single-point-vocabulary は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<VocabularyScope> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("vocabulary-scope"));
            let Some(name) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!(":single-point-vocabulary の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let mut group = VocabularyScope { name, patterns: Vec::new(), files: Vec::new(), except: Vec::new(), instead: String::new() };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":patterns" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                let Some(pattern) = self.pattern(item, ":patterns") else { continue };
                                match regex::Regex::new(&pattern) {
                                    Ok(_) => group.patterns.push(pattern),
                                    Err(error) => self.problem(item, &format!("vocabulary-scope {} の正規表現 {} を読めない: {}", group.name, pattern, error)),
                                }
                            }
                        }
                        None => self.problem(field, ":patterns は [r\"…\" …] の列"),
                    },
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":instead" => group.instead = self.required_string(field, ":instead").unwrap_or_default(),
                    _ => self.unknown_key(key, "vocabulary-scope"),
                }
            }
            if group.patterns.is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} に :patterns が無い", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} に :files が無い(探す file の無い群は置かない)", group.name));
            }
            if group.except.is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} に :except(判定の 1 点)が無い", group.name));
            }
            if group.instead.trim().is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} に :instead(直し方)が無い", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("vocabulary-scope {} が 2 度宣言されている", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// `[(retired-calls "名" :calls [..] :files [..] :except [..] :instead "…") …]` を読む(:calls・:files・:instead は要る)。
    fn retired_calls(&mut self, value: &Form) -> Vec<RetiredCalls> {
        let shape = "(retired-calls \"名\" :calls [..] :files [..] :except [..]? :instead \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":retired-calls は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<RetiredCalls> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("retired-calls"));
            let Some(name) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!(":retired-calls の要素は {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let mut group = RetiredCalls { name, calls: Vec::new(), files: Vec::new(), except: Vec::new(), instead: String::new() };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":calls" => group.calls = self.names(field, ":calls"),
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":instead" => group.instead = self.required_string(field, ":instead").unwrap_or_default(),
                    _ => self.unknown_key(key, "retired-calls"),
                }
            }
            if group.calls.is_empty() || group.calls.iter().any(|c| c.is_empty() || c.contains(char::is_whitespace)) {
                self.problem(entry, &format!("retired-calls {} の :calls は呼びの頭の綴り(空白を含まない)の列で、空にしない", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("retired-calls {} に :files が無い", group.name));
            }
            if group.instead.trim().is_empty() {
                self.problem(entry, &format!("retired-calls {} に :instead(代わりに使う物)が無い", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("retired-calls {} が 2 度宣言されている", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// `:business-fakes {…}` を読む(:simulation と :business-modules は要る)。
    fn business_fakes(&mut self, value: &Form) -> Option<BusinessFakes> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":business-fakes は {:simulation [..] :assembly [..] :tests [..] :skip [..] :sets [..] … :business-modules [..]} の辞書");
            return None;
        };
        let mut decl = BusinessFakes::default();
        for (key, field) in self.pairs(&entries) {
            match self.text(key) {
                ":simulation" => decl.simulation = self.path_globs(field, ":business-fakes :simulation"),
                ":assembly" => decl.assembly = self.path_globs(field, ":business-fakes :assembly"),
                ":tests" => decl.tests = self.path_globs(field, ":business-fakes :tests"),
                ":skip" => decl.skip = self.path_globs(field, ":business-fakes :skip"),
                ":production" => decl.production = self.path_globs(field, ":business-fakes :production"),
                ":sets" => decl.sets = self.path_globs(field, ":business-fakes :sets"),
                ":simulation-prefix" => decl.simulation_prefix = self.required_string(field, ":business-fakes :simulation-prefix"),
                ":production-prefix" => decl.production_prefix = self.required_string(field, ":business-fakes :production-prefix"),
                ":entry-string-modules" => decl.entry_string_modules = self.names(field, ":business-fakes :entry-string-modules"),
                ":entry-string-files" => decl.entry_string_files = self.path_globs(field, ":business-fakes :entry-string-files"),
                ":business-modules" => decl.business_modules = self.names(field, ":business-fakes :business-modules"),
                ":external-effects" => decl.external_effects = self.required_string(field, ":business-fakes :external-effects"),
                ":counterexamples" => decl.counterexamples = self.required_string(field, ":business-fakes :counterexamples"),
                ":unserved" => decl.unserved = self.required_string(field, ":business-fakes :unserved"),
                _ => self.unknown_key(key, ":business-fakes"),
            }
        }
        if decl.simulation.is_empty() {
            self.problem(value, ":business-fakes に :simulation(模擬の環境の file の綴りの型)が無い");
        }
        if decl.business_modules.is_empty() {
            self.problem(value, ":business-fakes に :business-modules(業務の効果を定義する module)が無い");
        }
        Some(decl)
    }

    /// `{:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark "…" :value-types [..]}` を読む(:files は要る)。
    fn handler_arguments(&mut self, value: &Form) -> Option<HandlerArguments> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":handler-arguments は {:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark \"…\" :value-types [..]} の辞書");
            return None;
        };
        let mut decl = HandlerArguments::default();
        for (key, field) in self.pairs(&entries) {
            match self.text(key) {
                ":files" => decl.files = self.names(field, ":handler-arguments :files"),
                ":exclude" => decl.exclude = self.names(field, ":handler-arguments :exclude"),
                ":store-names" => decl.store_names = self.names(field, ":handler-arguments :store-names"),
                ":store-suffixes" => decl.store_suffixes = self.names(field, ":handler-arguments :store-suffixes"),
                ":keep-mark" => decl.keep_mark = self.required_string(field, ":handler-arguments :keep-mark").filter(|m| !m.is_empty()),
                ":value-types" => decl.value_types = self.names(field, ":handler-arguments :value-types"),
                _ => self.unknown_key(key, ":handler-arguments"),
            }
        }
        if decl.files.is_empty() {
            self.problem(value, ":handler-arguments に :files(判じる file の綴りの型)が無い");
        }
        Some(decl)
    }

    /// `[(blind "module:名" :forbid-words [..] :no-imports True :allow-requires [..] :why "…") …]` を読む
    /// (:why と、:forbid-words か :no-imports True のどちらかは要る)。
    fn blind_definitions(&mut self, value: &Form) -> Vec<BlindDefinition> {
        let shape = "(blind \"module:名\" :forbid-words [..]? :no-imports True? :allow-requires [..]? :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":blind-definitions は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<BlindDefinition> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("blind"));
            let Some(parts) = parts else {
                self.problem(entry, &format!(":blind-definitions の要素は {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "blind に \"module:名\" が無い");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "blind") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut blind = BlindDefinition { definition, forbid_words: Vec::new(), no_imports: false, allow_requires: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":forbid-words" => blind.forbid_words = self.names(field, ":forbid-words"),
                    ":no-imports" => match self.symbol(field) {
                        Some("True") => blind.no_imports = true,
                        Some("False") => blind.no_imports = false,
                        _ => self.problem(field, ":no-imports は True か False"),
                    },
                    ":allow-requires" => blind.allow_requires = self.names(field, ":allow-requires"),
                    ":why" => blind.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "blind"),
                }
            }
            let spelling = blind.definition.spelling();
            if blind.forbid_words.iter().any(|w| w.is_empty()) {
                self.problem(entry, &format!("blind {} の :forbid-words に空の綴りがある", spelling));
            }
            if blind.forbid_words.is_empty() && !blind.no_imports {
                self.problem(entry, &format!("blind {} に :forbid-words も :no-imports True も無い(何も求めない宣言は置かない)", spelling));
            }
            if !blind.allow_requires.is_empty() && !blind.no_imports {
                self.problem(entry, &format!("blind {} の :allow-requires は :no-imports True の時だけ効く", spelling));
            }
            if blind.why.trim().is_empty() {
                self.problem(entry, &format!("blind {} に :why(なぜその材料を読まないか)が無い", spelling));
            }
            if out.iter().any(|b| b.definition == blind.definition) {
                self.problem(entry, &format!("blind {} が 2 度宣言されている", spelling));
                continue;
            }
            out.push(blind);
        }
        out
    }

    /// `[(call-site "頭" :files [..] :except [..]? :sites [(site "module:名" :count N? :parent "頭"? :branch "名"?) …] :why "…") …]` を読む
    /// (:files・:sites・:why は要る)。
    fn call_sites(&mut self, value: &Form) -> Vec<CallSite> {
        let shape = "(call-site \"頭\" :files [..] :except [..]? :sites [(site \"module:名\" :count N? :parent \"頭\"? :branch \"名\"?) …] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":call-sites は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<CallSite> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("call-site"));
            let Some(parts) = parts else {
                self.problem(entry, &format!(":call-sites の要素は {}", shape));
                continue;
            };
            let Some(head) = parts.get(1).and_then(|h| self.name(h)).filter(|h| !h.is_empty()) else {
                self.problem(entry, "call-site に頭の綴りが無い");
                continue;
            };
            let range = self.lines.range(entry.span.start, entry.span.end);
            let mut declared = CallSite { head, files: Vec::new(), except: Vec::new(), sites: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":files" => declared.files = self.names(field, ":files"),
                    ":except" => declared.except = self.names(field, ":except"),
                    ":sites" => declared.sites = self.call_site_sites(field),
                    ":why" => declared.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "call-site"),
                }
            }
            let head = declared.head.clone();
            if declared.files.is_empty() {
                self.problem(entry, &format!("call-site {} に :files が無い(探す file の無い宣言は置かない)", head));
            }
            if declared.sites.is_empty() {
                self.problem(entry, &format!("call-site {} に :sites が無い(呼んでよい場所の無い宣言は置かない)", head));
            }
            if declared.why.trim().is_empty() {
                self.problem(entry, &format!("call-site {} に :why(なぜここだけか)が無い", head));
            }
            if out.iter().any(|d| d.head == declared.head) {
                self.problem(entry, &format!("call-site {} が 2 度宣言されている", head));
                continue;
            }
            out.push(declared);
        }
        out
    }

    /// `[(site "module:名" :count N :parent "頭" :branch "名") …]` を読む。
    fn call_site_sites(&mut self, value: &Form) -> Vec<CallSiteSite> {
        let shape = "(site \"module:名\" :count N? :parent \"頭\"? :branch \"名\"?)";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":sites は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<CallSiteSite> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("site"));
            let Some(parts) = parts else {
                self.problem(entry, &format!(":sites の要素は {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "site に \"module:名\" が無い");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "site") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut site = CallSiteSite { definition, count: None, parent: None, branch: None, range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":count" => match self.text(field).parse::<usize>() {
                        Ok(count) => site.count = Some(count),
                        Err(_) => self.problem(field, ":count は 0 以上の整数"),
                    },
                    ":parent" => site.parent = self.required_string(field, ":parent"),
                    ":branch" => site.branch = self.required_string(field, ":branch"),
                    _ => self.unknown_key(key, "site"),
                }
            }
            if out.iter().any(|s| s.definition == site.definition) {
                self.problem(entry, &format!("site {} が 2 度書かれている", site.definition.spelling()));
                continue;
            }
            out.push(site);
        }
        out
    }

    /// `[(allowed-heads "module:名" :heads [..] :why "…") …]` を読む(:heads と :why は要る)。
    fn allowed_heads(&mut self, value: &Form) -> Vec<AllowedHeads> {
        let shape = "(allowed-heads \"module:名\" :heads [..] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":allowed-heads は {} の列", shape));
            return Vec::new();
        };
        let mut out: Vec<AllowedHeads> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("allowed-heads"));
            let Some(parts) = parts else {
                self.problem(entry, &format!(":allowed-heads の要素は {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "allowed-heads に \"module:名\" が無い");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "allowed-heads") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut declared = AllowedHeads { definition, heads: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":heads" => declared.heads = self.names(field, ":heads"),
                    ":why" => declared.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "allowed-heads"),
                }
            }
            let spelling = declared.definition.spelling();
            if declared.heads.is_empty() {
                self.problem(entry, &format!("allowed-heads {} に :heads が無い(何も呼ばない定義は無い — 定義の頭 defk なども挙げる)", spelling));
            }
            if declared.heads.iter().any(|h| h.is_empty()) {
                self.problem(entry, &format!("allowed-heads {} の :heads に空の綴りがある", spelling));
            }
            if declared.why.trim().is_empty() {
                self.problem(entry, &format!("allowed-heads {} に :why(なぜこの頭だけか)が無い", spelling));
            }
            if out.iter().any(|d| d.definition == declared.definition) {
                self.problem(entry, &format!("allowed-heads {} が 2 度宣言されている", spelling));
                continue;
            }
            out.push(declared);
        }
        out
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
        for (i, placed) in arch.placed_dependencies.iter().enumerate() {
            if !layers.contains(placed.as_str()) {
                push(&mut self.problems, format!(":placed-dependencies の {} は :layers に無い", placed));
            } else if arch.foundation.as_deref() == Some(placed.as_str()) {
                push(&mut self.problems, format!(":placed-dependencies の {} は :foundation の層 — service の外の層には当てない", placed));
            }
            if arch.placed_dependencies[..i].contains(placed) {
                push(&mut self.problems, format!(":placed-dependencies の {} が 2 度書かれている", placed));
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
