# The single file: a makeself archive carrying the whole closure.
#
# Two layers of compression, and the split is deliberate. The outer makeself
# archive is uncompressed, because its stub would have to shell out to a
# decompressor the host may not have -- a bare Debian 12 container carries
# only tar and gzip. The inner store tarball uses zstd, decompressed by a
# static zstd that travels inside the archive. The result depends on nothing
# but a POSIX shell and tar.
#
# zstd checks its own frames, so makeself's md5 and crc passes over 175 MiB
# would only re-verify what the decompressor already does.

{
  lib,
  runCommand,
  makeself,
  zstd,
  pkgsStatic,
  gnutar,
  coreutils,
  closureInfo,
  closure,
  cmrelaySrc,
  version,
  # -19 is what a release wants; a development build can trade size for time.
  zstdLevel ? 19,
}:

let
  info = closureInfo { rootPaths = [ closure ]; };
  staticZstd = pkgsStatic.zstd;

  # Files a relay never reads, dropped on the way into the payload.
  #
  # This is a deliberate departure from Nix's usual contract, and worth being
  # explicit about: the store paths a cmrelay host ends up with are no longer
  # byte-for-byte what their hashes name. That is sound here and nowhere else,
  # because the host has no Nix: no daemon, no database, nothing that verifies
  # a path or rebuilds from one, a store that is written once and read-only
  # afterwards, and an uninstall that deletes the whole tree. The one thing it
  # costs is that such a store cannot be used as an input to Nix again, which
  # nothing does.
  #
  # The alternative is overriding the interpreter with nixpkgs' stripConfig
  # and friends. That rebuilds the entire Python package set from source --
  # measured at 48 derivations including cython, cffi and the pytest tooling,
  # over half an hour, and again on every nixpkgs bump -- to save less than
  # this does, and only for Python. Excluding by pattern costs nothing at
  # build time and covers every component at once.
  #
  # libtool .la files are deliberately kept: dovecot's module loader has
  # historically read them, and they are 0.2 MiB.
  #
  # __pycache__ goes too, it's not worth some megabytes.
  deadWeight = [
    "*.a" # static libraries, 63 MiB of it libpython
    "*/include/*" # C headers
    "*/share/doc/*"
    "*/share/man/*"
    "*/share/locale/*" # gettext catalogues; a relay logs in English
    "*/lib/python3.14/test/*" # CPython's own test suite
    "*/idlelib/*" # the Tk IDE
    "*/pkgconfig/*"
    "*/__pycache__/*" # unrefillable on a read-only store
  ];
in
runCommand "cmrelay-${version}"
  {
    nativeBuildInputs = [
      makeself
      zstd
      gnutar
      coreutils
    ];
    inherit version;
    passthru = { inherit closure; };
    meta.description = "self-extracting chatmail relay installer";
  }
  ''
    mkdir -p archive
    cp ${cmrelaySrc}/stub/setup.sh archive/setup.sh
    chmod 755 archive/setup.sh

    # The static zstd is the one binary that has to run before the closure is
    # unpacked, so it cannot come out of the closure itself.
    cp ${staticZstd}/bin/zstd archive/zstd
    chmod 755 archive/zstd

    echo -n "${closure}" > archive/closure

    # Paths are stored relative to /, so unpacking under a staging directory
    # yields <staging>/nix/store/... and unpacking under / would yield the
    # real thing.
    sed 's|^/||' ${info}/store-paths > paths.txt
    echo "packing $(wc -l < paths.txt) store paths"

    # The excludes have to precede --files-from: GNU tar applies them only to
    # names that come after them on the command line.
    tar --create \
        --directory=/ \
        ${lib.concatMapStringsSep " " (p: "--exclude='${p}'") deadWeight} \
        --files-from=paths.txt \
        --owner=0 --group=0 --numeric-owner \
        --sort=name \
        --mtime=@1 \
      | zstd -${toString zstdLevel} -T0 -q -o archive/store.tar.zst

    ls -l archive/

    mkdir -p $out/bin
    # --nox11 matters more than it looks. Without it the stub checks, when
    # stdout is not a terminal, whether DISPLAY is set, and if so re-execs
    # itself inside a spawned xterm ending in "Press Return to close this
    # window". An operator piping the installer to a log, or running it over
    # ssh with X11 forwarding, would get a window instead of output, and an
    # automated run would hang waiting for a keypress nobody is there to give.
    makeself \
      --nox11 \
      --nomd5 --nocrc --nocomp \
      --tar-extra "--owner=0 --group=0 --numeric-owner" \
      archive \
      $out/bin/cmrelay-${version}.run \
      "cmrelay ${version}: chatmail relay installer" \
      ./setup.sh

    ln -s cmrelay-${version}.run $out/bin/cmrelay.run
  ''
