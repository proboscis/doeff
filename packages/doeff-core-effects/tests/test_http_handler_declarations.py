"""Failure cases for the declarations of the installer that http-production-handler creates on each call.

http-production-handler creates a client per scope, so it returns a function (the installer) whose clauses cannot be read statically. The effects it answers and emits are
declared with __doeff_handles__ / __doeff_effects__, but the declarations existed only on the function itself and were not carried over to the installer.
As a result, putting the installer into a stacked_handlers bundle (which attaches the union of the inner handlers' declarations) left the bundle an "unreadable handler".
"""

from doeff import stacked_handlers
from doeff_core_effects import HttpRequest, ResolveHost
from doeff_core_effects import http_production_handler


def test_the_installer_declares_what_the_function_declares() -> None:
    installer = http_production_handler()
    assert installer.__doeff_handles__ == http_production_handler.__doeff_handles__ == (HttpRequest, ResolveHost)
    assert installer.__doeff_effects__ == http_production_handler.__doeff_effects__


def test_a_bundle_with_the_installer_declares_the_request_effect() -> None:
    bundle = stacked_handlers(http_production_handler())
    assert HttpRequest in bundle.__doeff_handles__


def test_a_bundle_with_the_installer_declares_the_lookup_effect() -> None:
    bundle = stacked_handlers(http_production_handler())
    assert ResolveHost in bundle.__doeff_handles__
