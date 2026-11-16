# sphinx and the theme come from the doc group of the root pyproject.toml
UV = uv run --group doc

html:
	$(UV) sphinx-build -W -b html -d ../build/doc/doctrees . ../build/doc/html

auto:
	$(UV) sphinx-autobuild -d ../build/doc/doctrees . ../build/doc/html

# The pictures in diagrams/ are committed; this renders them again, with nix.
diagrams:
	cd diagrams && for name in components mailflow; do \
	    nix run nixpkgs#mermaid-cli -- -p puppeteer.json -b white -i $$name.mmd -o $$name.svg; \
	done
	sed -i -E 's/@font-face *\{[^}]*\}//g' diagrams/*.svg

.PHONY: html auto diagrams
