"""Shared helpers for the test suites.

Deliberately NOT named test_*.py so pytest does not collect it as a test module. Lives at
the server root alongside the test files, which is already on sys.path when pytest runs from
there — the same way the suites import `app`.

Existed as four copy-pasted implementations of code_only() across test_commercial_foundation,
test_payment_path, test_stripe_adapter and test_stripe_webhooks. Four copies of a helper that
has to behave identically cannot be fixed independently without drifting, so it lives here.
"""

from __future__ import annotations

import ast
import inspect
import textwrap


def _strip_docstrings(node: ast.AST) -> None:
    """Remove docstrings in place, keeping every body syntactically valid.

    The bug this fixes: popping the docstring from a body whose ONLY statement is that
    docstring leaves an empty body, and `ast.unparse` then emits

        class Foo:

    which is not parseable. Substituting `ast.Pass()` removes the prose — which is the entire
    point of the helper — while keeping the output valid. Skipping the transformation instead
    would leave the docstring text visible to "this token must not appear" assertions and
    quietly defeat them.
    """
    for child in ast.walk(node):
        if not isinstance(child, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(child, "body", None)
        if not body:
            continue
        first = body[0]
        is_docstring = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if not is_docstring:
            continue
        if len(body) == 1:
            # A module may legally be empty; a class/function body may not.
            if isinstance(child, ast.Module):
                body.clear()
            else:
                body[0] = ast.Pass()
        else:
            body.pop(0)


def code_only(obj) -> str:
    """Source of `obj` (module, class or function) with comments and docstrings removed.

    Used by assertions of the form "this token must not appear in the code", which are
    otherwise satisfied or defeated by explanatory prose that legitimately names the very
    thing being prohibited — e.g. a comment recording that `DEFAULT_CAPACITY_ENVELOPE = 500`
    was removed would fail a naive grep for it.

    `ast.unparse` drops comments for free; docstrings need removing explicitly. The result is
    always valid Python and can be re-parsed with ast.parse().
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    _strip_docstrings(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def code_only_source(source: str) -> str:
    """Same transformation over a source STRING rather than a live object — for testing the
    helper itself, and for scanning files that are not importable."""
    tree = ast.parse(textwrap.dedent(source))
    _strip_docstrings(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)
