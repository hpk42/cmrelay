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

        # Both architectures land on one release page, so artifacts say which.
        arch = if pkgs.stdenv.hostPlatform.isAarch64 then "aarch64" else "x86_64";

        fetched = pkgs.callPackage ./nix/fetch/binaries.nix { inherit system; };

        crypt-r = pkgs.python3Packages.callPackage ./nix/packages/crypt-r.nix { };
        # the payload's own extractor, so no host tar is needed
        bsdtar-minimal = pkgs.callPackage ./nix/packages/bsdtar-minimal.nix {
          libarchiveStatic = pkgs.pkgsStatic.libarchive;
        };
        # postfix pulls ICU in for SMTPUTF8 and uses only its IDNA tables
        icu-idna = pkgs.callPackage ./nix/packages/icu-idna.nix { };
        # withLDAP pulls openldap and a second cyrus-sasl for `ldap:` lookup
        # tables. relay's postfix configuration defines none, and a chatmail
        # relay has no directory to look anything up in.
        postfix = pkgs.postfix.override {
          icu = icu-idna;
          withLDAP = false;
        };
        acmetool = pkgs.callPackage ./nix/packages/acmetool.nix { };
        # libsystemd only. dovecot links it for sd_notify and fcgiwrap for
        # socket activation; neither runs systemd, which the host provides.
        # The plain `systemd` attribute is the whole init system, and
        # systemdLibs is the same 261.1 built for exactly this case.
        dovecot = pkgs.callPackage ./nix/packages/dovecot.nix {
          systemd = pkgs.systemdLibs;
        };
        fcgiwrap = pkgs.fcgiwrap.override { systemd = pkgs.systemdLibs; };
        # nginx is the one component nixpkgs builds against OpenSSL 4, and a
        # second major costs 8.7 MiB to serve the same TLS that postfix,
        # dovecot, unbound, opendkim and python all get from pkgs.openssl.
        nginx = pkgs.nginx.override { openssl = pkgs.openssl; };
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
          # PEP 440 ordering, which is what tells `upgrade` a candidate is newer
          packaging
          qrcode
        ];

        pythonEnv = pkgs.python3.withPackages (_: pythonDeps);

        # The release version, and the commit that produced it. A tag push
        # is checked against VERSION in the release workflow, so the file
        # name, the manifest and the tag cannot disagree.
        version = lib.fileContents ./VERSION;
        commit = self.rev or self.dirtyRev or "unknown";

        closure = pkgs.callPackage ./nix/closure.nix {
          inherit
            fcgiwrap
            nginx
            fetched
            acmetool
            dovecot
            postfix
            opendkim-milter
            pythonEnv
            pythonDeps
            version
            commit
            ;
          cmrelaySrc = ./.;
        };

        payload = pkgs.callPackage ./nix/payload.nix {
          inherit arch closure bsdtar-minimal version;
          cmrelaySrc = ./.;
        };
      in
      {
        packages = {
          default = payload;
          inherit
            payload
            closure
            dovecot
            bsdtar-minimal
            opendkim-milter
            postfix
            nginx
            icu-idna
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
            pkgs.zstd
            pkgs.quilt
            pkgs.git
          ];
          shellHook = ''
            echo "cmrelay devShell: pytest, ruff, quilt"
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
          pkgs.runCommand "cmrelay-pytest" { nativeBuildInputs = [ pkgs.openssl ]; } ''
            cp -r ${./.}/src ${./.}/tests ${./.}/vendor .
            cp -r ${./.}/patches ${./.}/nix ${./.}/units ${./.}/stub .
            cp ${./.}/vendor.toml ${./.}/vendor.lock ${./.}/pyproject.toml .
            chmod -R u+w .
            # The tests that read the pinned relay checkout skip without it;
            # a flake check has no submodule to read.
            ${testEnv}/bin/python -m pytest tests -q -p no:cacheprovider
            touch $out
          '';

        formatter = pkgs.nixfmt-tree;
      }
    );
}
