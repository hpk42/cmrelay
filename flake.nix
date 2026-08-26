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
        # a relay runs mtail, not the three tools shipped beside it
        mtail-minimal = pkgs.callPackage ./nix/packages/mtail-minimal.nix { };
        # ... and the opendkim milter, not the Perl script that made its key
        opendkim-milter = pkgs.callPackage ./nix/packages/opendkim-milter.nix { };

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
        ];

        pythonEnv = pkgs.python3.withPackages (_: pythonDeps);

        closure = pkgs.callPackage ./nix/closure.nix {
          inherit
            fetched
            acmetool
            dovecot
            mtail-minimal
            opendkim-milter
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
            mtail-minimal
            opendkim-milter
            acmetool
            chatmaild
            crypt-r
            pythonEnv
            ;
          inherit (fetched)
            filtermail
            chatmail-turn
            iroh-relay
            filtermail-mtail
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

        # pytest deliberately does not go in pythonEnv: that environment ends
        # up in the closure a relay runs, and a relay has no business
        # carrying a test framework.
        checks.pytest =
          let
            testEnv = pkgs.python3.withPackages (
              ps: pythonDeps ++ [ ps.pytest ]
            );
          in
          pkgs.runCommand "cmrelay-pytest" { } ''
            cp -r ${./.}/src ${./.}/tests ${./.}/vendor .
            cp ${./.}/vendor.toml ${./.}/vendor.lock .
            cp -r ${./.}/patches .
            cp -r ${./.}/nix .
            cp -r ${./.}/units .
            chmod -R u+w .
            # The tests that read the pinned relay checkout skip without it;
            # a flake check has no submodule to read.
            ${testEnv}/bin/python -m pytest tests -q -p no:cacheprovider
            touch $out
          '';

        formatter = pkgs.nixfmt-tree;

        _module.args.lib = lib;
      }
    );
}
