
## Building the documentation

You can use the `make` command and `make html` to build web pages.

You need a Python environment with `sphinx` and other dependencies;
with [uv](https://docs.astral.sh/uv/) installed, `uv run --group doc` provides one
on the fly: `uv run --group doc make -C doc html` from the repository root.

To develop/change documentation, you can do:

    cd doc
    uv run --group doc make auto

A page will open at http://127.0.0.1:8000/ serving the docs
and it will react to changes to source files pretty fast.
