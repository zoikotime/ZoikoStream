"""Regression coverage for the shared test helper (_testsupport.code_only).

The bug being pinned: stripping a docstring from a body whose ONLY statement is that
docstring left an empty body, and ast.unparse then emitted `class Foo:` — not parseable. Any
assertion that re-parses code_only()'s output would fail confusingly.

Two properties must hold for every input:
  1. the docstring text is GONE (otherwise "token must not appear" assertions are defeated)
  2. the output is valid Python and re-parses (otherwise downstream ast.parse blows up)
"""
import ast

import pytest

from _testsupport import code_only, code_only_source


def assert_stripped_and_valid(source: str, *, prose: list[str], kept: list[str] = ()):
    out = code_only_source(source)
    ast.parse(out)                                  # property 2: re-parses
    for text in prose:
        assert text not in out, f"docstring prose survived: {text!r}"
    for text in kept:
        assert text in out, f"real code was removed: {text!r}"
    return out


# ── 1. class with only a docstring ────────────────────────────────────────────────────────

def test_class_with_only_a_docstring():
    out = assert_stripped_and_valid(
        'class Foo:\n    """SECRETPROSE"""\n',
        prose=["SECRETPROSE"], kept=["class Foo"],
    )
    assert "pass" in out          # body replaced, not emptied


# ── 2. function with only a docstring ─────────────────────────────────────────────────────

def test_function_with_only_a_docstring():
    out = assert_stripped_and_valid(
        'def bar():\n    """SECRETPROSE"""\n',
        prose=["SECRETPROSE"], kept=["def bar"],
    )
    assert "pass" in out


# ── 3. async function with only a docstring ───────────────────────────────────────────────

def test_async_function_with_only_a_docstring():
    out = assert_stripped_and_valid(
        'async def baz():\n    """SECRETPROSE"""\n',
        prose=["SECRETPROSE"], kept=["async def baz"],
    )
    assert "pass" in out


# ── 4. nested class/function with only a docstring ────────────────────────────────────────

def test_nested_docstring_only_bodies():
    source = (
        'class Outer:\n'
        '    """OUTERPROSE"""\n'
        '\n'
        '    class Inner:\n'
        '        """INNERPROSE"""\n'
        '\n'
        '    def method(self):\n'
        '        """METHODPROSE"""\n'
    )
    assert_stripped_and_valid(
        source, prose=["OUTERPROSE", "INNERPROSE", "METHODPROSE"],
        kept=["class Outer", "class Inner", "def method"],
    )


def test_deeply_nested_function_in_function():
    source = (
        'def outer():\n'
        '    """OUTERPROSE"""\n'
        '    def inner():\n'
        '        """INNERPROSE"""\n'
        '    return inner\n'
    )
    assert_stripped_and_valid(
        source, prose=["OUTERPROSE", "INNERPROSE"], kept=["def outer", "def inner", "return inner"],
    )


# ── 5. normal bodies with executable statements ───────────────────────────────────────────

def test_docstring_removed_but_real_statements_kept():
    source = (
        'def calc(a, b):\n'
        '    """SECRETPROSE"""\n'
        '    total = a + b\n'
        '    return total\n'
    )
    out = assert_stripped_and_valid(
        source, prose=["SECRETPROSE"], kept=["total = a + b", "return total"],
    )
    assert "pass" not in out          # nothing to substitute — body was non-empty


def test_class_with_docstring_and_members():
    source = (
        'class Thing:\n'
        '    """SECRETPROSE"""\n'
        '    LIMIT = 5\n'
    )
    out = assert_stripped_and_valid(source, prose=["SECRETPROSE"], kept=["LIMIT = 5"])
    assert "pass" not in out


def test_a_string_that_is_not_a_docstring_is_preserved():
    """Only the FIRST statement counts as a docstring — a bare string elsewhere is code."""
    source = 'def f():\n    x = 1\n    "NOTADOCSTRING"\n'
    out = code_only_source(source)
    ast.parse(out)
    assert "NOTADOCSTRING" in out


# ── 6. module-level docstring ─────────────────────────────────────────────────────────────

def test_module_docstring_is_removed():
    out = assert_stripped_and_valid(
        '"""MODULEPROSE"""\nVALUE = 1\n', prose=["MODULEPROSE"], kept=["VALUE = 1"],
    )
    assert "pass" not in out


def test_module_containing_only_a_docstring_stays_valid():
    """A module may legally be empty, so no `pass` is inserted at module level."""
    out = code_only_source('"""MODULEPROSE"""\n')
    ast.parse(out)
    assert "MODULEPROSE" not in out
    assert out.strip() == ""


# ── comments, and the real modules the suites actually scan ───────────────────────────────

def test_comments_are_removed_too():
    out = code_only_source("# SECRETCOMMENT\nVALUE = 1\n")
    assert "SECRETCOMMENT" not in out and "VALUE = 1" in out


@pytest.mark.parametrize("module_name", [
    "app.crud.commercial",
    "app.models.commercial",
    "app.routers.commercial",
    "app.services.payments",
    "app.services.payments_stripe",
    "app.services.payments_stripe_events",
])
def test_real_modules_survive_the_round_trip(module_name):
    """The concrete failure mode: payments_stripe_events has a docstring-only exception class,
    which used to make its code_only() output un-reparseable."""
    import importlib
    module = importlib.import_module(module_name)
    out = code_only(module)
    ast.parse(out)                     # must not raise
    assert out.strip(), "helper produced empty output for a real module"


def test_code_only_accepts_functions_and_classes_too():
    from app.services import payments as pay
    for obj in (pay.get_provider, pay.to_minor_units, pay.MockPaymentProvider):
        ast.parse(code_only(obj))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
