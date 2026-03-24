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
use crate::ids::VarId;
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
            return StepResult::Error(VMError::internal(format!("uncaught error: {:?}", error)));
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
                    StepResult::Error(VMError::internal(format!("uncaught error: {:?}", error)))
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
                // TODO: need staged evaluation — for now handle Pure case
                match *expr {
                    DoCtrl::Pure { value } => {
                        self.push_stream_value(value)
                    }
                    other => {
                        // Need to evaluate first, then expand result
                        // Push an ExpandReturn frame and evaluate
                        self.mode = Mode::Eval(other);
                        // TODO: push frame to remember we need to expand the result
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
                self.eval_with_handler(handler, body)
            }

            DoCtrl::Pass => {
                // TODO: reperform at outer handler
                self.mode = Mode::Raise(Value::String("Pass: not yet implemented".into()));
                StepResult::Continue
            }

            DoCtrl::Delegate => {
                // TODO: delegate to outer handler
                self.mode = Mode::Raise(Value::String("Delegate: not yet implemented".into()));
                StepResult::Continue
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
                        seg.push_frame(Frame::Program { stream, metadata: None });
                        self.mode = Mode::Send(Value::Unit);
                        return StepResult::Continue;
                    }
                }
                StepResult::Error(VMError::internal("push_stream: no current segment"))
            }
            other => {
                // Not a stream — just deliver the value
                self.mode = Mode::Send(other);
                StepResult::Continue
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
    /// After perform_effect: current_segment = handler's parent (root).
    /// We push the handler stream on the BOUNDARY fiber so that when
    /// Resume re-links body.parent → boundary, the chain is correct.
    fn eval_perform(&mut self, effect: Value) -> StepResult {
        // 1. Use dispatch to find handler and detach chain
        let result = match self.perform_effect(&effect) {
            Ok(result) => result,
            Err(step_result) => return step_result,
        };

        let handler_fiber_id = result.handler_fiber_id;
        let k = result.continuation;

        // 2. Get the handler callable from the handler boundary fiber
        let handler_callable = self.segments.get(handler_fiber_id)
            .and_then(|seg| seg.prompt_handler().cloned());

        let Some(handler_callable) = handler_callable else {
            return StepResult::Error(VMError::internal("perform: handler has no callable"));
        };

        // 3. Call handler(effect, k) → should return Value::Stream
        match handler_callable.call(vec![effect, Value::Continuation(k)]) {
            Ok(Value::Stream(stream)) => {
                // Push handler stream on the BOUNDARY fiber (not root)
                // This way, when Resume links body.parent → boundary,
                // the next perform from body can find the handler.
                if let Some(seg) = self.segments.get_mut(handler_fiber_id) {
                    seg.push_frame(Frame::program(stream, None));
                }
                // Switch to boundary fiber to run the handler stream
                self.current_segment = Some(handler_fiber_id);
                self.mode = Mode::Send(Value::Unit);
                StepResult::Continue
            }
            Ok(other) => {
                self.mode = Mode::Send(other);
                StepResult::Continue
            }
            Err(err) => StepResult::Error(err),
        }
    }

    /// Evaluate WithHandler: install handler boundary, create body fiber, execute body.
    ///
    /// OCaml 5 model:
    ///   boundary_fiber = alloc(parent = current)
    ///   boundary_fiber.handler = handler
    ///   body_fiber = alloc(parent = boundary_fiber)
    ///   current = body_fiber
    ///   execute body on body_fiber
    fn eval_with_handler(&mut self, handler: Value, body: Value) -> StepResult {
        let handler_callable = match handler {
            Value::Callable(c) => c,
            _ => {
                self.mode = Mode::Raise(Value::String("WithHandler: handler is not callable".into()));
                return StepResult::Continue;
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
        // current_segment is now boundary_fid

        // 2. Create a SEPARATE body fiber whose parent is the boundary
        let body_fiber = Fiber::new(Some(boundary_fid));
        let body_fid = self.alloc_segment(body_fiber);

        // 3. Push body stream on the body fiber
        let stream = match body {
            Value::Stream(s) => s,
            Value::Callable(callable) => {
                match callable.call(vec![]) {
                    Ok(Value::Stream(s)) => s,
                    Ok(other) => {
                        self.mode = Mode::Send(other);
                        return StepResult::Continue;
                    }
                    Err(err) => return StepResult::Error(err),
                }
            }
            _ => {
                self.mode = Mode::Raise(Value::String("WithHandler: body must be Stream or Callable".into()));
                return StepResult::Continue;
            }
        };

        if let Some(body_seg) = self.segments.get_mut(body_fid) {
            body_seg.push_frame(Frame::program(stream, None));
        }

        // 4. Switch to body fiber
        self.current_segment = Some(body_fid);
        self.mode = Mode::Send(Value::Unit);
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
