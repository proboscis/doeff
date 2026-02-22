pub fn build_traceback_data_bad() -> Option<()> {
    let serialized: Result<(), ()> = Err(());
    serialized.ok()?;
    Some(())
}
