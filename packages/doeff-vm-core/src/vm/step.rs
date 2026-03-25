//! Step machine — drives the VM one step at a time.
//!
//! Modes:
//!   Eval(DoCtrl)  — evaluate an instruction
//!   Send(Value)   — send value to current stream
//!   Raise(Value)  — signal error to current stream
//!
//! The step machine is a simple loop: take Mode, process it, set next Mode.
//! No implicit behavior. No Python. No trace state.

use crate::continuation::Continuation;
use crate::do_ctrl::DoCtrl;
use crate::driver::{ExternalCall, Mode, StepResult};
use crate::error::VMError;
use crate::frame::{EvalReturnContinuation, Frame};
use crate::segment::Fiber;
use crate::ids::{FiberId, VarId};
use crate::ir_stream::StreamStep;
use crate::value::Value;
use crate::vm::VM;

impl VM {
    /// Execute one step.
    pub fn step(&mut self) -> StepResult {
        let mode = std::mem::replace(&mut self.mode, Mode::Send(Value::Unit));
        match mode {
            Mode::Eval(doctrl) => self.step_eval(doctrl),
            Mode::Send(value) => self.step_send(value),
            Mode::Raise(error) => self.step_raise(error),
        }
    }

    // -------------------------------------------------------------------
    // Send — deliver a value to the current stream
    // -------------------------------------------------------------------

    fn step_send(&mut self, value: Value) -> StepResult {
        let Some(seg_id) = self.current_segment else {
            return StepResult::Done(value);
        };
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return StepResult::Error(VMError::internal("send: segment not found"));
        };

        match seg.frames.last() {
            None => {
                // No frames — fiber complete, go to parent
                let parent = seg.parent;
                self.current_segment = parent;
                if parent.is_some() {
                    self.mode = Mode::Send(value);
                    StepResult::Continue
                } else {
                    StepResult::Done(value)
                }
            }
            Some(Frame::Program { .. }) => {
                let Frame::Program { stream, .. } = seg.frames.last().unwrap() else {
                    unreachable!()
                };
                match stream.resume(value) {
                    StreamStep::Instruction(doctrl) => {
                        self.mode = Mode::Eval(doctrl);
                        StepResult::Continue
                    }
                    StreamStep::Done(value) => {
                        seg.frames.pop();
                        self.mode = Mode::Send(value);
                        StepResult::Continue
                    }
                    StreamStep::Error(error) => {
                        self.mode = Mode::Raise(error);
                        StepResult::Continue
                    }
                    StreamStep::External(call) => {
                        StepResult::External(call)
                    }
                }
            }
            Some(Frame::EvalReturn(_)) => {
                let Frame::EvalReturn(eval_return) = seg.frames.pop().unwrap() else {
                    unreachable!()
                };
                self.step_eval_return(*eval_return, value)
            }
            Some(Frame::LexicalScope { .. }) => {
                // Scope frame — skip, deliver to next frame
                // TODO: may need to pop scope on exit
                self.mode = Mode::Send(value);
                StepResult::Continue
            }
            Some(Frame::MapReturn { .. } | Frame::FlatMapBindResult
                | Frame::FlatMapBindSource { .. }) => {
                // Legacy frames — remove in future cleanup
                seg.frames.pop();
                self.mode = Mode::Send(value);
                StepResult::Continue
            }
        }
    }

    // -------------------------------------------------------------------
    // Raise — signal error to the current stream
    // -------------------------------------------------------------------

    fn step_raise(&mut self, error: Value) -> StepResult {
        let Some(seg_id) = self.current_segment else {
            return StepResult::Error(VMError::uncaught_exception(error));
        };
        let Some(seg) = self.segments.get_mut(seg_id) else {
            return StepResult::Error(VMError::internal("raise: segment not found"));
        };

        match seg.frames.last() {
            None => {
                // No frames — propagate to parent
                let parent = seg.parent;
                self.current_segment = parent;
                if parent.is_some() {
                    self.mode = Mode::Raise(error);
                    StepResult::Continue
                } else {
                    StepResult::Error(VMError::uncaught_exception(error))
                }
            }
            Some(Frame::Program { .. }) => {
                let Frame::Program { stream, .. } = seg.frames.last().unwrap() else {
                    unreachable!()
                };
                match stream.throw(error) {
                    StreamStep::Instruction(doctrl) => {
                        self.mode = Mode::Eval(doctrl);
                        StepResult::Continue
                    }
                    StreamStep::Done(value) => {
                        seg.frames.pop();
                        self.mode = Mode::Send(value);
                        StepResult::Continue
                    }
                    StreamStep::Error(error) => {
                        // Stream didn't handle — pop and propagate
                        seg.frames.pop();
                        self.mode = Mode::Raise(error);
                        StepResult::Continue
                    }
                    StreamStep::External(call) => {
                        StepResult::External(call)
                    }
                }
            }
            _ => {
                // Non-program frames can't handle errors — pop and propagate
                seg.frames.pop();
                self.mode = Mode::Raise(error);
                StepResult::Continue
            }
        }
    }

    // -------------------------------------------------------------------
    // Eval — process a DoCtrl instruction
    // -------------------------------------------------------------------

    fn step_eval(&mut self, doctrl: DoCtrl) -> StepResult {
        match doctrl {
            DoCtrl::Pure { value } => {
                self.mode = Mode::Send(value);
                StepResult::Continue
            }

            DoCtrl::Eval { expr } => {
                self.mode = Mode::Eval(*expr);
                StepResult::Continue
            }

            DoCtrl::Expand { expr } => {
                // Evaluate inner, expect Value::Stream, push as frame
                match *expr {
                    DoCtrl::Pure { value } => {
                        self.push_stream_value(value)
                    }
                    other => {
                        // Push ExpandReturn frame so we intercept the result
                        if let Some(seg_id) = self.current_segment {
                            if let Some(seg) = self.segments.get_mut(seg_id) {
                                seg.push_frame(Frame::EvalReturn(Box::new(
                                    EvalReturnContinuation::ExpandReturn,
                                )));
                            }
                        }
                        self.mode = Mode::Eval(other);
                        StepResult::Continue
                    }
                }
            }

            DoCtrl::Apply { f, args } => {
                self.eval_apply(*f, args)
            }

            DoCtrl::Perform { effect } => {
                self.eval_perform(effect)
            }

            DoCtrl::Resume { mut k, value } => {
                match self.continue_k(&mut k, value) {
                    Ok(()) => StepResult::Continue,
                    Err(event) => event,
                }
            }

            DoCtrl::Transfer { mut k, value } => {
                // Tail position — pop handler frame before resuming
                if let Some(seg_id) = self.current_segment {
                    if let Some(seg) = self.segments.get_mut(seg_id) {
                        seg.frames.pop(); // remove handler stream frame
                    }
                }
                match self.continue_k(&mut k, value) {
                    Ok(()) => StepResult::Continue,
                    Err(event) => event,
                }
            }

            DoCtrl::ResumeThrow { mut k, exception } => {
                match self.continue_k(&mut k, Value::Unit) {
                    Ok(()) => {
                        self.mode = Mode::Raise(exception);
                        StepResult::Continue
                    }
                    Err(event) => event,
                }
            }

            DoCtrl::TransferThrow { mut k, exception } => {
                // Tail position — pop handler frame before resuming
                if let Some(seg_id) = self.current_segment {
                    if let Some(seg) = self.segments.get_mut(seg_id) {
                        seg.frames.pop();
                    }
                }
                match self.continue_k(&mut k, Value::Unit) {
                    Ok(()) => {
                        self.mode = Mode::Raise(exception);
                        StepResult::Continue
                    }
                    Err(event) => event,
                }
            }

            DoCtrl::WithHandler { handler, body } => {
                self.eval_with_handler(handler, *body)
            }

            DoCtrl::Pass { effect, k } => {
                // Inner handler doesn't handle — forward (effect, k) to outer handler.
                // Pop handler stream, walk up to find next handler boundary.
                self.eval_pass(effect, k)
            }

            DoCtrl::Delegate { effect, k } => {
                // Same as Pass for now.
                // TODO: append current handler fiber to k before forwarding.
                self.eval_pass(effect, k)
            }

            DoCtrl::WithIntercept { interceptor, body } => {
                self.eval_with_intercept(interceptor, *body)
            }

            DoCtrl::AllocVar { initial } => {
                if let Some(seg_id) = self.current_segment {
                    let var = self.alloc_scoped_var_in_segment(seg_id, initial);
                    self.mode = Mode::Send(Value::Var(var));
                } else {
                    self.mode = Mode::Raise(Value::String("AllocVar: no current segment".into()));
                }
                StepResult::Continue
            }

            DoCtrl::ReadVar { var } => {
                if let Some(seg_id) = self.current_segment {
                    match self.read_scoped_var_from(seg_id, var) {
                        Some(value) => self.mode = Mode::Send(value),
                        None => self.mode = Mode::Raise(Value::String(
                            format!("ReadVar: variable {:?} not found", var)
                        )),
                    }
                } else {
                    self.mode = Mode::Raise(Value::String("ReadVar: no current segment".into()));
                }
                StepResult::Continue
            }

            DoCtrl::WriteVar { var, value } => {
                if let Some(seg_id) = self.current_segment {
                    self.write_scoped_var_in_current_segment(seg_id, var, value.clone());
                    self.mode = Mode::Send(value);
                } else {
                    self.mode = Mode::Raise(Value::String("WriteVar: no current segment".into()));
                }
                StepResult::Continue
            }

            DoCtrl::GetTraceback { from } => {
                let locations = self.collect_traceback(from);
                let frames: Vec<Value> = locations
                    .into_iter()
                    .map(|loc| Value::List(vec![
                        Value::String(loc.func_name),
                        Value::String(loc.source_file),
                        Value::Int(loc.source_line as i64),
                    ]))
                    .collect();
                self.mode = Mode::Send(Value::List(frames));
                StepResult::Continue
            }
        }
    }

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------

    /// Push a Value::Stream as a new Program frame on the current fiber.
    fn push_stream_value(&mut self, value: Value) -> StepResult {
        match value {
            Value::Stream(stream) => {
                if let Some(seg_id) = self.current_segment {
                    if let Some(seg) = self.segments.get_mut(seg_id) {
                        seg.push_frame(Frame::program(stream, None));
                        self.mode = Mode::Send(Value::Unit);
                        return StepResult::Continue;
                    }
                }
                StepResult::Error(VMError::internal("push_stream: no current segment"))
            }
            other => {
                StepResult::Error(VMError::type_error(format!(
                    "Expand: expected Value::Stream, got {:?}", other
                )))
            }
        }
    }

    /// Evaluate Apply: call f(args).
    fn eval_apply(&mut self, f: DoCtrl, args: Vec<DoCtrl>) -> StepResult {
        // Simple case: f and all args are Pure
        let f_value = match f {
            DoCtrl::Pure { value } => value,
            _ => {
                // TODO: staged evaluation with EvalReturn frames
                self.mode = Mode::Raise(Value::String(
                    "Apply: non-pure f not yet implemented".into()
                ));
                return StepResult::Continue;
            }
        };

        let mut arg_values = Vec::with_capacity(args.len());
        for arg in args {
            match arg {
                DoCtrl::Pure { value } => arg_values.push(value),
                _ => {
                    // TODO: staged arg evaluation
                    self.mode = Mode::Raise(Value::String(
                        "Apply: non-pure arg not yet implemented".into()
                    ));
                    return StepResult::Continue;
                }
            }
        }

        match f_value {
            Value::Callable(callable) => {
                match callable.call(arg_values) {
                    Ok(result) => {
                        self.mode = Mode::Send(result);
                        StepResult::Continue
                    }
                    Err(err) => StepResult::Error(err),
                }
            }
            _ => {
                self.mode = Mode::Raise(Value::String("Apply: f is not callable".into()));
                StepResult::Continue
            }
        }
    }

    /// Evaluate Perform: find handler, detach chain, call handler.
    ///
    /// After perform_effect: current_segment = handler's parent.
    ///
    /// OCaml 5 semantics: handler is called, returns a DoExpr (or stream)
    /// which is evaluated on the parent fiber. No manual stream pushing —
    /// handler result is evaluated as a normal program.
    fn eval_perform(&mut self, effect: Value) -> StepResult {
        self.eval_perform_with_skip(effect, None)
    }

    fn eval_perform_with_skip(&mut self, effect: Value, skip_intercept: Option<FiberId>) -> StepResult {
        let current = match self.current_segment {
            Some(id) => id,
            None => return StepResult::Error(VMError::internal("perform: no current segment")),
        };

        // 1. Check for interceptor first (skip the one we already invoked)
        let boundary = self.find_next_boundary(current, &effect);
        if let Some((boundary_fid, _boundary_parent, true)) = boundary {
            if skip_intercept != Some(boundary_fid) {
                return self.eval_intercept(effect, boundary_fid);
            }
        }

        // 2. No (new) interceptor — proceed to handler
        let result = match self.perform_effect(&effect) {
            Ok(result) => result,
            Err(step_result) => return step_result,
        };

        let handler_fiber_id = result.handler_fiber_id;
        let k = result.continuation;

        // 3. Get the handler callable from the handler boundary fiber
        let handler_callable = self.segments.get(handler_fiber_id)
            .and_then(|seg| seg.prompt_handler().cloned());

        let Some(handler_callable) = handler_callable else {
            return StepResult::Error(VMError::internal("perform: handler has no callable"));
        };

        // 4. Call handler(effect, k) → must return a DoExpr.
        match handler_callable.call_handler(vec![effect, Value::Continuation(k)]) {
            Ok(doctrl) => {
                self.mode = Mode::Eval(doctrl);
                StepResult::Continue
            }
            Err(err) => StepResult::Error(err),
        }
    }

    /// Evaluate an interceptor: call interceptor(effect) → effect.
    /// Always returns an effect. Passthrough = return the same effect.
    /// The transformed effect is then performed normally (full perform from body).
    /// The interceptor is skipped by temporarily removing it during perform.
    fn eval_intercept(&mut self, effect: Value, intercept_fid: FiberId) -> StepResult {
        let interceptor = self.segments.get(intercept_fid)
            .and_then(|seg| seg.intercept_handler().cloned());

        let Some(interceptor) = interceptor else {
            return StepResult::Error(VMError::internal("intercept: no interceptor callable"));
        };

        let new_effect = match interceptor.call(vec![effect]) {
            Ok(value) => value,
            Err(err) => return StepResult::Error(err),
        };

        // Re-perform with new effect, skipping this interceptor
        self.eval_perform_with_skip(new_effect, Some(intercept_fid))
    }

    /// Evaluate WithIntercept: install interceptor boundary, create body fiber, evaluate body.
    fn eval_with_intercept(&mut self, interceptor: Value, body: DoCtrl) -> StepResult {
        let interceptor_callable = match interceptor {
            Value::Callable(c) => c,
            other => {
                return StepResult::Error(VMError::type_error(format!(
                    "WithIntercept: interceptor must be Callable, got {:?}", other
                )));
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
        self.mode = Mode::Eval(body);
        StepResult::Continue
    }

    /// Evaluate Pass: inner handler doesn't handle, forward to outer handler.
    ///
    /// OCaml 5 semantics (reperform):
    /// k already includes the inner boundary (perform captures boundary in k).
    /// Handler runs on the parent. We just need to do a new perform from the
    /// parent to find the outer handler.
    fn eval_pass(&mut self, effect: Value, k: Continuation) -> StepResult {
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
        self.eval_perform_with_k(effect, k)
    }

    /// Perform an effect with an existing continuation (used by Pass).
    /// Walks from current_segment to find the next handler, then calls it.
    fn eval_perform_with_k(&mut self, effect: Value, k: Continuation) -> StepResult {
        let current = match self.current_segment {
            Some(id) => id,
            None => return StepResult::Error(VMError::internal("perform_with_k: no current segment")),
        };

        // Find handler walking up from current
        let (handler_fiber_id, handler_parent) = match self.find_handler_for_effect(current, &effect) {
            Some(result) => result,
            None => return StepResult::Error(VMError::internal("Pass: no outer handler found")),
        };

        // Extend k: include fibers from current up to (and including) the outer boundary
        // Detach outer boundary from its parent
        let boundary_parent = self.segments.get(handler_fiber_id).and_then(|s| s.parent);
        if let Some(seg) = self.segments.get_mut(handler_fiber_id) {
            seg.parent = None;
        }

        // Link k.last → current (extend the continuation with the intermediate chain)
        let mut k = k;
        if let Some(last) = k.last_fiber() {
            if let Some(seg) = self.segments.get_mut(last) {
                seg.parent = Some(current);
            }
        }
        k.last_fiber = Some(handler_fiber_id);

        // Switch to outer handler's parent
        self.current_segment = boundary_parent;

        // Get handler callable
        let handler_callable = self.segments.get(handler_fiber_id)
            .and_then(|seg| seg.prompt_handler().cloned());

        let Some(handler_callable) = handler_callable else {
            return StepResult::Error(VMError::internal("Pass: outer handler has no callable"));
        };

        // Call handler — must return DoExpr
        match handler_callable.call_handler(vec![effect, Value::Continuation(k)]) {
            Ok(doctrl) => {
                self.mode = Mode::Eval(doctrl);
                StepResult::Continue
            }
            Err(err) => StepResult::Error(err),
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
    fn eval_with_handler(&mut self, handler: Value, body: DoCtrl) -> StepResult {
        let handler_callable = match handler {
            Value::Callable(c) => c,
            other => {
                return StepResult::Error(VMError::type_error(format!(
                    "WithHandler: handler must be Callable, got {:?}", other
                )));
            }
        };

        // 1. Create boundary fiber with handler
        let marker = crate::ids::Marker::fresh();
        let handler_obj = crate::segment::Handler::prompt(
            marker,
            marker,
            handler_callable,
            None,
        );
        let boundary_fid = self.match_with(handler_obj);

        // 2. Create a SEPARATE body fiber whose parent is the boundary
        let body_fiber = Fiber::new(Some(boundary_fid));
        let body_fid = self.alloc_segment(body_fiber);

        // 3. Switch to body fiber and evaluate body DoExpr
        self.current_segment = Some(body_fid);
        self.mode = Mode::Eval(body);
        StepResult::Continue
    }

    /// Process an EvalReturn frame with the delivered value.
    fn step_eval_return(&mut self, eval_return: EvalReturnContinuation, value: Value) -> StepResult {
        match eval_return {
            EvalReturnContinuation::ResumeToContinuation { head_fiber } => {
                let mut k = Continuation::new(head_fiber, head_fiber);
                match self.continue_k(&mut k, value) {
                    Ok(()) => StepResult::Continue,
                    Err(event) => event,
                }
            }
            EvalReturnContinuation::ReturnToContinuation { head_fiber } => {
                let mut k = Continuation::new(head_fiber, head_fiber);
                match self.continue_k(&mut k, value) {
                    Ok(()) => StepResult::Continue,
                    Err(event) => event,
                }
            }
            EvalReturnContinuation::EvalInScopeReturn { head_fiber } => {
                let mut k = Continuation::new(head_fiber, head_fiber);
                match self.continue_k(&mut k, value) {
                    Ok(()) => StepResult::Continue,
                    Err(event) => event,
                }
            }
            EvalReturnContinuation::TailResumeReturn => {
                self.mode = Mode::Send(value);
                StepResult::Continue
            }
            EvalReturnContinuation::ExpandReturn => {
                self.push_stream_value(value)
            }
            _ => {
                // Other EvalReturn variants — TODO
                self.mode = Mode::Send(value);
                StepResult::Continue
            }
        }
    }

    /// Receive result from an external call.
    pub fn receive_external_result(&mut self, result: Result<Value, Value>) {
        match result {
            Ok(value) => self.mode = Mode::Send(value),
            Err(error) => self.mode = Mode::Raise(error),
        }
    }
}
