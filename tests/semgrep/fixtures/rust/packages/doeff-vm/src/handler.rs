pub fn can_handle_bad() -> Option<()> {
    let conversion: Result<u8, u8> = Ok(1);
    conversion.ok()?;
    Some(())
}
