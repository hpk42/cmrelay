# The closure: every chatmail relay package, the copied configuration files
# and the cmrelay CLI, as one store path.
# Not a buildEnv: postfix and dovecot resolve siblings by their own prefix.

{
  lib,
  stdenv,
  runCommand,
  writeText,
  postfix,
  nginx,
  unbound,
  opendkim-milter,
  fcgiwrap,
  openssl,
  fetched,
  acmetool,
  dovecot,
  ldns,
  pythonEnv,
  pythonDeps,
  python3Packages,
  version,
}:

let
  # Only what the host runs, so that a change elsewhere rebuilds nothing.
  cmrelay = lib.fileset.toSource {
    root = ../../host/cmrelay;
    fileset = lib.fileset.difference ../../host/cmrelay ../../host/cmrelay/tests;
  };
  # Real store paths for the header: the env's symlinks dangle during staging.
  stagingPythonPath = python3Packages.makePythonPath (
    python3Packages.requiredPythonModules pythonDeps
  );

  package =
    pkg: extra:
    {
      path = "${pkg}";
      version = pkg.version or "unknown";
    }
    // extra;

  packages = {
    postfix = package postfix {
      postfix = "${postfix}/bin/postfix";
      postconf = "${postfix}/bin/postconf";
      postmap = "${postfix}/bin/postmap";
    };

    dovecot = package dovecot {
      dovecot = "${dovecot}/sbin/dovecot";
      doveconf = "${dovecot}/bin/doveconf";
      doveadm = "${dovecot}/bin/doveadm";
      # the static parameters dovecot-core ships as /usr/share/dovecot/dh.pem
      dh_params = "${dovecot}/share/dovecot/dh.pem";
    };

    # nixpkgs' nginx has stream built in; cmrelay passes an empty stream_module.
    nginx = package nginx {
      nginx = "${nginx}/bin/nginx";
      prefix = "${nginx}";
    };

    unbound = package unbound {
      unbound = "${unbound}/bin/unbound";
      unbound-anchor = "${unbound}/bin/unbound-anchor";
    };

    opendkim = package opendkim-milter {
      opendkim = "${opendkim-milter}/bin/opendkim";
    };

    fcgiwrap = package fcgiwrap {
      fcgiwrap = "${fcgiwrap}/sbin/fcgiwrap";
    };

    acmetool = package acmetool {
      acmetool = "${acmetool}/bin/acmetool";
    };

    filtermail = package fetched.filtermail {
      filtermail = "${fetched.filtermail}/bin/filtermail";
      # the mtail program shipped with this filtermail release
      mtail_program = "${fetched.filtermail-mtail}/filtermail.mtail";
    };

    chatmail-turn = package fetched.chatmail-turn {
      chatmail-turn = "${fetched.chatmail-turn}/bin/chatmail-turn";
    };

    iroh-relay = package fetched.iroh-relay {
      iroh-relay = "${fetched.iroh-relay}/bin/iroh-relay";
    };

    # buildEnv has no version of its own, so the interpreter's is used.
    python = package pythonEnv {
      version = pythonEnv.python.version;
      python = "${pythonEnv}/bin/python3";
      # the copied units start chatmaild's console scripts
      doveauth = "${pythonEnv}/bin/doveauth";
      chatmail-metadata = "${pythonEnv}/bin/chatmail-metadata";
      chatmail-expire = "${pythonEnv}/bin/chatmail-expire";
      chatmail-quota-expire = "${pythonEnv}/bin/chatmail-quota-expire";
      lastlogin = "${pythonEnv}/bin/lastlogin";
    };

    # drill for `cmrelay dns` and `status`: 0.6 MiB against bind's dnsutils
    ldns = package ldns { drill = "${ldns}/bin/drill"; };
    openssl = package openssl { openssl = "${openssl}/bin/openssl"; };
  };

  manifest = writeText "cmrelay-manifest.json" (
    builtins.toJSON {
      inherit version packages;
    }
  );

  # Every path the manifest names is checked at build time.
  checks = lib.concatStringsSep "\n" (
    lib.flatten (
      lib.mapAttrsToList (
        name: attrs:
        lib.mapAttrsToList (
          key: value:
          lib.optionalString (key != "version") ''
            test -e ${value} || {
              echo "closure: ${name}.${key} is missing: ${value}" >&2
              exit 1
            }
          ''
        ) attrs
      ) packages
    )
  );

in
runCommand "cmrelay-closure" { } ''
  ${checks}

  mkdir -p $out
  cp ${manifest} $out/manifest.json
  cp ${../../LICENSE} $out/LICENSE
  cp ${../../release-key.pem} $out/release-key.pem
  cp -r ${cmrelay} $out/cmrelay
  chmod -R u+w $out

  # The header runs the CLI before the store is in place.
  cat > $out/bootstrap.env <<ENV
  CMRELAY_LD=${stdenv.cc.bintools.dynamicLinker}
  CMRELAY_PYTHON=${pythonEnv.python.interpreter}
  CMRELAY_SITE=${stagingPythonPath}
  ENV

  # The entry point: the bundled interpreter, without the host's PATH.
  mkdir -p $out/bin
  cat > $out/bin/cmrelay <<SHIM
  #!${pythonEnv}/bin/python3
  import sys
  sys.path.insert(0, "$out")
  from cmrelay.cli.main import main
  sys.exit(main())
  SHIM
  chmod 755 $out/bin/cmrelay
''
