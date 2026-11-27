import json
import subprocess
from pathlib import Path

project = "cmrelay"
copyright = "2026, holger krekel"
# the version the installer reports, shown in the sidebar and on the front page
release = (Path(__file__).parents[1] / "host/VERSION").read_text().strip()
html_title = f"cmrelay {release}"

extensions = ["sphinx.ext.extlinks", "sphinx_copybutton"]
templates_path = ["_templates"]

# The chatmail/relay commit the copied chapters and the installed software come from.
pins = Path(__file__).parents[1] / "host/cmrelay/stack/pins.json"
commit = json.loads(pins.read_text())["RELAY_COMMIT"]
rst_epilog = f".. |relay_commit| replace:: ``{commit[:12]}``"

# Links into the chatmail/relay repository at that commit.
extlinks = {
    "relayfile": (f"https://github.com/chatmail/relay/blob/{commit}/%s", "%s"),
    "relaydir": (f"https://github.com/chatmail/relay/tree/{commit}/%s", "%s"),
}
extlinks_detect_hardcoded_links = True

# delta.chat/help picks the reader's language in JavaScript and keeps the anchor.
linkcheck_anchors_ignore_for_url = [r"https://delta\.chat/help"]

# The newest release, whose installers the commands in the manual fetch:
# between releases host/VERSION on main names the next one, which has no installers yet.
describe = ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"]
tag = subprocess.check_output(describe, cwd=Path(__file__).parent, text=True).strip()
installer_tokens = {
    "|release-url|": f"https://github.com/hpk42/cmrelay/releases/download/{tag}",
    "|installer-x86_64|": f"cmrelay-installer-{tag[1:]}-x86_64-linux.run",
    "|installer-aarch64|": f"cmrelay-installer-{tag[1:]}-aarch64-linux.run",
}


def setup(app):
    # replaced as each page is read, so that they reach into literal blocks too,
    # which reST substitutions do not
    def replace_tokens(app, docname, source):
        for token, value in installer_tokens.items():
            source[0] = source[0].replace(token, value)

    app.connect("source-read", replace_tokens)


# copies a console block's commands without their "$ " prompt and without the output
copybutton_prompt_text = "$ "

html_theme = "furo"
html_logo = "_static/cmrelay.svg"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["close-nav.js", "theme-toggle.js"]

# Two columns rather than furo's three: one template puts every page and its
# sections into the sidebar, so there is no right-hand column.
html_sidebars = {
    "**": [
        "sidebar/scroll-start.html",
        "sidebar/brand.html",
        "sidebar/search.html",
        "fullnav.html",
        "sidebar/scroll-end.html",
    ]
}

# A dark sidebar beside light content.
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#00567a",
        "color-brand-content": "#0076d1",
        "color-sidebar-background": "#16202b",
        "color-sidebar-background-border": "#16202b",
        "color-sidebar-brand-text": "#ffffff",
        "color-sidebar-search-background": "#1f2d3b",
        "color-sidebar-search-background--focus": "#27394b",
        "color-sidebar-search-border": "#2c3f52",
        "color-sidebar-search-text": "#ffffff",
        "color-sidebar-search-icon": "#8ea3b5",
    },
    "dark_css_variables": {
        "color-brand-primary": "#7fc4e8",
        "color-brand-content": "#63b8e6",
        "color-sidebar-background": "#0f161d",
        "color-sidebar-background-border": "#0f161d",
    },
}
