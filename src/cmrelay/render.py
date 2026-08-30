"""Render relay's own templates, with relay's own renderer and Config.

Deliberately not a rewrite: same templates, same chatmaild Config, same
chatmail.ini, so there is no semantic transformation in the pipeline.

Rendering only. Everything that reaches the filesystem lives in
`cmrelay.fsops`, so that "every change is reported" is a property the test
suite enforces rather than a habit.
"""

from pathlib import Path

from jinja2 import Environment, StrictUndefined

# Same as pyinfra.api.util.get_template. StrictUndefined is the load-bearing
# half: a missing variable must fail loudly, not yield an empty directive.
_ENV = Environment(undefined=StrictUndefined, keep_trailing_newline=True)


def render_template(src: Path, **data) -> str:
    """Render one .j2 file the way pyinfra's files.template would."""
    return _ENV.from_string(Path(src).read_text()).render(**data)


def render_format(src: Path, **params) -> str:
    """Render one .f file the way basedeploy.configure_remote_units would."""
    return Path(src).read_text().format(**params)
