{
  description = "cmrelay: a single-file chatmail relay installer built as a Nix closure";

  inputs = {
    # a stable branch: a lock bump brings fixes and no new major versions
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      perSystem =
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          lib = pkgs.lib;

          arch = if pkgs.stdenv.hostPlatform.isAarch64 then "aarch64" else "x86_64";

          fetched = pkgs.callPackage ./nix/fetch-binaries.nix { inherit arch; };

          crypt-r = pkgs.python3Packages.callPackage ./nix/packages/crypt-r.nix { };
          # the payload's own extractor, so no host tar is needed
          bsdtar-minimal = pkgs.callPackage ./nix/packages/bsdtar-minimal.nix {
            libarchiveStatic = pkgs.pkgsStatic.libarchive;
          };
          # postfix pulls ICU in for SMTPUTF8 and uses only its IDNA tables
          icu-idna = pkgs.callPackage ./nix/packages/icu-idna.nix { };
          # No LDAP: the relay's postfix has no `ldap:` tables.
          # makedefs.out records the build host's kernel major; pinned for reproducibility.
          postfix =
            (pkgs.postfix.override {
              icu = icu-idna;
              withLDAP = false;
              # nixpkgs' postfix asks its cyrus-sasl for the MySQL plugin, whatever withMySQL says;
              # the plugin brings a MariaDB client and curl. This one declines.
              cyrus_sasl = pkgs.cyrus_sasl // {
                override = _: pkgs.cyrus_sasl;
              };
            }).overrideAttrs
              (old: {
                postInstall = (old.postInstall or "") + ''
                  sed -i 's/^SYSTYPE.*/SYSTYPE = LINUX/' $out/etc/postfix/makedefs.out
                '';
              });
          acmetool = pkgs.callPackage ./nix/packages/acmetool.nix { };
          # libsystemd only; `systemd` would be the whole init system.
          dovecot = pkgs.callPackage ./nix/packages/dovecot.nix {
            systemd = pkgs.systemdLibs;
          };
          fcgiwrap = pkgs.fcgiwrap.override { systemd = pkgs.systemdLibs; };
          # nixpkgs builds nginx against OpenSSL 4; a second major costs 8.7 MiB.
          # No extra modules: nixpkgs adds rtmp and more-headers by default.
          # No XSLT either: nixpkgs compiles it in, but no page here is transformed.
          nginx =
            (pkgs.nginx.override {
              openssl = pkgs.openssl;
              modules = [ ];
            }).overrideAttrs
              (old: {
                configureFlags = lib.remove "--with-http_xslt_module" old.configureFlags;
                buildInputs = lib.subtractLists [ pkgs.libxml2 pkgs.libxslt ] old.buildInputs;
              });
          # the milter without the Perl key generator
          opendkim-milter = pkgs.callPackage ./nix/packages/opendkim-milter.nix { };
          # chatmaild and cmrelay share one interpreter: cmrelay imports chatmaild's Config.
          chatmaild = pkgs.python3Packages.callPackage ./nix/packages/chatmaild.nix {
            inherit crypt-r;
          };

          pythonDeps = with pkgs.python3Packages; [
            chatmaild
            jinja2
            markdown
            # PEP 440 ordering for `upgrade`
            packaging
            qrcode
          ];

          pythonEnv = pkgs.python3.withPackages (_: pythonDeps);

          version = lib.fileContents ../host/VERSION;

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
              ;
          };

          payload = pkgs.callPackage ./nix/payload.nix {
            inherit
              arch
              closure
              bsdtar-minimal
              version
              ;
          };
        in
        {
          packages = {
            inherit payload closure;
          };

          # pytest stays out of pythonEnv: that one ends up in the closure.
          checks.pytest =
            let
              testEnv = pkgs.python3.withPackages (ps: pythonDeps ++ [ ps.pytest ]);
            in
            pkgs.runCommand "cmrelay-pytest" { nativeBuildInputs = [ pkgs.openssl ]; } ''
              cp -r ${../.}/host ${../.}/installer ${../.}/doc ${../.}/README.md ${../.}/pyproject.toml ${../.}/release-key.pem .
              chmod -R u+w .
              ${testEnv}/bin/python -m pytest -q -p no:cacheprovider host/cmrelay installer/test_installer.py
              # the closure's chatmaild leaves out its tests, this copy has them
              PYTHONPATH=host/chatmaild/src ${testEnv}/bin/python -m pytest -q -p no:cacheprovider host/chatmaild/src
              touch $out
            '';
        };
    in
    nixpkgs.lib.genAttrs [ "packages" "checks" ] (
      output: nixpkgs.lib.genAttrs systems (system: (perSystem system).${output})
    );
}
