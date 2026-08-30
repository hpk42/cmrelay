"""Read relay's pinned artefacts and its preflight port table as plain data.

Two pieces of data cmrelay's build needs live in relay's Python rather than
in files the vendoring mapping can copy, and they are read two different ways
because relay keeps them two different ways.

The pinned download URLs and sha256 sums live in `cmdeploy/pins.py`, which
imports nothing. That is deliberate on relay's side, and it means cmrelay can
run the module and read its names rather than parsing them out. The only
guard needed is that it stays importless: an import would mean executing
something with dependencies, and this is the one place cmrelay runs upstream
code rather than reading it.

The preflight port table is still a list built inside `deploy_chatmail()`, so
it is parsed out of the AST. That extractor asserts the shape it expects, so
an upstream refactor becomes a loud build failure here rather than silent
drift: moving the table behind a helper call yields "port_services is not a
list literal at line 502". When relay lifts the port table into a file too,
this module goes away entirely.
"""

import ast
from pathlib import Path

PINS_SOURCE = "cmdeploy/src/cmdeploy/pins.py"
PORT_TABLE_SOURCE = "cmdeploy/src/cmdeploy/deployers.py"

# The artefact tables cmrelay fetches. relay has one more, DOVECOT_SHA256 for
# the .deb packages, which cmrelay does not use because it builds dovecot
# from the chatmail source tree.
EXPECTED_PINS = [
    "FILTERMAIL_ARTIFACTS",
    "MTAIL_ARTIFACTS",
    "TURN_ARTIFACTS",
    "IROH_ARTIFACTS",
]

# relay has 19 port entries today; a large drop means the shape changed
# rather than that upstream shrank.
MIN_PORT_ENTRIES = 15


class ShapeError(Exception):
    """relay's source no longer matches what this reader understands."""


def _parse(path: Path) -> ast.Module:
    return ast.parse(Path(path).read_text(), filename=str(path))


# ----------------------------------------------------------------------
# pinned artefacts, read from cmdeploy/pins.py
# ----------------------------------------------------------------------


def read_pins(path: Path) -> dict:
    """Run relay's pins module and return its public names.

    The importlessness check is the whole safety story. pins.py is data
    written as Python, and running data is fine; running something that pulls
    in pyinfra is not, and would fail in the build sandbox anyway.
    """
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ShapeError(
                f"{path} imports something at line {node.lineno};"
                " cmrelay only runs it because it does not"
            )

    namespace = {}
    exec(compile(tree, str(path), "exec"), namespace)

    pins = {
        name: value
        for name, value in namespace.items()
        if not name.startswith("__") and _jsonable(value)
    }
    missing = [name for name in EXPECTED_PINS if name not in pins]
    if missing:
        raise ShapeError(
            f"{path} no longer defines {', '.join(missing)};"
            " relay changed how it pins the binaries cmrelay fetches"
        )
    return pins


def _jsonable(value) -> bool:
    # DOVECOT_SHA256 is keyed by (package, arch) tuples, which JSON cannot
    # express; cmrelay does not use it, so it is dropped rather than encoded.
    if isinstance(value, str):
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) for k in value) and all(
            _jsonable(v) for v in value.values()
        )
    if isinstance(value, (list, tuple)):
        return all(_jsonable(v) for v in value)
    return False


# ----------------------------------------------------------------------
# the preflight port table, parsed out of deployers.py
# ----------------------------------------------------------------------


def _str_or_strlist(node) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.List):
        out = []
        for elt in node.elts:
            if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                raise ShapeError(
                    f"port name list holds a non-string at line {elt.lineno}"
                )
            out.append(elt.value)
        return out
    raise ShapeError(f"expected a name or list of names at line {node.lineno}")


def _port(node) -> dict:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return {"port": node.value}
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id != "config":
            raise ShapeError(f"port refers to {node.value.id}, expected config")
        return {"config_attr": node.attr}
    raise ShapeError(f"unrecognised port expression at line {node.lineno}")


def _entries(list_node) -> list[dict]:
    out = []
    for elt in list_node.elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) == 2):
            raise ShapeError(f"port entry is not a 2-tuple at line {elt.lineno}")
        names, port = elt.elts
        out.append({"names": _str_or_strlist(names), **_port(port)})
    return out


def extract_port_table(path: Path) -> list[dict]:
    """The preflight table: which process names may hold which port."""
    tree = _parse(path)
    base, added, appended = None, [], []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "port_services" for t in node.targets
        ):
            if not isinstance(node.value, ast.List):
                raise ShapeError(
                    f"port_services is not a list literal at line {node.lineno}"
                )
            base = _entries(node.value)
        elif (
            isinstance(node, ast.AugAssign)
            and getattr(node.target, "id", "") == "port_services"
        ):
            if not isinstance(node.value, ast.List):
                raise ShapeError(f"port_services += non-list at line {node.lineno}")
            added += _entries(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and getattr(node.func.value, "id", "") == "port_services"
        ):
            # conditional entries, e.g. acmetool's 402 only in acme mode
            arg = node.args[0]
            if not (isinstance(arg, ast.Tuple) and len(arg.elts) == 2):
                raise ShapeError(
                    f"port_services.append of a non-2-tuple at line {node.lineno}"
                )
            names, port = arg.elts
            appended.append(
                {"names": _str_or_strlist(names), **_port(port), "conditional": True}
            )

    if base is None:
        raise ShapeError(
            "no port_services assignment found; relay moved the port table"
        )
    entries = base + appended + added
    if len(entries) < MIN_PORT_ENTRIES:
        raise ShapeError(
            f"only {len(entries)} port entries found, expected at least"
            f" {MIN_PORT_ENTRIES}"
        )
    return entries


def extract_all(relay_root: Path) -> dict:
    """Everything cmrelay's build needs out of relay's Python, as plain data."""
    relay_root = Path(relay_root)
    return {
        "pins": read_pins(relay_root / PINS_SOURCE),
        "port_table": extract_port_table(relay_root / PORT_TABLE_SOURCE),
    }
