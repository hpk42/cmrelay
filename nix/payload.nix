# The single file: a shell header with the store bolted onto the end of it.
#
# There is no makeself here, and no host tar. makeself's stub unpacks with
# the host's tar before any cmrelay code runs, so a host without tar --
# AlmaLinux's minimal image among them -- fails with "Extraction failed" and
# no way to say why. The header below slices its own zstd and tar out of
# itself by byte offset instead, using only coreutils, and streams the store
# straight through them without landing it on disk twice.
#
# Owning the format has three other effects. The offsets are ours, which is
# what a signature trailer needs. The stub is ours, which is where `umask
# 077` silently breaking /var/lib/postfix, and a spawned xterm asking an
# unattended install to press Return, both came from. And zstd checks its own
# frames, so nothing has to re-verify what the decompressor already did.
#
# Layout, with every offset patched into fixed-width fields in the header so
# that patching cannot change the size of the header they are measured from:
#
#     [ header ][ zstd ][ tar.zst ][ store.tar.zst ]

{
  lib,
  runCommand,
  zstd,
  pkgsStatic,
  gnutar,
  coreutils,
  closureInfo,
  closure,
  bsdtar-minimal,
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
    "*/share/*/doc/*" # postfix keeps its 5.4 MiB of it one level deeper
    "*/share/man/*"
    "*/share/locale/*" # gettext catalogues; a relay logs in English
    # glibc's locale *sources*: 16 MiB of text that only localedef reads, and
    # localedef lives in glibc's bin output, which this closure does not have.
    # Nothing here can consume them. share/terminfo and share/zoneinfo look
    # similar and are not: readline and python read the first, python, mtail
    # and acmetool the second, and both cost under 0.3 MiB compressed anyway.
    "*/share/i18n/*"
    "*/lib/python3.14/test/*" # CPython's own test suite
    "*/idlelib/*" # the Tk IDE
    "*/pkgconfig/*"
    "*/__pycache__/*" # unrefillable on a read-only store
  ];
in
runCommand "cmrelay-${version}"
  {
    nativeBuildInputs = [
      zstd
      gnutar
      coreutils
    ];
    inherit version;
    passthru = { inherit closure; };
    meta.description = "self-extracting chatmail relay installer";
  }
  ''
    # The header carries the closure path, so patching it changes the
    # header's own length. Substitute that first, measure, then patch the
    # numeric fields, whose widths are fixed and therefore free.
    sed "s|@CLOSURE@|${closure}|" ${cmrelaySrc}/stub/header.sh > header.sh

    cp ${staticZstd}/bin/zstd zstd
    chmod +w zstd

    # tar travels compressed, unpacked by the zstd above: 0.83 MiB becomes
    # 0.36. zstd itself cannot, since nothing exists yet that could unpack it.
    zstd -${toString zstdLevel} -q -o tar.zst ${bsdtar-minimal}/bin/bsdtar

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
      | zstd -${toString zstdLevel} -T0 -q -o store.tar.zst

    patch_size() {
      # Same width in, same width out; the header's length must not move.
      # Padded on the right, not the left: a POSIX shell reads a leading zero
      # as the start of an octal literal, and $((0000008395)) is not octal.
      value=$(printf '%-10s' "$2")
      sed -i "s|^$1=0\{10\}$|$1=$value|" header.sh
      grep -q "^$1=$2 *$" header.sh || {
        echo "payload: could not patch $1 into the header" >&2
        exit 1
      }
    }

    patch_size CMRELAY_HEADER_BYTES "$(stat -c%s header.sh)"
    patch_size CMRELAY_ZSTD_BYTES   "$(stat -c%s zstd)"
    patch_size CMRELAY_TAR_BYTES    "$(stat -c%s tar.zst)"
    patch_size CMRELAY_STORE_BYTES  "$(stat -c%s store.tar.zst)"

    mkdir -p $out/bin
    run=$out/bin/cmrelay-${version}.run
    cat header.sh zstd tar.zst store.tar.zst > $run
    chmod 755 $run

    # The header's recorded length is what every offset is derived from, so
    # a mismatch would slice binaries out of the middle of the script.
    recorded=$(sed -n 's|^CMRELAY_HEADER_BYTES=\([0-9]*\) *$|\1|p' header.sh)
    actual=$(stat -c%s header.sh)
    [ "$recorded" = "$actual" ] || {
      echo "payload: header is $actual bytes but records $recorded" >&2
      exit 1
    }

    ls -l $out/bin
    ln -s cmrelay-${version}.run $out/bin/cmrelay.run
  ''
