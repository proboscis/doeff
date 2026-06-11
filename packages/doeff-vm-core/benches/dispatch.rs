use std::any::Any;
use std::sync::Arc;

use criterion::{black_box, criterion_group, criterion_main, BatchSize, Criterion};
use doeff_vm_core::{
    Callable, CallableRef, DoCtrl, Fiber, Frame, IRStream, IRStreamRef, Signal, StepResult,
    VMError, Value, VM,
};

const DISPATCH_CYCLES: usize = 100;
const STEP_CYCLES: usize = 1_000;
const FIBER_CYCLES: usize = 1_000;

#[derive(Debug)]
struct PerformLoopStream {
    cycles: usize,
    completed: usize,
    started: bool,
}

impl PerformLoopStream {
    fn new(cycles: usize) -> Self {
        Self {
            cycles,
            completed: 0,
            started: false,
        }
    }
}

impl IRStream for PerformLoopStream {
    fn resume(&mut self, value: Value) -> doeff_vm_core::ir_stream::StreamStep {
        if !self.started {
            self.started = true;
        } else {
            black_box(value);
            self.completed += 1;
        }

        if self.completed >= self.cycles {
            return doeff_vm_core::ir_stream::StreamStep::Done(Value::Int(self.completed as i64));
        }

        doeff_vm_core::ir_stream::StreamStep::Instruction(DoCtrl::Perform {
            effect: Value::Int(self.completed as i64),
        })
    }

    fn throw(&mut self, error: Value) -> doeff_vm_core::ir_stream::StreamStep {
        doeff_vm_core::ir_stream::StreamStep::Error(error)
    }
}

#[derive(Debug)]
struct StepLoopStream {
    remaining: usize,
}

impl StepLoopStream {
    fn new(remaining: usize) -> Self {
        Self { remaining }
    }
}

impl IRStream for StepLoopStream {
    fn resume(&mut self, value: Value) -> doeff_vm_core::ir_stream::StreamStep {
        black_box(value);
        if self.remaining == 0 {
            return doeff_vm_core::ir_stream::StreamStep::Done(Value::Unit);
        }
        self.remaining -= 1;
        doeff_vm_core::ir_stream::StreamStep::Instruction(DoCtrl::Pure { value: Value::Unit })
    }

    fn throw(&mut self, error: Value) -> doeff_vm_core::ir_stream::StreamStep {
        doeff_vm_core::ir_stream::StreamStep::Error(error)
    }
}

#[derive(Debug, Clone, Copy)]
enum ResumeMode {
    Resume,
    Transfer,
}

#[derive(Debug)]
struct ContinueHandler {
    mode: ResumeMode,
}

impl ContinueHandler {
    fn new(mode: ResumeMode) -> Self {
        Self { mode }
    }
}

impl Callable for ContinueHandler {
    fn call(&self, _args: Vec<Value>) -> Result<Value, VMError> {
        Err(VMError::internal(
            "ContinueHandler must be used as a handler",
        ))
    }

    fn call_handler(&self, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let mut args_iter = args.into_iter();
        let _effect = args_iter
            .next()
            .ok_or_else(|| VMError::internal("handler missing effect"))?;
        let k = match args_iter.next() {
            Some(Value::Continuation(k)) => k,
            _ => return Err(VMError::internal("handler missing continuation")),
        };

        match self.mode {
            ResumeMode::Resume => Ok(DoCtrl::Resume {
                k,
                value: Value::Int(1),
            }),
            ResumeMode::Transfer => Ok(DoCtrl::Transfer {
                k,
                value: Value::Int(1),
            }),
        }
    }

    fn name(&self) -> Option<String> {
        Some(
            match self.mode {
                ResumeMode::Resume => "bench_resume_handler",
                ResumeMode::Transfer => "bench_transfer_handler",
            }
            .to_string(),
        )
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

#[derive(Debug)]
struct PassHandler;

impl Callable for PassHandler {
    fn call(&self, _args: Vec<Value>) -> Result<Value, VMError> {
        Err(VMError::internal("PassHandler must be used as a handler"))
    }

    fn call_handler(&self, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let mut args_iter = args.into_iter();
        let effect = args_iter
            .next()
            .ok_or_else(|| VMError::internal("pass handler missing effect"))?;
        let k = match args_iter.next() {
            Some(Value::Continuation(k)) => k,
            _ => return Err(VMError::internal("pass handler missing continuation")),
        };
        Ok(DoCtrl::Pass { effect, k })
    }

    fn name(&self) -> Option<String> {
        Some("bench_pass_handler".to_string())
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

fn callable_value(callable: impl Callable) -> Value {
    Value::Callable(Arc::new(callable) as CallableRef)
}

fn expand_stream(stream: impl IRStream + 'static) -> DoCtrl {
    DoCtrl::Expand {
        expr: Box::new(DoCtrl::Pure {
            value: Value::Stream(IRStreamRef::new(Box::new(stream))),
        }),
    }
}

fn handler_program(cycles: usize, depth: usize, mode: ResumeMode) -> DoCtrl {
    assert!(depth >= 1, "handler depth must be at least 1");

    let mut program = expand_stream(PerformLoopStream::new(cycles));
    for _ in 1..depth {
        program = DoCtrl::WithHandler {
            handler: callable_value(PassHandler),
            body: Box::new(program),
        };
    }

    DoCtrl::WithHandler {
        handler: callable_value(ContinueHandler::new(mode)),
        body: Box::new(program),
    }
}

fn run_doctrl_to_completion(doctrl: DoCtrl) -> Value {
    let mut vm = VM::new();
    let root_fid = vm.alloc_segment(Fiber::new(None));
    vm.current_segment = Some(root_fid);
    run_loop(&mut vm, Signal::eval(doctrl))
}

fn run_stream_to_completion(stream: impl IRStream + 'static) -> Value {
    let mut vm = VM::new();
    let stream_ref = IRStreamRef::new(Box::new(stream));
    let mut root = Fiber::new(None);
    root.push_frame(Frame::program(stream_ref, None));
    let root_fid = vm.alloc_segment(root);
    vm.current_segment = Some(root_fid);
    run_loop(&mut vm, Signal::send(Value::Unit))
}

fn run_loop(vm: &mut VM, mut signal: Signal) -> Value {
    loop {
        match vm.step(signal) {
            StepResult::Continue(next_signal) => {
                signal = next_signal;
            }
            StepResult::Done(value) => return value,
            StepResult::Error { error, .. } => panic!("VM benchmark errored: {error}"),
            StepResult::External { .. } => {
                panic!("VM benchmark unexpectedly requested external work")
            }
        }
    }
}

fn run_fiber_lifecycle(cycles: usize) -> usize {
    let mut vm = VM::new();
    let root_fid = vm.alloc_segment(Fiber::new(None));

    for _ in 0..cycles {
        let child_fid = vm.alloc_segment(Fiber::new(Some(root_fid)));
        vm.current_segment = Some(child_fid);
        match vm.fiber_return(Value::Unit) {
            StepResult::Continue(signal) => {
                black_box(signal);
            }
            other => panic!("fiber_return returned unexpected result: {other:?}"),
        }
    }

    vm.segments.len()
}

fn bench_dispatch(c: &mut Criterion) {
    let mut group = c.benchmark_group("dispatch");

    group.bench_function("perform_resume_depth_1", |b| {
        b.iter_batched(
            || handler_program(DISPATCH_CYCLES, 1, ResumeMode::Resume),
            |program| black_box(run_doctrl_to_completion(program)),
            BatchSize::SmallInput,
        );
    });

    group.bench_function("perform_resume_depth_8_reperform", |b| {
        b.iter_batched(
            || handler_program(DISPATCH_CYCLES, 8, ResumeMode::Resume),
            |program| black_box(run_doctrl_to_completion(program)),
            BatchSize::SmallInput,
        );
    });

    group.bench_function("continuation_resume_non_tail", |b| {
        b.iter_batched(
            || handler_program(DISPATCH_CYCLES, 1, ResumeMode::Resume),
            |program| black_box(run_doctrl_to_completion(program)),
            BatchSize::SmallInput,
        );
    });

    group.bench_function("continuation_transfer_tail", |b| {
        b.iter_batched(
            || handler_program(DISPATCH_CYCLES, 1, ResumeMode::Transfer),
            |program| black_box(run_doctrl_to_completion(program)),
            BatchSize::SmallInput,
        );
    });

    group.bench_function("fiber_create_return", |b| {
        b.iter(|| black_box(run_fiber_lifecycle(FIBER_CYCLES)));
    });

    group.bench_function("raw_step_trivial_loop", |b| {
        b.iter_batched(
            || StepLoopStream::new(STEP_CYCLES),
            |stream| black_box(run_stream_to_completion(stream)),
            BatchSize::SmallInput,
        );
    });

    group.finish();
}

criterion_group!(benches, bench_dispatch);
criterion_main!(benches);
