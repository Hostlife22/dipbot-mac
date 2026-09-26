"""Dependency boundaries: domain isolation and self-contained distribution."""

import ast
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "dipbot"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not node.level, f"Use explicit package imports: {path}"
            if node.module:
                yield node.module


def test_domain_has_no_io_or_framework_dependencies():
    for path in (ROOT / "domain").glob("*.py"):
        for module in imports(path):
            assert module.startswith("dipbot.domain") or module.split(".")[0] in sys.stdlib_module_names, (
                path,
                module,
            )


def test_runtime_does_not_depend_on_repository_tools_or_tests():
    for path in ROOT.rglob("*.py"):
        for module in imports(path):
            assert module.split(".")[0] not in {"tools", "tests"}, (path, module)


def test_backend_does_not_import_ui_or_application_or_checks():
    for package in ("domain", "market", "execution", "persistence", "research", "observability"):
        for path in (ROOT / package).glob("*.py"):
            for module in imports(path):
                assert not module.startswith(("dipbot.ui", "dipbot.application", "dipbot.checks")), (
                    path,
                    module,
                )


def test_domain_import_does_not_initialize_qt_or_rpc():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib, pkgutil, sys
import dipbot.domain
for item in pkgutil.iter_modules(dipbot.domain.__path__):
    importlib.import_module('dipbot.domain.' + item.name)
assert not any(name in sys.modules for name in ('PySide6', 'web3', 'requests', 'keyring'))
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_packaged_resources_and_paper_entrypoint_are_available():
    from dipbot.checks.token_ui_paper import run
    from dipbot.ui.theme import STYLE

    assert callable(run)
    for name in ("chevron-down.svg", "checkmark.svg"):
        resource = files("dipbot").joinpath("assets", name)
        assert resource.is_file()
        assert str(resource) in STYLE
    assert files("dipbot").joinpath("profiles.json").is_file()


def test_test_modules_only_share_support_helpers():
    for path in (ROOT.parent / "tests").rglob("*.py"):
        for module in imports(path):
            assert not any(part.startswith("test_") for part in module.split(".")), (path, module)
