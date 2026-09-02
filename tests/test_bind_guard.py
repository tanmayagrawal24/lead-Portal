"""`portal serve`'s bind guard (external audit finding 6, Unit 2a).

The audit's point was precise and correct: `--host`'s help text warned that
binding elsewhere "publishes an unauthenticated database", and **nothing
enforced it**. A warning in `--help` is read at the moment you read `--help`,
not at the moment you type `--host 0.0.0.0`.

So the property under test is not "the flag exists" — it is that the default
path is untouched, every spelling of loopback still works with no flag, and
every other address is refused with a non-zero exit **before** the server
starts. `serve_mod.serve` is patched throughout: a test that actually bound a
socket would be testing the operating system.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from portal import cli, db, migrate


class BindClassification(unittest.TestCase):
    """The predicate, isolated from the command."""

    def test_every_loopback_spelling_is_loopback(self) -> None:
        for host in (
            "127.0.0.1",
            "127.0.0.53",  # the whole 127/8 block is loopback, not just .1
            "::1",
            "[::1]",
            "localhost",
            "LocalHost",
            " 127.0.0.1 ",
        ):
            self.assertTrue(cli.is_loopback_bind(host), host)

    def test_wildcards_are_public_not_unspecified(self) -> None:
        """The failure mode worth naming: a wildcard binds *every* interface, so
        it is the most exposed address there is. Reading "unspecified" as
        "probably fine" is how this mistake is usually made."""
        for host in ("0.0.0.0", "::", "", "   "):
            self.assertFalse(cli.is_loopback_bind(host), repr(host))

    def test_routable_addresses_are_public(self) -> None:
        for host in ("192.168.1.10", "10.0.0.5", "203.0.113.7", "2001:db8::1"):
            self.assertFalse(cli.is_loopback_bind(host), host)

    def test_an_unresolvable_name_is_refused_rather_than_resolved(self) -> None:
        """A name lookup would make the guard depend on what the machine's
        resolver currently believes, which is the one thing a security check
        must not do. An unknown name is not evidence of a loopback interface."""
        self.assertFalse(cli.is_loopback_bind("portal.internal"))


class ServeCommand(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "portal.db"
        conn = db.connect(self.path)
        try:
            migrate.apply_pending(conn)
        finally:
            conn.close()

    def run_serve(self, *extra: str) -> tuple[int, str, mock.Mock]:
        served = mock.Mock(return_value=0)
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli.serve_mod, "serve", served),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["--db", str(self.path), "serve", *extra])
        return code, out.getvalue() + err.getvalue(), served

    def test_the_default_is_unchanged_and_needs_no_flag(self) -> None:
        code, text, served = self.run_serve()
        self.assertEqual(code, 0)
        served.assert_called_once()
        self.assertEqual(served.call_args.args[0], "127.0.0.1")
        self.assertNotIn("refusing", text)

    def test_an_explicit_loopback_host_needs_no_flag(self) -> None:
        code, _text, served = self.run_serve("--host", "localhost")
        self.assertEqual(code, 0)
        served.assert_called_once()

    def test_a_non_loopback_bind_is_refused_before_the_server_starts(self) -> None:
        code, text, served = self.run_serve("--host", "0.0.0.0")
        self.assertEqual(code, 2)
        served.assert_not_called()
        self.assertIn("refusing to bind", text)
        # The message has to name the way out, or it is an obstacle rather than
        # a guard — and it has to say what the cost is, or the flag is cargo.
        self.assertIn("--allow-public-bind", text)
        self.assertIn("no authentication", text)

    def test_the_flag_permits_it_and_still_says_so(self) -> None:
        """…provided credentials are set. Off loopback, `PORTAL_BASIC_AUTH` is
        required, not advisory (audit finding 5)."""
        with mock.patch.dict(os.environ, {"PORTAL_BASIC_AUTH": "op:pw"}):
            code, text, served = self.run_serve(
                "--host", "0.0.0.0", "--allow-public-bind"
            )
        self.assertEqual(code, 0)
        served.assert_called_once()
        self.assertEqual(served.call_args.args[0], "0.0.0.0")
        self.assertIn("WARNING", text)

    def test_the_flag_alone_is_not_enough_without_credentials(self) -> None:
        with mock.patch.dict(os.environ, {"PORTAL_BASIC_AUTH": ""}):
            code, text, served = self.run_serve(
                "--host", "0.0.0.0", "--allow-public-bind"
            )
        self.assertEqual(code, 2)
        served.assert_not_called()
        self.assertIn("PORTAL_BASIC_AUTH", text)

    def test_the_flag_is_not_needed_and_not_noisy_on_loopback(self) -> None:
        _code, text, _served = self.run_serve("--host", "127.0.0.1")
        self.assertNotIn("WARNING", text)

    def test_a_missing_database_still_fails_first(self) -> None:
        """Ordering matters: the pre-existing check must not be shadowed."""
        served = mock.Mock(return_value=0)
        out, err = io.StringIO(), io.StringIO()
        missing = Path(self._tmp.name) / "absent.db"
        with (
            mock.patch.object(cli.serve_mod, "serve", served),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["--db", str(missing), "serve", "--host", "0.0.0.0"])
        self.assertEqual(code, 2)
        self.assertIn("no database", out.getvalue() + err.getvalue())
        served.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
