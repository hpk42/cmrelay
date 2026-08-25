{
  description = "cmrelay: a single-file chatmail relay installer built as a Nix closure";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" ] (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        lib = pkgs.lib;

        fetched = pkgs.callPackage ./nix/fetch/binaries.nix { inherit system; };

        crypt-r = pkgs.python3Packages.callPackage ./nix/packages/crypt-r.nix { };
        acmetool = pkgs.callPackage ./nix/packages/acmetool.nix { };
        dovecot = pkgs.callPackage ./nix/packages/dovecot.nix { };

        # chatmaild and the cmrelay CLI share one interpreter, which is what
        # makes template rendering an import rather than an integration:
        # cmrelay renders relay's .j2 files with the same jinja2 and the same
        # chatmaild.config.Config that cmdeploy uses today.
        chatmaild = pkgs.python3Packages.callPackage ./nix/packages/chatmaild.nix {
          inherit crypt-r;
        };

        pythonDeps = with pkgs.python3Packages; [
          chatmaild
          crypt-r
          jinja2
          # the website build relay performs, reimplemented in cmrelay
          markdown
          qrcode
          pillow
        ];

        pythonEnv = pkgs.python3.withPackages (_: pythonDeps);

        closure = pkgs.callPackage ./nix/closure.nix {
          inherit
            fetched
            acmetool
            dovecot
            pythonEnv
            pythonDeps
            ;
          cmrelaySrc = ./.;
        };

        payload = pkgs.callPackage ./nix/payload.nix {
          inherit closure;
          cmrelaySrc = ./.;
          version = self.shortRev or self.dirtyShortRev or "dev";
        };
      in
      {
        packages = {
          default = payload;
          inherit
            payload
            closure
            dovecot
            acmetool
            chatmaild
            crypt-r
            pythonEnv
            ;
          inherit (fetched)
            filtermail
            chatmail-turn
            iroh-relay
            mtail
            ;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.python3Packages.pytest
            pkgs.ruff
            pkgs.makeself
            pkgs.zstd
            pkgs.quilt
            pkgs.git
          ];
          shellHook = ''
            echo "cmrelay devShell: pytest, ruff, makeself, quilt"
          '';
        };

        checks.pytest = pkgs.runCommand "cmrelay-pytest" { } ''
          cp -r ${./.}/{src,tests,vendor,vendor.toml,vendor.lock} .
          chmod -R u+w .
          PYTHONPATH=$PWD/src ${pythonEnv}/bin/python -m pytest tests -q \
            && touch $out
        '';

        formatter = pkgs.nixfmt-tree;

        _module.args.lib = lib;
      }
    );
}
