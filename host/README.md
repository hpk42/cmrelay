# The cmrelay tool

`cmrelay` is the command an installed host runs.
[The manual](../doc/getting_started.rst) describes its use;
[The cmrelay command](../doc/cmrelay.rst) lists every command and option.


## Developing

The package is `host/cmrelay/` and these sub directories:

| Directory  | Content                                                             |
|------------|---------------------------------------------------------------------|
| `cli/`     | the command line: `main.py`, `output.py`, one module per command    |
| `stack/`   | the deployment, config, users, pins and one directory per component |
| `system/`  | the host underneath: files, systemd, prechecks                      |
| `release/` | the closure, its store paths and signature                          |
| `tests/`   | the unit tests                                                      |

From the repository root:

    uv run pytest host/cmrelay

The tests need no root and no container.


## The manual's examples

Similar to how [pytest documentation](https://pytest.org) has synchronized/tested docs,
`doc/regen.py` keeps the command examples of the manual true to the code:
the help blocks of `doc/cmrelay.rst` come from cmrelay's own parser,
the defaults in `doc/limits.rst` from chatmaild's `chatmail.ini` template,
and `cmrelay ...` invocations in the manual and the READMEs must parse.
The unit tests fail when anything differs, and this rewrites the pages:

    uv run python doc/regen.py --update
