"""tests/fixtures は解析の見本(Rust の検 effect_tracking.rs が写して解析し、tests/python の test_fixture_package_* が import する
package doeff_test_target)で、検ではない — pytest が集めない(見本の effects/test_effects.py は module の名で、検の file ではない)。"""

collect_ignore = ["fixtures"]
