//! Step machine — drives the VM one step at a time.
//!
//! Signals:
//!   Eval(DoCtrl)  — evaluate an instruction
//!   Send(Value)   — send value to current stream
//!   Raise(Value)  — signal error to current stream
//!
//! The step machine is a simple loop: take Signal, process it, return next Signal.
//! No implicit behavior. No Python. No trace state.

use crate::continuation::Continuation;
use crate::do_ctrl::DoCtrl;
use crate::driver::{Signal, SignalAction, StepResult};
use crate::error::VMError;
use crate::frame::{EvalReturnContinuation, Frame};
use crate::ids::FiberId;
use crate::ir_stream::StreamStep;
use crate::segment::Fiber;
use crate::value::Value;
use crate::vm::VM;

impl VM {
    /// Execute one step.
    pub fn step(&mut self, signal: Signal) -> StepResult {
        let Signal {
            action,
            error_context,
        } = signal;
        let result = match action {
            SignalAction::Eval(doctrl) => self.step_eval(doctrl, error_context),
            SignalAction::Send(value) => self.step_send(value, error_context),
            SignalAction::Raise(error) => self.step_raise(error, error_context),
        };
        #[cfg(feature = "invariant-checks")]
        self.assert_invariants_after_step();
        result
    }

    // -------------------------------------------------------------------
    // Send — deliver a value to the current stream
    // -------------------------------------------------------------------

    fn step_send(&mut self, value: Value, error_context: Option<Vec<Value>>) -> StepResult {
        let Some(seg_id) = self.current_segment else {
            return StepResult::Done(value);
        };

        // Fast path: fiber completed (no frames) — free it and move to parent.
        // Checked before the mutable borrow so we can call free() without
        // conflicting with the borrow used in the frame-processing match below.
        if self
            .segments
            .get(seg_id)
            .map_or(false, |s| s.frames.is_empty())
        {
            let parent = self.segments.get(seg_id).and_then(|s| s.parent);
            self.segments.free(seg_id);
            self.current_segment = parent;
            if parent.is_some() {
                return continue_send(value, error_context);
            } else {
                return StepResult::Done(value);
            }
        }

        let Some(seg) = self.segments.get_mut(seg_id) else {
            return error_result(VMError::internal("send: segment not found"), error_context);
        };

        match seg.frames.last() {
            None => unreachable!(), // handled by fast path above
            Some(Frame::Program { .. }) => {
                let Frame::Program { stream, .. } = seg.frames.last().unwrap() else {
                    unreachable!()
                };
                match stream.resume(value) {
                    StreamStep::Instruction(doctrl) => continue_eval(doctrl, error_context),
                    StreamStep::Done(value) => {
                        seg.frames.pop();
                        continue_send(value, error_context)
                    }
                    StreamStep::Error(error) => continue_raise(error, error_context),
                    StreamStep::External(call) => external_result(call, error_context),
                }
            }
            Some(Frame::EvalReturn(_)) => {
                let Frame::EvalReturn(eval_return) = seg.frames.pop().unwrap() else {
                    unreachable!()
                };
                self.step_eval_return(*eval_return, value, error_context)
            }
            Some(Frame::LexicalScope { .. }) => {
                // Scope frame — skip, deliver to next frame
                // TODO: may need to pop scope on exit
                continue_send(value, error_context)
            }
            Some(
                Frame::MapReturn { .. }
                | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. },
            ) => {
                // Legacy frames — remove in future cleanup
                seg.frames.pop();
                continue_send(value, error_context)
            }
        }
    }

    // -------------------------------------------------------------------
    // Raise — signal error to the current stream
    // -------------------------------------------------------------------

    fn step_raise(&mut self, error: Value, error_context: Option<Vec<Value>>) -> StepResult {
        // Capture execution context on first error (before unwinding destroys it).
        let error_context = error_context.or_else(|| Some(self.collect_rich_execution_context()));

        let Some(seg_id) = self.current_segment else {
            return error_result(VMError::uncaught_exception(error), error_context);
        };

        // Fast path: fiber completed (no frames) — free it and propagate to parent.
        if self
            .segments
            .get(seg_id)
            .map_or(false, |s| s.frames.is_empty())
        {
            let parent = self.segments.get(seg_id).and_then(|s| s.parent);
            self.segments.free(seg_id);
            self.current_segment = parent;
            if parent.is_some() {
                return continue_raise(error, error_context);
            } else {
                return error_result(VMError::uncaught_exception(error), error_context);
            }
        }

        let Some(seg) = self.segments.get_mut(seg_id) else {
            return error_result(VMError::internal("raise: segment not found"), error_context);
        };

        match seg.frames.last() {
            None => unreachable!(), // handled by fast path above
            Some(Frame::Program { .. }) => {
                let Frame::Program { stream, .. } = seg.frames.last().unwrap() else {
                    unreachable!()
                };
                match stream.throw(error) {
                    StreamStep::Instruction(doctrl) => continue_eval(doctrl, error_context),
                    StreamStep::Done(value) => {
                        seg.frames.pop();
                        continue_send(value, error_context)
                    }
                    StreamStep::Error(error) => {
                        // Stream didn't handle — pop and propagate. If the
                        // popped frame was a handler body that carries a
                        // k handle, recover the perform-site chain (via
                        // PyK.take()) and route the error there.
                        let popped = seg.frames.pop();
                        if let Some(Frame::Program {
                            handler_k_handle: Some(handle),
                            ..
                        }) = popped
                        {
                            return self.recover_from_k_handle(
                                handle,
                                error,
                                error_context,
                            );
                        }
                        continue_raise(error, error_context)
                    }
                    StreamStep::External(call) => external_result(call, error_context),
                }
            }
            _ => {
                // Non-program frames can't handle errors — pop and propagate
                seg.frames.pop();
                continue_raise(error, error_context)
            }
        }
    }

    // -------------------------------------------------------------------
    // Eval — process a DoCtrl instruction
    // -------------------------------------------------------------------

    fn step_eval(&mut self, doctrl: DoCtrl, mut error_context: Option<Vec<Value>>) -> StepResult {
        match doctrl {
            DoCtrl::Pure { value } => continue_send(value, error_context),

            DoCtrl::Eval { expr } => continue_eval(*expr, error_context),

            DoCtrl::Expand { expr } => {
                // Evaluate inner, expect Value::Stream, push as frame
                match *expr {
                    DoCtrl::Pure { value } => self.push_stream_value(value, error_context),
                    other => {
                        // Push ExpandReturn frame so we intercept the result
                        if let Some(seg_id) = self.current_segment {
                            if let Some(seg) = self.segments.get_mut(seg_id) {
                                seg.push_frame(Frame::EvalReturn(Box::new(
                                    EvalReturnContinuation::ExpandReturn,
                                )));
                            }
                        }
                        continue_eval(other, error_context)
                    }
                }
            }

            DoCtrl::Apply { f, args } => self.eval_apply(*f, args, error_context),

            DoCtrl::Perform { effect } => self.eval_perform(effect, error_context),

            DoCtrl::Resume { mut k, value } => match self.continue_k(&mut k) {
                Ok(()) => continue_send(value, error_context),
                Err(error) => error_result(error, error_context),
            },

            DoCtrl::Transfer { mut k, value } => {
                // Tail position — pop handler frame before resuming
                if let Some(seg_id) = self.current_segment {
                    if let Some(seg) = self.segments.get_mut(seg_id) {
                        seg.frames.pop(); // remove handler stream frame
                    }
                }
                match self.continue_k(&mut k) {
                    Ok(()) => continue_send(value, error_context),
                    Err(error) => error_result(error, error_context),
                }
            }

            DoCtrl::ResumeThrow { mut k, exception } => match self.continue_k(&mut k) {
                Ok(()) => continue_raise(exception, error_context),
                Err(error) => error_result(error, error_context),
            },

            DoCtrl::TransferThrow { mut k, exception } => {
                // Tail position — pop handler frame before resuming
                if let Some(seg_id) = self.current_segment {
                    if let Some(seg) = self.segments.get_mut(seg_id) {
                        seg.frames.pop();
                    }
                }
                match self.continue_k(&mut k) {
                    Ok(()) => continue_raise(exception, error_context),
                    Err(error) => error_result(error, error_context),
                }
            }

            DoCtrl::WithHandler { handler, body } => {
                self.eval_with_handler(handler, *body, error_context)
            }

            DoCtrl::Pass { effect, k } => {
                // Inner handler doesn't handle — forward (effect, k) to outer handler.
                // Pop handler stream, walk up to find next handler boundary.
                self.eval_pass(effect, k, error_context)
            }

            DoCtrl::Delegate { .. } => error_result(
                VMError::type_error(
                    "Delegate is removed. Use 'yield effect' from handler @do body to re-perform."
                        .to_string(),
                ),
                error_context,
            ),

            DoCtrl::WithObserve { observer, body } => {
                self.eval_with_observe(observer, *body, error_context)
            }

            DoCtrl::AllocVar { initial } => {
                if let Some(seg_id) = self.current_segment {
                    let var = self.alloc_scoped_var_in_segment(seg_id, initial);
                    continue_send(Value::Var(var), error_context)
                } else {
                    error_result(
                        VMError::internal("AllocVar: no current segment"),
                        error_context,
                    )
                }
            }

            DoCtrl::ReadVar { var } => {
                if let Some(seg_id) = self.current_segment {
                    match self.read_scoped_var_from(seg_id, var) {
                        Some(value) => continue_send(value, error_context),
                        None => error_result(
                            VMError::internal(format!("ReadVar: variable {:?} not found", var)),
                            error_context,
                        ),
                    }
                } else {
                    error_result(
                        VMError::internal("ReadVar: no current segment"),
                        error_context,
                    )
                }
            }

            DoCtrl::WriteVar { var, value } => {
                if let Some(seg_id) = self.current_segment {
                    self.write_scoped_var_in_current_segment(seg_id, var, value.clone());
                    continue_send(value, error_context)
                } else {
                    error_result(
                        VMError::internal("WriteVar: no current segment"),
                        error_context,
                    )
                }
            }

            DoCtrl::GetTraceback { from } => {
                let locations = self.collect_traceback(from);
                let frames: Vec<Value> = locations
                    .into_iter()
                    .map(|loc| {
                        Value::List(vec![
                            Value::String(loc.func_name),
                            Value::String(loc.source_file),
                            Value::Int(loc.source_line as i64),
                        ])
                    })
                    .collect();
                continue_send(Value::List(frames), error_context)
            }

            DoCtrl::GetExecutionContext => {
                // Return error-site context if available (captured before unwinding),
                // otherwise current live context.
                let frames = error_context
                    .take()
                    .unwrap_or_else(|| self.collect_rich_execution_context());
                continue_send(Value::List(frames), error_context)
            }

            DoCtrl::GetHandlers { from } => {
                // Walk the fiber chain from `from` upward, collecting handler callables.
                let entries = self.handlers_in_caller_chain(from);
                let handlers: Vec<Value> = entries
                    .into_iter()
                    .map(|entry| Value::Callable(entry.handler))
                    .collect();
                continue_send(Value::List(handlers), error_context)
            }

            DoCtrl::GetOuterHandlers => {
                // Walk from current_segment upward — captures handlers OUTSIDE the
                // currently-catching handler. When a handler catches an effect, its
                // segment's parent is detached, so GetHandlers(k) cannot reach above
                // it. This effect walks the current execution chain instead.
                let entries = match self.current_segment {
                    Some(seg) => self.handlers_in_caller_chain(seg),
                    None => Vec::new(),
                };
                let handlers: Vec<Value> = entries
                    .into_iter()
                    .map(|entry| Value::Callable(entry.handler))
                    .collect();
                continue_send(Value::List(handlers), error_context)
            }

            DoCtrl::TailEval { expr } => {
                // Pop the current stream frame (tail-call semantics),
                // then evaluate the inner expression.
                // Transfer/TransferThrow already pop the top frame themselves,
                // so skip the pop for those to avoid double-popping.
                match expr.as_ref() {
                    DoCtrl::Transfer { .. } | DoCtrl::TransferThrow { .. } => {}
                    _ => {
                        if let Some(seg_id) = self.current_segment {
                            if let Some(seg) = self.segments.get_mut(seg_id) {
                                seg.frames.pop();
                            }
                        }
                    }
                }
                continue_eval(*expr, error_context)
            }
        }
    }

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------

    /// Push a Value::Stream as a new Program frame on the current fiber.
    fn push_stream_value(&mut self, value: Value, error_context: Option<Vec<Value>>) -> StepResult {
        // Consume any k handle stashed by eval_perform/eval_perform_with_k
        // so the resulting Program frame can recover the original perform-site
        // chain (via PyK.take()) when its stream raises an uncaught exception.
        let k_handle = self.pending_handler_k_handle.take();
        match value {
            Value::Stream(stream) => {
                if let Some(seg_id) = self.current_segment {
                    if let Some(seg) = self.segments.get_mut(seg_id) {
                        seg.push_frame(Frame::program_with_k_handle(stream, None, k_handle));
                        return continue_send(Value::Unit, error_context);
                    }
                }
                error_result(
                    VMError::internal("push_stream: no current segment"),
                    error_context,
                )
            }
            other => error_result(
                VMError::type_error(format!("Expand: expected Value::Stream, got {:?}", other)),
                error_context,
            ),
        }
    }

    /// Evaluate Apply: call f(args).
    fn eval_apply(
        &mut self,
        f: DoCtrl,
        args: Vec<DoCtrl>,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        // Simple case: f and all args are Pure
        let f_value = match f {
            DoCtrl::Pure { value } => value,
            _ => {
                return error_result(
                    VMError::internal("Apply: non-pure f not yet implemented"),
                    error_context,
                );
            }
        };

        let mut arg_values = Vec::with_capacity(args.len());
        for arg in args {
            match arg {
                DoCtrl::Pure { value } => arg_values.push(value),
                _ => {
                    return error_result(
                        VMError::internal("Apply: non-pure arg not yet implemented"),
                        error_context,
                    );
                }
            }
        }

        match f_value {
            Value::Callable(callable) => {
                match callable.call(arg_values) {
                    Ok(result) => continue_send(result, error_context),
                    Err(VMError::UncaughtException { exception }) => {
                        // Python exception from callable — propagate through
                        // generator stack so try/except blocks can catch it.
                        continue_raise(exception, error_context)
                    }
                    Err(err) => error_result(err, error_context),
                }
            }
            _ => error_result(VMError::internal("Apply: f is not callable"), error_context),
        }
    }

    /// Evaluate Perform: find handler, detach chain, call handler.
    ///
    /// After perform_effect: current_segment = handler's parent.
    ///
    /// OCaml 5 semantics: handler is called, returns a DoExpr (or stream)
    /// which is evaluated on the parent fiber. No manual stream pushing —
    /// handler result is evaluated as a normal program.
    fn eval_perform(&mut self, effect: Value, error_context: Option<Vec<Value>>) -> StepResult {
        let current = match self.current_segment {
            Some(id) => id,
            None => {
                return error_result(
                    VMError::internal("perform: no current segment"),
                    error_context,
                );
            }
        };

        // 1. Call ALL observers in the chain (synchronous, return value ignored)
        self.call_all_observers(current, &effect);

        // 2. Proceed to handler
        let result = match self.perform_effect(&effect) {
            Ok(result) => result,
            Err(step_result) => return step_result,
        };

        let k = result.continuation;
        let handler_callable = result.handler_callable;

        if handler_callable.is_generator_handler() {
            // Generator handler path (Python @do generators): wrap k in a
            // PyK Python object. The PyK is the single home for the chain
            // (SPEC-VM-021). We keep a Py<PyK> handle so that, if the
            // handler raises before consuming `k`, we can borrow the PyK,
            // take() the chain, and reattach it for exception propagation
            // (OCaml 5 semantics). The handle is stashed in
            // `pending_handler_k_handle` so `push_stream_value` can attach
            // it to the resulting Program frame.
            let k_value = pyo3::Python::attach(|py| {
                let py_k = pyo3::Py::new(
                    py,
                    crate::continuation::PyK::from_continuation(k),
                )
                .expect("failed to allocate PyK");
                let handle = py_k.clone_ref(py);
                self.pending_handler_k_handle = Some(handle);
                Value::Opaque(crate::py_shared::PyShared::new(py_k.into_any()))
            });

            let outcome = handler_callable.call_handler(vec![effect, k_value]);

            // If push_stream_value never ran (because outcome was Err or
            // non-Expand), take the handle so it doesn't leak into a future
            // handler call.
            let leftover_handle = self.pending_handler_k_handle.take();

            match outcome {
                Ok(doctrl) => {
                    // Only restore the handle for Expand results (which will
                    // reach push_stream_value). For Pure/other results that
                    // bypass push_stream_value, dropping the handle prevents
                    // the stale-backup-leak bug (the chain stays in PyK and
                    // is freed when the Python K object is GC'd).
                    if matches!(doctrl, DoCtrl::Expand { .. }) {
                        self.pending_handler_k_handle = leftover_handle;
                    }
                    continue_eval(doctrl, error_context)
                }
                Err(VMError::UncaughtException { exception }) => {
                    // Synchronous Python exception from call_handler itself
                    // (the @do wrapper's Expand construction raised). Recover
                    // the chain from the PyK handle.
                    match leftover_handle {
                        Some(handle) => {
                            self.recover_from_k_handle(handle, exception, error_context)
                        }
                        None => continue_raise(exception, error_context),
                    }
                }
                Err(err) => error_result(err, error_context),
            }
        } else {
            // Synchronous handler path (Rust CallableRef): pass the
            // continuation directly as Value::Continuation. No backup
            // handle is needed — call_handler returns immediately, so the
            // continuation is either consumed by the returned DoCtrl or
            // freed on drop if the handler errors.
            let outcome = handler_callable.call_handler(vec![effect, Value::Continuation(k)]);
            match outcome {
                Ok(doctrl) => continue_eval(doctrl, error_context),
                Err(VMError::UncaughtException { exception }) => {
                    continue_raise(exception, error_context)
                }
                Err(err) => error_result(err, error_context),
            }
        }
    }

    /// Recover from a handler-body exception by borrowing the PyK handle,
    /// taking the chain (if the handler didn't consume k), reattaching it,
    /// and raising the exception into the resulting stream. This makes the
    /// inner handler's `<-` site (or the user program's `yield` site) see
    /// the exception via Python's `gen.throw`, matching OCaml 5's semantics
    /// for unhandled exceptions in handler bodies (discontinue k exn).
    ///
    /// If the handler consumed `k` before raising (e.g. Resume followed by
    /// a body-level raise after the resumed continuation returned), the
    /// PyK is empty and we fall through to raising on `current_segment`.
    fn recover_from_k_handle(
        &mut self,
        handle: pyo3::Py<crate::continuation::PyK>,
        exception: Value,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        let continuation = pyo3::Python::attach(|py| {
            let mut k = handle.borrow_mut(py);
            match k.take() {
                Some(crate::continuation::OwnedControlContinuation::Started(c)) => Some(c),
                _ => None,
            }
        });
        if let Some(mut k) = continuation {
            if let Err(error) = self.reattach_chain(&mut k) {
                return error_result(error, error_context);
            }
        }
        continue_raise(exception, error_context)
    }

    /// Walk the entire chain and call all observers synchronously.
    fn call_all_observers(&self, start: FiberId, effect: &Value) {
        let mut cursor = Some(start);
        while let Some(fid) = cursor {
            let Some(seg) = self.segments.get(fid) else {
                break;
            };
            if seg.is_intercept_boundary() {
                if let Some(observer) = seg.intercept_handler().cloned() {
                    let _ = observer.call(vec![effect.clone()]);
                }
            }
            cursor = seg.parent;
        }
    }

    /// Evaluate WithObserve: install observer boundary, create body fiber, evaluate body.
    fn eval_with_observe(
        &mut self,
        observer: Value,
        body: DoCtrl,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        let interceptor_callable = match observer {
            Value::Callable(c) => c,
            other => {
                return error_result(
                    VMError::type_error(format!(
                        "WithObserve: observer must be Callable, got {:?}",
                        other
                    )),
                    error_context,
                );
            }
        };

        let marker = crate::ids::Marker::fresh();
        let handler = crate::segment::Handler::intercept(
            marker,
            interceptor_callable,
            None,
            crate::segment::InterceptMode::Include,
            None,
        );
        let boundary_fid = self.match_with(handler);

        let body_fiber = Fiber::new(Some(boundary_fid));
        let body_fid = self.alloc_segment(body_fiber);

        self.current_segment = Some(body_fid);
        continue_eval(body, error_context)
    }

    /// Evaluate Pass: inner handler doesn't handle, forward to outer handler.
    ///
    /// OCaml 5 semantics (reperform):
    /// k already includes the inner boundary (perform captures boundary in k).
    /// Handler runs on the parent. We just need to do a new perform from the
    /// parent to find the outer handler.
    fn eval_pass(
        &mut self,
        effect: Value,
        k: Continuation,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        // Handler code runs on the parent fiber. The handler stream frame
        // yielded Pass — it's done. Pop it before extending k, otherwise
        // the dead frame gets hit again when the continuation is resumed.
        if let Some(seg_id) = self.current_segment {
            if let Some(seg) = self.segments.get_mut(seg_id) {
                seg.frames.pop();
            }
        }
        // k already includes body → ... → inner_boundary (because perform
        // includes boundary in continuation). Re-perform from current
        // position to find the outer handler.
        self.eval_perform_with_k(effect, k, error_context)
    }

    /// Perform an effect with an existing continuation (used by Pass).
    /// Walks from current_segment to find the next handler, then calls it.
    fn eval_perform_with_k(
        &mut self,
        effect: Value,
        k: Continuation,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        let current = match self.current_segment {
            Some(id) => id,
            None => {
                return error_result(
                    VMError::internal("perform_with_k: no current segment"),
                    error_context,
                );
            }
        };

        // Find handler walking up from current
        let (handler_fiber_id, _handler_parent) =
            match self.find_handler_for_effect(current, &effect) {
                Some(result) => result,
                None => {
                    let mut context = k.collect_rich_context().unwrap_or_default();
                    if let Some(current) = self.current_segment {
                        context.extend(self.collect_rich_context_from(current));
                    }
                    // Reattach the detached chain so the raised UnhandledEffect surfaces to the
                    // user body's try/except via should_raise_into_stream. Unlike eval_perform,
                    // eval_perform_with_k arrives here after perform dispatch pre-detached k, so
                    // current_segment lacks the user body's program frame; reattach_chain restores
                    // it before the error is raised.
                    let mut k = k;
                    if let Err(error) = self.reattach_chain(&mut k) {
                        return error_result(error, Some(context));
                    }
                    return error_result(VMError::no_matching_handler(effect), Some(context));
                }
            };

        let handler_callable = self
            .segments
            .get(handler_fiber_id)
            .and_then(|seg| seg.prompt_handler().cloned());

        let Some(handler_callable) = handler_callable else {
            return error_result(
                VMError::internal("Pass: outer handler has no callable"),
                error_context,
            );
        };

        // Extend k: include fibers from current up to (and including) the outer boundary.
        let boundary_parent = self.segments.get(handler_fiber_id).and_then(|s| s.parent);
        let mut k = k;
        let chain = match self.segments.detach_chain(current, handler_fiber_id) {
            Ok(chain) => chain,
            Err(error) => return error_result(error, error_context),
        };
        if !k.append_chain(chain) {
            return error_result(
                VMError::internal("Pass: continuation already consumed"),
                error_context,
            );
        }

        // Switch to outer handler's parent
        self.current_segment = boundary_parent;

        if handler_callable.is_generator_handler() {
            // Generator handler path — see eval_perform for rationale.
            let k_value = pyo3::Python::attach(|py| {
                let py_k = pyo3::Py::new(
                    py,
                    crate::continuation::PyK::from_continuation(k),
                )
                .expect("failed to allocate PyK");
                let handle = py_k.clone_ref(py);
                self.pending_handler_k_handle = Some(handle);
                Value::Opaque(crate::py_shared::PyShared::new(py_k.into_any()))
            });

            let outcome = handler_callable.call_handler(vec![effect, k_value]);
            let leftover_handle = self.pending_handler_k_handle.take();

            match outcome {
                Ok(doctrl) => {
                    if matches!(doctrl, DoCtrl::Expand { .. }) {
                        self.pending_handler_k_handle = leftover_handle;
                    }
                    continue_eval(doctrl, error_context)
                }
                Err(VMError::UncaughtException { exception }) => match leftover_handle {
                    Some(handle) => {
                        self.recover_from_k_handle(handle, exception, error_context)
                    }
                    None => continue_raise(exception, error_context),
                },
                Err(err) => error_result(err, error_context),
            }
        } else {
            // Synchronous handler path — see eval_perform for rationale.
            let outcome =
                handler_callable.call_handler(vec![effect, Value::Continuation(k)]);
            match outcome {
                Ok(doctrl) => continue_eval(doctrl, error_context),
                Err(VMError::UncaughtException { exception }) => {
                    continue_raise(exception, error_context)
                }
                Err(err) => error_result(err, error_context),
            }
        }
    }

    /// Evaluate WithHandler: install handler boundary, create body fiber, evaluate body DoExpr.
    ///
    /// OCaml 5 model:
    ///   boundary_fiber = alloc(parent = current)
    ///   boundary_fiber.handler = handler
    ///   body_fiber = alloc(parent = boundary_fiber)
    ///   current = body_fiber
    ///   evaluate body DoExpr on body_fiber
    fn eval_with_handler(
        &mut self,
        handler: Value,
        body: DoCtrl,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        let handler_callable = match handler {
            Value::Callable(c) => c,
            other => {
                return error_result(
                    VMError::type_error(format!(
                        "WithHandler: handler must be Callable, got {:?}",
                        other
                    )),
                    error_context,
                );
            }
        };

        // 1. Create boundary fiber with handler
        let marker = crate::ids::Marker::fresh();
        let handler_obj = crate::segment::Handler::prompt(marker, marker, handler_callable, None);
        let boundary_fid = self.match_with(handler_obj);

        // 2. Create a SEPARATE body fiber whose parent is the boundary
        let body_fiber = Fiber::new(Some(boundary_fid));
        let body_fid = self.alloc_segment(body_fiber);

        // 3. Switch to body fiber and evaluate body DoExpr
        self.current_segment = Some(body_fid);
        continue_eval(body, error_context)
    }

    /// Process an EvalReturn frame with the delivered value.
    fn step_eval_return(
        &mut self,
        eval_return: EvalReturnContinuation,
        value: Value,
        error_context: Option<Vec<Value>>,
    ) -> StepResult {
        match eval_return {
            EvalReturnContinuation::ResumeToContinuation { head_fiber } => {
                match self.continue_attached_chain(head_fiber, head_fiber) {
                    Ok(()) => continue_send(value, error_context),
                    Err(error) => error_result(error, error_context),
                }
            }
            EvalReturnContinuation::ReturnToContinuation { head_fiber } => {
                match self.continue_attached_chain(head_fiber, head_fiber) {
                    Ok(()) => continue_send(value, error_context),
                    Err(error) => error_result(error, error_context),
                }
            }
            EvalReturnContinuation::EvalInScopeReturn { head_fiber } => {
                match self.continue_attached_chain(head_fiber, head_fiber) {
                    Ok(()) => continue_send(value, error_context),
                    Err(error) => error_result(error, error_context),
                }
            }
            EvalReturnContinuation::TailResumeReturn => continue_send(value, error_context),
            EvalReturnContinuation::ExpandReturn => self.push_stream_value(value, error_context),
            _ => {
                // Other EvalReturn variants — TODO
                continue_send(value, error_context)
            }
        }
    }
}

fn continue_eval(doctrl: DoCtrl, error_context: Option<Vec<Value>>) -> StepResult {
    StepResult::Continue(Signal::eval(doctrl).with_error_context(error_context))
}

fn continue_send(value: Value, error_context: Option<Vec<Value>>) -> StepResult {
    StepResult::Continue(Signal::send(value).with_error_context(error_context))
}

fn continue_raise(error: Value, error_context: Option<Vec<Value>>) -> StepResult {
    StepResult::Continue(Signal::raise(error).with_error_context(error_context))
}

fn external_result(
    call: crate::driver::ExternalCall,
    error_context: Option<Vec<Value>>,
) -> StepResult {
    StepResult::External {
        call,
        context: error_context,
    }
}

fn error_result(error: VMError, context: Option<Vec<Value>>) -> StepResult {
    StepResult::Error { error, context }
}
