from doeff import Pure

print("first run:", value)
print("again:", interpreter(program, env=Pure({"tax_rate": 0.25, "currency": "USD"})))
try:
    interpreter(program)
except Exception as e:
    print("no env:", type(e).__name__, e)
