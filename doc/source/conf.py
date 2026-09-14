# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "cmrelay documentation"
copyright = "2026, chatmail collective"
author = "chatmail collective"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = ["sphinx.ext.extlinks"]

templates_path = ["_templates"]
exclude_patterns = []

# Repository links go through the roles below.
# CI sets DOC_GITHUB_REF to the head commit of a pull request.
gh_ref = os.environ.get("DOC_GITHUB_REF", "main")

extlinks = {
    "repofile": (f"https://github.com/chatmail/cmrelay/blob/{gh_ref}/%s", "%s"),
    "repodir": (f"https://github.com/chatmail/cmrelay/tree/{gh_ref}/%s", "%s"),
    # The vendored upstream: the cmdeploy-based chatmail/relay repository.
    "relayfile": ("https://github.com/chatmail/relay/blob/main/%s", "%s"),
    "relaydir": ("https://github.com/chatmail/relay/tree/main/%s", "%s"),
}

# Warn about repository links spelled out in full instead of using the roles.
extlinks_detect_hardcoded_links = True


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_static_path = ["_static"]
html_css_files = [
    "custom.css",
]

# Two columns rather than furo's three: the page's own sections move from
# the right-hand column into the sidebar, which custom.css then widens.
html_sidebars = {
    "**": [
        "sidebar/scroll-start.html",
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/navigation.html",
        "page-toc.html",
        "sidebar/scroll-end.html",
    ]
}

html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#00567a",
        "color-brand-content": "#0076d1",
        "color-sidebar-background": "#f2f6f8",
    },
    "dark_css_variables": {
        "color-brand-primary": "#7fc4e8",
        "color-brand-content": "#63b8e6",
        "color-sidebar-background": "#16202a",
    },
}

html_title = "cmrelay documentation"

html_logo = "_static/chatmail.svg"
