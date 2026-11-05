# DOEFF002: Names of mutable attributes

## Purpose of this document

After reading this, you can predict which attributes DOEFF002 fires on and which it does not (the boundary between the class's own attributes and attributes inherited from a base), and you can fix a false positive by adding a framework-owned attribute to the table.

## What the rule says

An attribute written via `self.<name>` outside a class's `__init__`, `__post_init__` and `__set_name__` is mutable state, so its name must start with `mut_` (public) or `_mut` (private). The type does not matter (not only list, dict and set, but also numbers, booleans and strings).

Writes to an inherited attribute are outside this rule. A name decided by a framework stops being read by the framework if you change it. For example, Textual's `App.sub_title` is an attribute whose assignment changes the header display; renaming it to `self._mut_sub_title` would stop the display from updating.

## How ownership of an attribute is decided

For each class, the rule sorts attributes using the following terms.

| Term | Meaning |
|---|---|
| Place | Written to `self` by initialization (`__init__`, `__post_init__`, `__set_name__`) and construction (`__new__`). Also written by methods of the same class called from there as `self.<method>()` (including calls further down the chain). Also written in the class body (annotated fields, assignments, methods, nested classes, `__slots__` names) |
| Same-repo base | A class in the same file, or a class in a file inside the repo that an import points to (see the lookup below) |
| Invisible base | A base whose attributes cannot all be read. A class outside the repo (such as `textual.app.App`), an unresolvable import, an unreadable file, or an expression that cannot be pointed to by name, such as `make_base()`. `object`, `abc.ABC`, `typing.Generic` and `typing.Protocol` are known to have no attributes, so they are not counted |

| Situation | Example | Verdict |
|---|---|---|
| Writing, outside init, a name that the class or a same-repo base placed | `self.rows = []` in `__init__`, `self.rows = [1]` in `tick` | Fires (even if a base is a class outside the repo) |
| No invisible base, and writing outside init, for the first time, a name nobody placed | `self.cache = {}` in `tick` of a class with no bases | Fires (the class's own attribute is being created later) |
| An invisible base exists, and writing a name that neither the class nor a same-repo base placed | `class X(App): def tick(self): self.sub_title = "x"` | Does not fire (read as an attribute of the invisible base) |
| Writing a base's name listed in the table | A child of `App` also places `self.sub_title = ...` in `__init__` and rewrites it in `tick` | Does not fire (a name inherited from the base even though it is placed) |

## How a same-repo base is looked up

A base is resolved to a class from the file's imports (`from m import B`, `x.B` for `import m as x`, `a.b.B` for `import a.b`, relative imports, and for subscripted forms such as `Generic[T]` the subscript is removed).

| Where it is looked up | How |
|---|---|
| Same file | The class with the base's name written before the class that names it as a base (if there is none, the single such class, counting those inside functions too) |
| Absolute import | Takes the module path (`a/b.py` or `a/b/__init__.py`) from those directories, among the ones above up to the repo root, that lack `__init__.py`, and from their `src`. A directory with `__init__.py` is not an import root |
| Relative import | Climbs from the directory of the file being read by the number of dots |
| Re-export in `__init__.py` | Follows `from .a import B` up to 8 levels |

The repo root is the root decided by the linter run (`--root`, or the directory of the settings file). Directories above the root are not searched, so a tree without `.git` (the HEAD tree of the commit hook, the daily copy) searches the same files. A base that cannot be found or read is read as an invisible base, so the failure never leans toward firing.

## Adding to the table `data/framework_attributes.json`

Place the attributes known to be owned by a base outside the repo, per base by its fully qualified name. A name in the table does not fire, as an inherited name, whether or not the class places it in init.

| Field | Content |
|---|---|
| `transparent_bases` | Bases known to have no attributes at all (not counted as invisible bases) |
| `framework_attributes` | The base's fully qualified name (`App` in `from textual.app import App` is `textual.app.App`) mapped to the names of attributes that base has. This is not all of the base's attributes (even if listed, the base is read as possibly having other attributes) |

Only add a name when you have confirmed, in the framework's source, that the name is declared on the base class and documented as an entry point for users to write. A child class of a base (such as `textual.widgets.Static`) is an invisible base unless the base's name is listed separately, and the table's names do not apply to it (a name written for the first time outside init does not fire, by the invisible-base rule).

## Directions of failure

| Deviation | What happens |
|---|---|
| A class with an invisible base creates its own attribute for the first time outside init | Does not fire (cannot be told apart from an inherited attribute). If it is placed in a method that init calls, it fires |
| A base in another package's `src` (another package of a monorepo) | Not searched; counted as an invisible base |
| A framework attribute not in the table is also placed in init | Fires (fix it by adding it to the table) |
