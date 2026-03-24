//! DoCtrl — the instruction set for the effect handler VM.
//!
//! Every DoCtrl has explicit semantics. No implicit behavior.
//! The VM executes exactly what the AST says.
//!
//! The VM is language-agnostic — no Python types here.

use crate::continuation::Continuation;
use crate::ids::VarId;
use crate::value::Value;

/// The VM instruction set. 16 explicit operations.
#[derive(Debug)]
pub enum DoCtrl {
    // --- Values ---

    /// Return a value. `Pure(v)` → delivers `v`.
    Pure { value: Value },

    // --- Evaluation ---

    /// Evaluate inner DoCtrl, return the resulting Value.
    Eval { expr: Box<DoCtrl> },

    /// Evaluate inner DoCtrl to Value::Stream, push as frame and EXECUTE it.
    /// Explicit — the AST must wrap with Expand if the result is a stream.
    Expand { expr: Box<DoCtrl> },

    // --- Function call ---

    /// Evaluate f and args, call f(args), return the result Value.
    /// Does NOT execute the result even if it's a Stream — use Expand for that.
    Apply { f: Box<DoCtrl>, args: Vec<DoCtrl> },

    // --- OCaml 5 effect handler operations ---

    /// Perform an effect. Walks the handler chain, detaches the fiber chain,
    /// creates a continuation, and calls the handler.
    Perform { effect: Value },

    /// Resume continuation with value. Handler stays alive (non-tail).
    /// OCaml 5: `let result = continue k v in ...`
    Resume { k: Continuation, value: Value },

    /// Resume continuation with value. Handler is done (tail position).
    /// OCaml 5: `continue k v` as last expression.
    Transfer { k: Continuation, value: Value },

    /// Throw exception into continuation. Handler stays alive (non-tail).
    /// OCaml 5: `let result = discontinue k exn in ...`
    ResumeThrow { k: Continuation, exception: Value },

    /// Throw exception into continuation. Handler is done (tail position).
    /// OCaml 5: `discontinue k exn` as last expression.
    TransferThrow { k: Continuation, exception: Value },

    /// Install a handler and execute body.
    /// handler: Value::Callable — called with (effect, k) on perform.
    /// body: Value::Stream or Value::Callable — the computation to run under the handler.
    /// OCaml 5: `match_with body handler`
    WithHandler { handler: Value, body: Value },

    /// Current handler doesn't handle this effect. Re-perform at outer handler.
    /// OCaml 5: reperform (handler returns None for this effect).
    Pass,

    /// Forward effect to outer handler (handler explicitly delegates).
    /// OCaml 5: reperform with handler appended to continuation chain.
    Delegate,

    // --- Heap (OCaml ref cells) ---

    /// Allocate a mutable ref cell with initial value.
    AllocVar { initial: Value },

    /// Read a ref cell.
    ReadVar { var: VarId },

    /// Write to a ref cell.
    WriteVar { var: VarId, value: Value },
}

// DoCtrl is intentionally NOT Clone — Continuation-bearing variants flow by move.
