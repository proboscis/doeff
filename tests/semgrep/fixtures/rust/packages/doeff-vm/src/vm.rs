pub struct VM;

impl VM {
    fn current_segment_mut(&mut self) -> Option<u8> {
        None
    }

    fn maybe_skip_bad(&mut self) {
        if let Some(segment) = self.current_segment_mut() {
            let _ = segment;
        }
    }
}
