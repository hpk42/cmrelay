# The closure: every component of a chatmail relay, plus the vendored relay
# configuration and cmrelay's own CLI, as one Nix store path.
#
# This is deliberately not a buildEnv. Merging everything into one bin/ tree
# would break the components that resolve siblings by their own prefix --
# postfix computes daemon_directory, dovecot its module dir -- and would hide
# which store path a given binary came from, which is exactly what
# `cmrelay --version` and a bug report need.
#
# Instead the output is a manifest naming each component's store path. Nix
# scans build outputs for store path references, so naming them in JSON is
# what pulls the whole closure in; nothing else is required to keep it
# complete.

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
  coreutils,
  iproute2,
  fetched,
  acmetool,
  dovecot,
  ldns,
  pythonEnv,
  pythonDeps,
  python3Packages,
  cmrelaySrc,
  version,
  commit,
}:

let
  # The interpreter and its modules as *real* store paths, for the stub.
  # The env under $out is a symlink farm pointing at absolute /nix/store
  # paths, and during staging /nix does not exist yet, so every one of those
  # links dangles. Naming the packages themselves is what survives the
  # relocation.
  stagingPythonPath = python3Packages.makePythonPath (
    python3Packages.requiredPythonModules pythonDeps
  );

  component =
    pkg: extra:
    {
      path = "${pkg}";
      version = pkg.version or "unknown";
    }
    // extra;

  components = {
    # nixpkgs' postfix already defaults command_directory, daemon_directory
    # and meta_directory to its own store paths, so only the state
    # directories need overriding at install time.
    postfix = component postfix {
      postfix = "${postfix}/bin/postfix";
      postconf = "${postfix}/bin/postconf";
      postmap = "${postfix}/bin/postmap";
      postalias = "${postfix}/bin/postalias";
      daemon_directory = "${postfix}/libexec/postfix";
      command_directory = "${postfix}/bin";
      meta_directory = "${postfix}/etc/postfix";
    };

    dovecot = component dovecot {
      dovecot = "${dovecot}/sbin/dovecot";
      doveconf = "${dovecot}/bin/doveconf";
      doveadm = "${dovecot}/bin/doveadm";
      module_dir = "${dovecot}/lib/dovecot";
      # the static parameters dovecot-core ships as /usr/share/dovecot/dh.pem
      dh_params = "${dovecot}/share/dovecot/dh.pem";
    };

    # relay's nginx.conf uses a stream {} block for the TLS-multiplexing
    # listener. On Debian that needs libnginx-mod-stream and a load_module
    # line; nixpkgs builds --with-stream in, which is why the vendored config
    # carries a patch removing that line.
    nginx = component nginx {
      nginx = "${nginx}/bin/nginx";
      prefix = "${nginx}";
    };

    unbound = component unbound {
      unbound = "${unbound}/bin/unbound";
      unbound-checkconf = "${unbound}/bin/unbound-checkconf";
      unbound-anchor = "${unbound}/bin/unbound-anchor";
    };

    # The milter only; cmrelay makes the DKIM key with openssl rather than
    # with opendkim-genkey, which is Perl. See nix/packages/opendkim-milter.nix.
    opendkim = component opendkim-milter {
      opendkim = "${opendkim-milter}/bin/opendkim";
    };

    fcgiwrap = component fcgiwrap {
      fcgiwrap = "${fcgiwrap}/sbin/fcgiwrap";
    };

    acmetool = component acmetool {
      acmetool = "${acmetool}/bin/acmetool";
    };

    filtermail = component fetched.filtermail {
      filtermail = "${fetched.filtermail}/bin/filtermail";
      # the mtail program shipped with this filtermail release, so its own
      # metrics are monitored by the rules written against it
      mtail_program = "${fetched.filtermail-mtail}/filtermail.mtail";
    };

    chatmail-turn = component fetched.chatmail-turn {
      chatmail-turn = "${fetched.chatmail-turn}/bin/chatmail-turn";
    };

    iroh-relay = component fetched.iroh-relay {
      iroh-relay = "${fetched.iroh-relay}/bin/iroh-relay";
    };

    # buildEnv carries no version of its own; the interpreter's is what a
    # bug report wants to see.
    python = component pythonEnv {
      version = pythonEnv.python.version;
      python = "${pythonEnv}/bin/python3";
      # chatmaild's console scripts; the vendored .f units point their
      # ExecStart at these.
      doveauth = "${pythonEnv}/bin/doveauth";
      chatmail-metadata = "${pythonEnv}/bin/chatmail-metadata";
      chatmail-expire = "${pythonEnv}/bin/chatmail-expire";
      chatmail-quota-expire = "${pythonEnv}/bin/chatmail-quota-expire";
      chatmail-fsreport = "${pythonEnv}/bin/chatmail-fsreport";
      lastlogin = "${pythonEnv}/bin/lastlogin";
    };

    # Tools the installer shells out to. Taking them from the store rather
    # than the host is what keeps behaviour identical across distributions.
    coreutils = component coreutils { };
    # `cmrelay dns` queries the authoritative nameservers. drill is one
    # 0.6 MiB store path against seven for bind's dnsutils, and its -Q output
    # is byte-identical to `dig +short` for every type the zone check reads.
    ldns = component ldns { drill = "${ldns}/bin/drill"; };
    openssl = component openssl { openssl = "${openssl}/bin/openssl"; };
    iproute2 = component iproute2 { ss = "${iproute2}/bin/ss"; };
  };

  manifest = writeText "cmrelay-manifest.json" (
    builtins.toJSON {
      schema = 1;
      inherit version commit components;
    }
  );

  # Every path the manifest names is checked at build time, so a nixpkgs
  # layout change fails here rather than at install time on somebody's host.
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
      ) components
    )
  );

in
runCommand "cmrelay-closure"
  {
    passthru = { inherit components; };
    meta.description = "chatmail relay stack, configuration and installer";
  }
  ''
    ${checks}

    mkdir -p $out
    cp ${manifest} $out/manifest.json
    # So a host has the terms of everything running on it, on the host.
    cp ${cmrelaySrc}/LICENSE ${cmrelaySrc}/THIRD-PARTY.md $out/
    cp ${cmrelaySrc}/vendor.lock $out/vendor.lock
    # the preflight port table, extracted from relay's deployers at build time
    cp ${cmrelaySrc}/nix/generated/relay-pins.json $out/relay-pins.json

    cp -r ${cmrelaySrc}/vendor $out/vendor
    cp -r ${cmrelaySrc}/units $out/units
    cp -r ${cmrelaySrc}/release-keys $out/release-keys
    cp -r ${cmrelaySrc}/src/cmrelay $out/cmrelay
    chmod -R u+w $out
    find $out -name __pycache__ -prune -exec rm -rf {} +

    # What the stub needs to run this CLI before the store is in place.
    # Globbing cannot find these: python3.withPackages produces an env whose
    # bin/python3 is a wrapper that execs an absolute store path, so during
    # staging only the raw interpreter is runnable, and only with the env's
    # site-packages named explicitly on PYTHONPATH.
    cat > $out/bootstrap.env <<ENV
    CMRELAY_LD=${stdenv.cc.bintools.dynamicLinker}
    CMRELAY_PYTHON=${pythonEnv.python.interpreter}
    CMRELAY_SITE=${stagingPythonPath}
    ENV

    # The installed entry point: the bundled interpreter running the CLI
    # straight out of the store, with no PATH that could leak the host's.
    mkdir -p $out/bin
    cat > $out/bin/cmrelay <<SHIM
    #!${pythonEnv}/bin/python3
    import sys
    sys.path.insert(0, "$out")
    from cmrelay.cli import main
    sys.exit(main())
    SHIM
    chmod 755 $out/bin/cmrelay
  ''
