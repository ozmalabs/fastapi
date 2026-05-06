"""
ozma-spec: generate a complete OpenAPI spec (with x-gamma) from a FastAPI app.

Usage:
    ozma-spec mypackage:app
    ozma-spec mypackage.submodule:api
    ozma-spec --install mypackage mypackage.web:app
    ozma-spec --output spec.json mypackage:app
    ozma-spec --format yaml mypackage:app

Discovery:
    The module:attr syntax is identical to uvicorn's. If no attr is given,
    the module is scanned for FastAPI instances named app, application, or api.

Installation:
    --install <package>  pip-installs the package before import. Use this when
    the target is not already in the current environment.
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import types
from typing import Any


def _pip_install(package: str) -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", package],
        stdout=sys.stderr,
    )


def _load_app(module_path: str) -> Any:
    """
    Load a FastAPI app from a module path.

    Accepts:
        module:attr        — import module, return module.attr
        module             — import module, auto-discover FastAPI instance
    """
    if ":" in module_path:
        module_name, attr = module_path.rsplit(":", 1)
    else:
        module_name = module_path
        attr = None

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        print(f"error: cannot import {module_name!r}: {exc}", file=sys.stderr)
        print(
            f"hint: try --install <package> to install the package first",
            file=sys.stderr,
        )
        sys.exit(1)

    if attr is not None:
        app = getattr(module, attr, None)
        if app is None:
            print(
                f"error: {module_name!r} has no attribute {attr!r}", file=sys.stderr
            )
            sys.exit(1)
        return app

    return _autodiscover(module)


def _autodiscover(module: types.ModuleType) -> Any:
    """Scan module namespace for FastAPI instances."""
    try:
        from fastapi import FastAPI
    except ImportError:
        FastAPI = None  # type: ignore[assignment,misc]

    candidates = ["app", "application", "api", "server"]
    for name in candidates:
        obj = getattr(module, name, None)
        if obj is not None:
            if FastAPI is not None and isinstance(obj, FastAPI):
                return obj
            # Duck-type fallback: has openapi() method
            if callable(getattr(obj, "openapi", None)):
                return obj

    # Broader scan
    for name in dir(module):
        obj = getattr(module, name, None)
        if obj is None:
            continue
        if FastAPI is not None and isinstance(obj, FastAPI):
            return obj

    print(
        f"error: no FastAPI instance found in {module.__name__!r}.\n"
        "Pass an explicit attr: module:app",
        file=sys.stderr,
    )
    sys.exit(1)


def _generate_schema(app: Any) -> dict[str, Any]:
    """Call app.openapi() to produce the schema dict."""
    try:
        schema = app.openapi()
    except Exception as exc:
        print(f"error: openapi() failed: {exc}", file=sys.stderr)
        sys.exit(1)
    return schema  # type: ignore[return-value]


def _output(schema: dict[str, Any], fmt: str, dest: str | None) -> None:
    if fmt == "yaml":
        try:
            import yaml  # type: ignore[import-untyped]
            text = yaml.dump(schema, allow_unicode=True, sort_keys=False)
        except ImportError:
            print(
                "error: PyYAML is required for YAML output (pip install pyyaml)",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"

    if dest:
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {dest}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ozma-spec",
        description="Generate a complete OpenAPI spec (with x-gamma) from a FastAPI app.",
    )
    parser.add_argument(
        "target",
        help="module:attr path to the FastAPI app (e.g. mypackage.web:app)",
    )
    parser.add_argument(
        "--install",
        metavar="PACKAGE",
        help="pip-install this package before import",
    )
    parser.add_argument(
        "--format",
        choices=["json", "yaml"],
        default="json",
        dest="fmt",
        help="output format (default: json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        default=None,
        help="write to FILE instead of stdout",
    )

    args = parser.parse_args()

    if args.install:
        print(f"installing {args.install}...", file=sys.stderr)
        _pip_install(args.install)

    app = _load_app(args.target)
    schema = _generate_schema(app)
    _output(schema, args.fmt, args.output)
