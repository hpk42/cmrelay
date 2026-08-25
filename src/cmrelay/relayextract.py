"""Extract declarative data out of relay's Python deployers, without importing them.

relay keeps two things cmrelay needs as data inside Python function bodies:
the preflight port table, and the pinned download URLs and sha256 sums for
the binaries it fetches rather than packages. Parsing the AST gets us both
now, without waiting for relay to lift them into files and without importing
pyinfra.

Every extractor asserts the shape it expects and raises ShapeError otherwise.
That is the point: an upstream refactor becomes a loud build failure here
rather than silent drift in cmrelay. Moving the port table behind a helper
call, for instance, yields "port_services is not a list literal at line 519".

When relay lands the declarative files, delete this module.
"""

import ast
from pathlib import Path

# The files that carry pinned binaries, relative to relay's checkout root.
PIN_SOURCES = [
    "cmdeploy/src/cmdeploy/deployers.py",
    "cmdeploy/src/cmdeploy/mtail/deployer.py",
    "cmdeploy/src/cmdeploy/filtermail/deployer.py",
]

PORT_TABLE_SOURCE = "cmdeploy/src/cmdeploy/deployers.py"

# Sanity floors. relay has 19 port entries and 4 pinned binaries today;
# a large drop means the shape changed rather than that upstream shrank.
MIN_PORT_ENTRIES = 15
EXPECTED_COMPONENTS = {"turn", "iroh", "mtail", "filtermail"}


class ShapeError(Exception):
    """relay's source no longer matches what this extractor understands."""


def _parse(path: Path) -> ast.Module:
    return ast.parse(Path(path).read_text(), filename=str(path))


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
    """A port is either a literal or a `config.<attr>` reference."""
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


def _url(node) -> str:
    """A URL is a literal or an f-string whose only placeholder is {arch}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(value.value)
            elif (
                isinstance(value, ast.FormattedValue)
                and getattr(value.value, "id", "") == "arch"
            ):
                parts.append("{arch}")
            else:
                raise ShapeError(
                    f"URL f-string has an unexpected placeholder at line {node.lineno}"
                )
        return "".join(parts)
    raise ShapeError(f"unrecognised URL expression at line {node.lineno}")


def _enclosing_class(tree, lineno) -> str:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.lineno <= lineno:
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else "?"


def extract_binary_pins(path: Path) -> dict:
    """Pinned download URLs and sha256 sums, per component and architecture.

    An empty result is not an error here: relay spreads its pins over several
    files and any one of them may legitimately carry none. extract_all() does
    the checking, because only it can say which component went missing.
    """
    tree = _parse(path)
    pins = {}

    for node in ast.walk(tree):
        dict_node = None
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript):
            if isinstance(node.value.value, ast.Dict):
                dict_node = node.value.value  # (url, sha) = {arch: (url, sha)}[arch]
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            dict_node = node.value  # sha-only dict, URL built separately
        if dict_node is None or not dict_node.keys:
            continue
        keys = [getattr(k, "value", None) for k in dict_node.keys]
        if set(keys) != {"x86_64", "aarch64"}:
            continue

        component = _enclosing_class(tree, node.lineno).replace("Deployer", "").lower()
        entry = pins.setdefault(component, {})
        for arch, value in zip(keys, dict_node.values):
            if isinstance(value, ast.Tuple) and len(value.elts) == 2:
                entry[arch] = {
                    "url": _url(value.elts[0]),
                    "sha256": ast.literal_eval(value.elts[1]),
                }
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                entry[arch] = {"sha256": value.value}
            else:
                raise ShapeError(f"unrecognised pin value at line {value.lineno}")

        # A sha-only dict needs the URL from a sibling assignment nearby.
        if all("url" not in e for e in entry.values()):
            for other in ast.walk(tree):
                if (
                    isinstance(other, ast.Assign)
                    and any(getattr(t, "id", "") == "url" for t in other.targets)
                    and abs(other.lineno - node.lineno) < 10
                ):
                    url = _url(other.value)
                    for arch in entry:
                        entry[arch]["url"] = url.replace("{arch}", arch)
    return pins


def extract_all(relay_root: Path) -> dict:
    """Everything cmrelay's build needs out of relay's Python, as plain data."""
    relay_root = Path(relay_root)
    pins = {}
    for source in PIN_SOURCES:
        for component, arches in extract_binary_pins(relay_root / source).items():
            pins.setdefault(component, {}).update(arches)

    missing = EXPECTED_COMPONENTS - set(pins)
    if missing:
        raise ShapeError(
            f"no pins found for {', '.join(sorted(missing))};"
            " relay changed how it fetches them"
        )
    for component, arches in pins.items():
        for arch, pin in arches.items():
            if "url" not in pin or "sha256" not in pin:
                raise ShapeError(f"incomplete pin for {component}/{arch}: {pin}")

    return {
        "port_table": extract_port_table(relay_root / PORT_TABLE_SOURCE),
        "binary_pins": pins,
    }
