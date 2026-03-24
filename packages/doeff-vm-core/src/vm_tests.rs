//! Tests for the VM step machine — pure Rust, no Python.
//!
//! Each test constructs IRStreams (mock generators) that yield DoCtrl
//! instructions, then runs the VM step loop to verify correct behavior.

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::do_ctrl::DoCtrl;
    use crate::driver::{Mode, StepResult};
    use crate::error::VMError;
    use crate::frame::Frame;
    use crate::ids::FiberId;
    use crate::ir_stream::{IRStream, IRStreamRef, StreamStep};
    use crate::segment::Fiber;
    use crate::value::{Callable, CallableRef, Value};
    use crate::vm::VM;

    // -----------------------------------------------------------------------
    // Test helpers — mock IRStreams
    // -----------------------------------------------------------------------

    /// A stream that yields a sequence of DoCtrl, then returns a final value.
    #[derive(Debug)]
    struct ScriptStream {
        steps: Vec<DoCtrl>,
        final_value: Value,
        index: usize,
    }

    impl ScriptStream {
        fn new(steps: Vec<DoCtrl>, final_value: Value) -> Self {
            Self { steps, final_value, index: 0 }
        }

        fn returning(value: Value) -> Self {
            Self::new(vec![], value)
        }

        fn yielding_then_return(steps: Vec<DoCtrl>, final_value: Value) -> Self {
            Self::new(steps, final_value)
        }
    }

    impl IRStream for ScriptStream {
        fn resume(&mut self, _value: Value) -> StreamStep {
            if self.index < self.steps.len() {
                let step = std::mem::replace(
                    &mut self.steps[self.index],
                    DoCtrl::Pure { value: Value::Unit },
                );
                self.index += 1;
                StreamStep::Instruction(step)
            } else {
                StreamStep::Done(self.final_value.clone())
            }
        }

        fn throw(&mut self, error: Value) -> StreamStep {
            StreamStep::Error(error)
        }
    }

    /// A handler stream that receives (effect, k), yields a DoCtrl, then returns.
    #[derive(Debug)]
    struct HandlerStream {
        /// What to yield when resumed with the effect+k
        response: Option<DoCtrl>,
    }

    impl HandlerStream {
        fn resume_with(doctrl: DoCtrl) -> Self {
            Self { response: Some(doctrl) }
        }
    }

    impl IRStream for HandlerStream {
        fn resume(&mut self, _value: Value) -> StreamStep {
            match self.response.take() {
                Some(doctrl) => StreamStep::Instruction(doctrl),
                None => StreamStep::Done(Value::Unit),
            }
        }

        fn throw(&mut self, error: Value) -> StreamStep {
            StreamStep::Error(error)
        }
    }

    // -----------------------------------------------------------------------
    // Helper: run VM to completion
    // -----------------------------------------------------------------------

    fn run_to_completion(vm: &mut VM) -> Result<Value, VMError> {
        for _ in 0..1000 {
            match vm.step() {
                StepResult::Continue => continue,
                StepResult::Done(value) => return Ok(value),
                StepResult::Error(err) => return Err(err),
                StepResult::External(_) => {
                    return Err(VMError::internal("unexpected external call in test"))
                }
            }
        }
        Err(VMError::internal("step limit exceeded"))
    }

    fn setup_vm_with_stream(stream: impl IRStream + 'static) -> VM {
        let mut vm = VM::new();
        let stream_ref = IRStreamRef::new(Box::new(stream));
        let mut fiber = Fiber::new(None);
        fiber.push_frame(Frame::program(stream_ref, None));
        let fid = vm.alloc_segment(fiber);
        vm.current_segment = Some(fid);
        vm.mode = Mode::Send(Value::Unit); // initial resume
        vm
    }

    /// Helper: wrap a stream as a DoCtrl body for WithHandler.
    fn expand_stream(stream: impl IRStream + 'static) -> Box<DoCtrl> {
        let stream_ref = IRStreamRef::new(Box::new(stream));
        Box::new(DoCtrl::Expand {
            expr: Box::new(DoCtrl::Pure { value: Value::Stream(stream_ref) }),
        })
    }

    // -----------------------------------------------------------------------
    // Test 1: Pure → Done
    // -----------------------------------------------------------------------

    #[test]
    fn test_pure_returns_value() {
        let stream = ScriptStream::new(
            vec![DoCtrl::Pure { value: Value::Int(42) }],
            Value::Unit,
        );
        let mut vm = setup_vm_with_stream(stream);

        // First step: resume stream → yields Pure(42)
        // Second step: eval Pure(42) → Send(42)
        // Third step: send 42 to stream → stream returns Unit
        // Fourth step: send Unit → no frames, done
        let result = run_to_completion(&mut vm);
        // The stream yields Pure(42), VM delivers 42 back to stream,
        // stream then returns Unit
        assert!(result.is_ok());
    }

    #[test]
    fn test_stream_returns_directly() {
        let stream = ScriptStream::returning(Value::Int(99));
        let mut vm = setup_vm_with_stream(stream);

        let result = run_to_completion(&mut vm).unwrap();
        match result {
            Value::Int(99) => {} // correct
            other => panic!("expected Int(99), got {:?}", other),
        }
    }

    // -----------------------------------------------------------------------
    // Test 2: AllocVar + ReadVar + WriteVar
    // -----------------------------------------------------------------------

    #[test]
    fn test_alloc_read_write_var() {
        // Script:
        //   var = AllocVar(10)  → yields Var(id)
        //   val = ReadVar(id)   → yields 10
        //   WriteVar(id, 20)    → yields 20
        //   val = ReadVar(id)   → yields 20
        //   return val

        // We need a smarter stream that captures the var id from the first step.
        // Let's use a stream that records received values and yields based on state.
        #[derive(Debug)]
        struct VarTestStream {
            state: u8,
            var_id: Option<crate::ids::VarId>,
        }

        impl IRStream for VarTestStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                match self.state {
                    0 => {
                        // Initial resume — alloc var
                        self.state = 1;
                        StreamStep::Instruction(DoCtrl::AllocVar { initial: Value::Int(10) })
                    }
                    1 => {
                        // Received Var(id) — read it
                        if let Value::Var(var_id) = value {
                            self.var_id = Some(var_id);
                            self.state = 2;
                            StreamStep::Instruction(DoCtrl::ReadVar { var: var_id })
                        } else {
                            StreamStep::Error(Value::String("expected Var".into()))
                        }
                    }
                    2 => {
                        // Received Int(10) — write 20
                        assert!(matches!(value, Value::Int(10)));
                        self.state = 3;
                        StreamStep::Instruction(DoCtrl::WriteVar {
                            var: self.var_id.unwrap(),
                            value: Value::Int(20),
                        })
                    }
                    3 => {
                        // Received Int(20) from write — read again
                        self.state = 4;
                        StreamStep::Instruction(DoCtrl::ReadVar {
                            var: self.var_id.unwrap(),
                        })
                    }
                    4 => {
                        // Should be Int(20) — return it
                        StreamStep::Done(value)
                    }
                    _ => StreamStep::Error(Value::String("bad state".into())),
                }
            }

            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        let stream = VarTestStream { state: 0, var_id: None };
        let mut vm = setup_vm_with_stream(stream);

        let result = run_to_completion(&mut vm).unwrap();
        match result {
            Value::Int(20) => {} // correct
            other => panic!("expected Int(20), got {:?}", other),
        }
    }

    // -----------------------------------------------------------------------
    // Test 3: Apply with Callable
    // -----------------------------------------------------------------------

    #[test]
    fn test_apply_callable() {
        #[derive(Debug)]
        struct AddOne;

        impl Callable for AddOne {
            fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                match args.first() {
                    Some(Value::Int(n)) => Ok(Value::Int(n + 1)),
                    _ => Err(VMError::type_error("expected Int")),
                }
            }
        }

        let callable = Value::Callable(Arc::new(AddOne) as CallableRef);

        let stream = ScriptStream::new(
            vec![DoCtrl::Apply {
                f: Box::new(DoCtrl::Pure { value: callable }),
                args: vec![DoCtrl::Pure { value: Value::Int(41) }],
            }],
            Value::Unit, // won't reach — Apply result is delivered to stream
        );

        #[derive(Debug)]
        struct CaptureStream {
            inner: ScriptStream,
            captured: Option<Value>,
        }

        impl IRStream for CaptureStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                if self.captured.is_none() && matches!(value, Value::Unit) {
                    // First resume — delegate to inner
                    self.inner.resume(value)
                } else {
                    // Got result from Apply — capture and return
                    self.captured = Some(value.clone());
                    StreamStep::Done(value)
                }
            }

            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        let stream = CaptureStream {
            inner: ScriptStream::new(
                vec![DoCtrl::Apply {
                    f: Box::new(DoCtrl::Pure { value: Value::Callable(Arc::new(AddOne) as CallableRef) }),
                    args: vec![DoCtrl::Pure { value: Value::Int(41) }],
                }],
                Value::Unit,
            ),
            captured: None,
        };

        let mut vm = setup_vm_with_stream(stream);
        let result = run_to_completion(&mut vm).unwrap();
        match result {
            Value::Int(42) => {} // correct: 41 + 1 = 42
            other => panic!("expected Int(42), got {:?}", other),
        }
    }

    // -----------------------------------------------------------------------
    // Test 4: Resume continuation
    // -----------------------------------------------------------------------

    #[test]
    fn test_resume_continuation() {
        use crate::continuation::Continuation;

        // Create a fiber with a stream that returns the value it receives
        let mut vm = VM::new();

        // Create a child fiber that will be the "body" — returns whatever it gets
        let body_stream = IRStreamRef::new(Box::new(ScriptStream::returning(Value::Unit)));
        let mut body_fiber = Fiber::new(None);
        body_fiber.push_frame(Frame::program(body_stream, None));
        let body_fid = vm.alloc_segment(body_fiber);

        // Create a continuation pointing to the body fiber
        let mut k = Continuation::new(body_fid, body_fid);

        // Create a root fiber that yields Resume(k, 77)
        #[derive(Debug)]
        struct ResumeStream {
            k: Option<Continuation>,
        }

        impl IRStream for ResumeStream {
            fn resume(&mut self, _value: Value) -> StreamStep {
                match self.k.take() {
                    Some(k) => StreamStep::Instruction(DoCtrl::Resume {
                        k,
                        value: Value::Int(77),
                    }),
                    None => StreamStep::Done(Value::String("handler done".into())),
                }
            }

            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        let root_stream = IRStreamRef::new(Box::new(ResumeStream { k: Some(k) }));
        let mut root_fiber = Fiber::new(None);
        root_fiber.push_frame(Frame::program(root_stream, None));
        let root_fid = vm.alloc_segment(root_fiber);

        // Set body's parent to root (so when body completes, it returns to root)
        if let Some(body) = vm.segments.get_mut(body_fid) {
            body.parent = Some(root_fid);
        }

        vm.current_segment = Some(root_fid);
        vm.mode = Mode::Send(Value::Unit);

        let result = run_to_completion(&mut vm);
        assert!(result.is_ok(), "got error: {:?}", result.err());
    }

    // -----------------------------------------------------------------------
    // Test 5: Eval wraps inner DoCtrl
    // -----------------------------------------------------------------------

    #[test]
    fn test_eval_pure() {
        let stream = ScriptStream::new(
            vec![DoCtrl::Eval {
                expr: Box::new(DoCtrl::Pure { value: Value::Int(55) }),
            }],
            Value::Unit,
        );

        #[derive(Debug)]
        struct CaptureFirstResume {
            first: bool,
            inner: ScriptStream,
        }

        impl IRStream for CaptureFirstResume {
            fn resume(&mut self, value: Value) -> StreamStep {
                if self.first {
                    self.first = false;
                    self.inner.resume(value)
                } else {
                    // Got the result of Eval — return it
                    StreamStep::Done(value)
                }
            }

            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        let stream = CaptureFirstResume {
            first: true,
            inner: ScriptStream::new(
                vec![DoCtrl::Eval {
                    expr: Box::new(DoCtrl::Pure { value: Value::Int(55) }),
                }],
                Value::Unit,
            ),
        };

        let mut vm = setup_vm_with_stream(stream);
        let result = run_to_completion(&mut vm).unwrap();
        match result {
            Value::Int(55) => {} // correct
            other => panic!("expected Int(55), got {:?}", other),
        }
    }

    // -----------------------------------------------------------------------
    // Test 6: Raise propagates error
    // -----------------------------------------------------------------------

    #[test]
    fn test_raise_propagates_to_error() {
        #[derive(Debug)]
        struct ErrorStream;

        impl IRStream for ErrorStream {
            fn resume(&mut self, _value: Value) -> StreamStep {
                StreamStep::Error(Value::String("boom".into()))
            }

            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        let mut vm = setup_vm_with_stream(ErrorStream);
        let result = run_to_completion(&mut vm);
        assert!(result.is_err());
    }

    // -----------------------------------------------------------------------
    // Test 7: WithHandler + Perform + Resume (full effect handler cycle)
    // -----------------------------------------------------------------------

    #[test]
    fn test_with_handler_perform_resume() {
        use crate::continuation::Continuation;

        // The handler: receives (effect, k), resumes k with 100
        #[derive(Debug)]
        struct TestHandler;

        impl Callable for TestHandler {
            fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                // args = [effect, continuation]
                // Return a stream that yields Resume(k, 100)
                let k = match args.into_iter().nth(1) {
                    Some(Value::Continuation(k)) => k,
                    _ => return Err(VMError::internal("handler: expected continuation")),
                };

                #[derive(Debug)]
                struct ResumeStream {
                    k: Option<Continuation>,
                }

                impl IRStream for ResumeStream {
                    fn resume(&mut self, value: Value) -> StreamStep {
                        match self.k.take() {
                            Some(k) => StreamStep::Instruction(DoCtrl::Resume {
                                k,
                                value: Value::Int(100),
                            }),
                            None => StreamStep::Done(value), // pass through body's return value
                        }
                    }

                    fn throw(&mut self, error: Value) -> StreamStep {
                        StreamStep::Error(error)
                    }
                }

                let stream = IRStreamRef::new(Box::new(ResumeStream { k: Some(k) }));
                Ok(Value::Stream(stream))
            }
        }

        // The body: performs an effect, returns whatever it gets back
        #[derive(Debug)]
        struct BodyStream {
            state: u8,
        }

        impl IRStream for BodyStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                match self.state {
                    0 => {
                        // First resume — perform an effect
                        self.state = 1;
                        StreamStep::Instruction(DoCtrl::Perform {
                            effect: Value::String("get_value".into()),
                        })
                    }
                    1 => {
                        // Got the resume value from handler — return it
                        StreamStep::Done(value)
                    }
                    _ => StreamStep::Error(Value::String("bad state".into())),
                }
            }

            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        #[derive(Debug)]
        struct RootStream {
            yielded_with_handler: bool,
            handler: Option<Value>,
            body: Option<Box<DoCtrl>>,
        }

        impl IRStream for RootStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                if !self.yielded_with_handler {
                    self.yielded_with_handler = true;
                    StreamStep::Instruction(DoCtrl::WithHandler {
                        handler: self.handler.take().unwrap(),
                        body: self.body.take().unwrap(),
                    })
                } else {
                    StreamStep::Done(value)
                }
            }
            fn throw(&mut self, error: Value) -> StreamStep {
                StreamStep::Error(error)
            }
        }

        let root = RootStream {
            yielded_with_handler: false,
            handler: Some(Value::Callable(Arc::new(TestHandler) as CallableRef)),
            body: Some(expand_stream(BodyStream { state: 0 })),
        };

        let mut vm = setup_vm_with_stream(root);
        let result = run_to_completion(&mut vm);
        match result {
            Ok(Value::Int(100)) => {} // correct: body performed, handler resumed with 100
            Ok(other) => panic!("expected Int(100), got {:?}", other),
            Err(err) => panic!("expected Ok, got error: {:?}", err),
        }
    }

    // -----------------------------------------------------------------------
    // Test 8: Transfer (tail position — handler done after resume)
    // -----------------------------------------------------------------------

    #[test]
    fn test_with_handler_perform_transfer() {
        use crate::continuation::Continuation;

        // Handler: receives (effect, k), transfers k with 200 (tail position)
        #[derive(Debug)]
        struct TransferHandler;

        impl Callable for TransferHandler {
            fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                let k = match args.into_iter().nth(1) {
                    Some(Value::Continuation(k)) => k,
                    _ => return Err(VMError::internal("handler: expected continuation")),
                };

                #[derive(Debug)]
                struct TransferStream { k: Option<Continuation> }

                impl IRStream for TransferStream {
                    fn resume(&mut self, _value: Value) -> StreamStep {
                        match self.k.take() {
                            Some(k) => StreamStep::Instruction(DoCtrl::Transfer {
                                k,
                                value: Value::Int(200),
                            }),
                            None => unreachable!("Transfer is tail — handler shouldn't be resumed"),
                        }
                    }
                    fn throw(&mut self, error: Value) -> StreamStep {
                        StreamStep::Error(error)
                    }
                }

                Ok(Value::Stream(IRStreamRef::new(Box::new(TransferStream { k: Some(k) }))))
            }
        }

        // Body: performs, returns what it gets
        #[derive(Debug)]
        struct BodyStream { state: u8 }

        impl IRStream for BodyStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                match self.state {
                    0 => { self.state = 1; StreamStep::Instruction(DoCtrl::Perform { effect: Value::String("get".into()) }) }
                    1 => StreamStep::Done(value),
                    _ => StreamStep::Error(Value::String("bad".into())),
                }
            }
            fn throw(&mut self, error: Value) -> StreamStep { StreamStep::Error(error) }
        }

        #[derive(Debug)]
        struct Root { done: bool, handler: Option<Value>, body: Option<Box<DoCtrl>> }

        impl IRStream for Root {
            fn resume(&mut self, value: Value) -> StreamStep {
                if !self.done {
                    self.done = true;
                    StreamStep::Instruction(DoCtrl::WithHandler {
                        handler: self.handler.take().unwrap(),
                        body: self.body.take().unwrap(),
                    })
                } else {
                    StreamStep::Done(value)
                }
            }
            fn throw(&mut self, error: Value) -> StreamStep { StreamStep::Error(error) }
        }

        let mut vm = setup_vm_with_stream(Root {
            done: false,
            handler: Some(Value::Callable(Arc::new(TransferHandler) as CallableRef)),
            body: Some(expand_stream(BodyStream { state: 0 })),
        });

        let result = run_to_completion(&mut vm);
        match result {
            Ok(Value::Int(200)) => {}
            Ok(other) => panic!("expected Int(200), got {:?}", other),
            Err(err) => panic!("expected Ok, got error: {:?}", err),
        }
    }

    // -----------------------------------------------------------------------
    // Test 9: Multiple performs — handler handles two effects
    // -----------------------------------------------------------------------

    #[test]
    fn test_handler_handles_multiple_performs() {
        use crate::continuation::Continuation;

        // Handler: resumes with incrementing values (10, 20)
        #[derive(Debug)]
        struct CountHandler;

        impl Callable for CountHandler {
            fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                let k = match args.into_iter().nth(1) {
                    Some(Value::Continuation(k)) => k,
                    _ => return Err(VMError::internal("expected k")),
                };

                static COUNTER: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(10);

                let val = COUNTER.fetch_add(10, std::sync::atomic::Ordering::Relaxed);

                #[derive(Debug)]
                struct S { k: Option<Continuation>, val: i64 }

                impl IRStream for S {
                    fn resume(&mut self, value: Value) -> StreamStep {
                        match self.k.take() {
                            Some(k) => StreamStep::Instruction(DoCtrl::Resume { k, value: Value::Int(self.val) }),
                            None => StreamStep::Done(value),
                        }
                    }
                    fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
                }

                Ok(Value::Stream(IRStreamRef::new(Box::new(S { k: Some(k), val }))))
            }
        }

        // Body: performs twice, adds the results
        #[derive(Debug)]
        struct BodyStream { state: u8, first: i64 }

        impl IRStream for BodyStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                match self.state {
                    0 => { self.state = 1; StreamStep::Instruction(DoCtrl::Perform { effect: Value::String("a".into()) }) }
                    1 => {
                        if let Value::Int(v) = value { self.first = v; }
                        self.state = 2;
                        StreamStep::Instruction(DoCtrl::Perform { effect: Value::String("b".into()) })
                    }
                    2 => {
                        if let Value::Int(v) = value {
                            StreamStep::Done(Value::Int(self.first + v))
                        } else {
                            StreamStep::Error(Value::String("expected int".into()))
                        }
                    }
                    _ => StreamStep::Error(Value::String("bad".into())),
                }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        #[derive(Debug)]
        struct Root { done: bool, handler: Option<Value>, body: Option<Box<DoCtrl>> }

        impl IRStream for Root {
            fn resume(&mut self, value: Value) -> StreamStep {
                if !self.done {
                    self.done = true;
                    StreamStep::Instruction(DoCtrl::WithHandler {
                        handler: self.handler.take().unwrap(),
                        body: self.body.take().unwrap(),
                    })
                } else {
                    StreamStep::Done(value)
                }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        // Reset counter for this test
        let mut vm = setup_vm_with_stream(Root {
            done: false,
            handler: Some(Value::Callable(Arc::new(CountHandler) as CallableRef)),
            body: Some(expand_stream(BodyStream { state: 0, first: 0 })),
        });

        let result = run_to_completion(&mut vm);
        match result {
            Ok(Value::Int(sum)) => {
                // Counter starts at 10, increments by 10 each call
                // First perform: 10, second: 20, sum = 30
                assert_eq!(sum, 30, "expected 10 + 20 = 30, got {}", sum);
            }
            Ok(other) => panic!("expected Int, got {:?}", other),
            Err(err) => panic!("expected Ok, got error: {:?}", err),
        }
    }

    // -----------------------------------------------------------------------
    // Test 10: Nested handlers — inner handles, outer untouched
    // -----------------------------------------------------------------------

    #[test]
    fn test_nested_handlers_inner_handles() {
        use crate::continuation::Continuation;

        // Both handlers resume with different values
        fn make_handler(reply: i64) -> Value {
            #[derive(Debug)]
            struct H { reply: i64 }

            impl Callable for H {
                fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                    let k = match args.into_iter().nth(1) {
                        Some(Value::Continuation(k)) => k,
                        _ => return Err(VMError::internal("expected k")),
                    };
                    let reply = self.reply;

                    #[derive(Debug)]
                    struct S { k: Option<Continuation>, reply: i64 }

                    impl IRStream for S {
                        fn resume(&mut self, value: Value) -> StreamStep {
                            match self.k.take() {
                                Some(k) => StreamStep::Instruction(DoCtrl::Resume { k, value: Value::Int(self.reply) }),
                                None => StreamStep::Done(value),
                            }
                        }
                        fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
                    }

                    Ok(Value::Stream(IRStreamRef::new(Box::new(S { k: Some(k), reply }))))
                }
            }

            Value::Callable(Arc::new(H { reply }) as CallableRef)
        }

        // Body: performs once, returns the value
        #[derive(Debug)]
        struct BodyStream { state: u8 }

        impl IRStream for BodyStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                match self.state {
                    0 => { self.state = 1; StreamStep::Instruction(DoCtrl::Perform { effect: Value::String("ask".into()) }) }
                    1 => StreamStep::Done(value),
                    _ => StreamStep::Error(Value::String("bad".into())),
                }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        // Structure:
        //   WithHandler(outer=999,
        //     WithHandler(inner=42, body))
        // Body performs → inner handles → returns 42

        #[derive(Debug)]
        struct InnerRoot { done: bool, inner_handler: Option<Value>, body: Option<Box<DoCtrl>> }

        impl IRStream for InnerRoot {
            fn resume(&mut self, value: Value) -> StreamStep {
                if !self.done {
                    self.done = true;
                    StreamStep::Instruction(DoCtrl::WithHandler {
                        handler: self.inner_handler.take().unwrap(),
                        body: self.body.take().unwrap(),
                    })
                } else {
                    StreamStep::Done(value)
                }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        #[derive(Debug)]
        struct OuterRoot { done: bool, outer_handler: Option<Value>, inner: Option<Box<DoCtrl>> }

        impl IRStream for OuterRoot {
            fn resume(&mut self, value: Value) -> StreamStep {
                if !self.done {
                    self.done = true;
                    StreamStep::Instruction(DoCtrl::WithHandler {
                        handler: self.outer_handler.take().unwrap(),
                        body: self.inner.take().unwrap(),
                    })
                } else {
                    StreamStep::Done(value)
                }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        let inner_stream = IRStreamRef::new(Box::new(InnerRoot {
            done: false,
            inner_handler: Some(make_handler(42)),
            body: Some(expand_stream(BodyStream { state: 0 })),
        }));

        let mut vm = setup_vm_with_stream(OuterRoot {
            done: false,
            outer_handler: Some(make_handler(999)),
            inner: Some(Box::new(DoCtrl::Expand { expr: Box::new(DoCtrl::Pure { value: Value::Stream(inner_stream) }) })),
        });

        let result = run_to_completion(&mut vm);
        match result {
            Ok(Value::Int(42)) => {} // inner handler handled it, not outer
            Ok(other) => panic!("expected Int(42), got {:?}", other),
            Err(err) => panic!("expected Ok, got error: {:?}", err),
        }
    }

    // -----------------------------------------------------------------------
    // Test 11: Pass — inner handler passes to outer handler
    // -----------------------------------------------------------------------

    #[test]
    fn test_pass_to_outer_handler() {
        use crate::continuation::Continuation;

        // Handler that passes everything
        #[derive(Debug)]
        struct PassHandler;

        impl Callable for PassHandler {
            fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                let effect = args.first().cloned().unwrap_or(Value::Unit);
                let k = match args.into_iter().nth(1) {
                    Some(Value::Continuation(k)) => k,
                    _ => return Err(VMError::internal("expected k")),
                };

                #[derive(Debug)]
                struct S { effect: Option<Value>, k: Option<Continuation> }

                impl IRStream for S {
                    fn resume(&mut self, _value: Value) -> StreamStep {
                        let effect = self.effect.take().unwrap();
                        let k = self.k.take().unwrap();
                        StreamStep::Instruction(DoCtrl::Pass { effect, k })
                    }
                    fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
                }

                Ok(Value::Stream(IRStreamRef::new(Box::new(S {
                    effect: Some(effect),
                    k: Some(k),
                }))))
            }
        }

        // Handler that resumes with a value
        fn make_resume_handler(reply: i64) -> Value {
            #[derive(Debug)]
            struct H { reply: i64 }

            impl Callable for H {
                fn call(&self, args: Vec<Value>) -> Result<Value, VMError> {
                    let k = match args.into_iter().nth(1) {
                        Some(Value::Continuation(k)) => k,
                        _ => return Err(VMError::internal("expected k")),
                    };
                    let reply = self.reply;

                    #[derive(Debug)]
                    struct S { k: Option<Continuation>, reply: i64 }

                    impl IRStream for S {
                        fn resume(&mut self, value: Value) -> StreamStep {
                            match self.k.take() {
                                Some(k) => StreamStep::Instruction(DoCtrl::Resume { k, value: Value::Int(self.reply) }),
                                None => StreamStep::Done(value),
                            }
                        }
                        fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
                    }

                    Ok(Value::Stream(IRStreamRef::new(Box::new(S { k: Some(k), reply }))))
                }
            }

            Value::Callable(Arc::new(H { reply }) as CallableRef)
        }

        // Body: performs once, returns the value
        #[derive(Debug)]
        struct BodyStream { state: u8 }

        impl IRStream for BodyStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                match self.state {
                    0 => { self.state = 1; StreamStep::Instruction(DoCtrl::Perform { effect: Value::String("ask".into()) }) }
                    1 => StreamStep::Done(value),
                    _ => StreamStep::Error(Value::String("bad".into())),
                }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        // WithHandler(outer=777, WithHandler(inner=Pass, body))
        // Body performs → inner passes → outer handles → 777

        #[derive(Debug)]
        struct WrapStream { done: bool, h: Option<Value>, b: Option<Box<DoCtrl>> }

        impl IRStream for WrapStream {
            fn resume(&mut self, value: Value) -> StreamStep {
                if !self.done {
                    self.done = true;
                    StreamStep::Instruction(DoCtrl::WithHandler {
                        handler: self.h.take().unwrap(),
                        body: self.b.take().unwrap(),
                    })
                } else { StreamStep::Done(value) }
            }
            fn throw(&mut self, e: Value) -> StreamStep { StreamStep::Error(e) }
        }

        let inner_stream = IRStreamRef::new(Box::new(WrapStream {
            done: false,
            h: Some(Value::Callable(Arc::new(PassHandler) as CallableRef)),
            b: Some(expand_stream(BodyStream { state: 0 })),
        }));

        let mut vm = setup_vm_with_stream(WrapStream {
            done: false,
            h: Some(make_resume_handler(777)),
            b: Some(Box::new(DoCtrl::Expand { expr: Box::new(DoCtrl::Pure { value: Value::Stream(inner_stream) }) })),
        });

        let result = run_to_completion(&mut vm);
        match result {
            Ok(Value::Int(777)) => {} // inner passed, outer handled with 777
            Ok(other) => panic!("expected Int(777), got {:?}", other),
            Err(err) => panic!("expected Ok, got error: {:?}", err),
        }
    }
}
