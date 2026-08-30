# The single file: a shell header with the store bolted onto the end of it.
# See stub/header.sh for why the format is cmrelay's own rather than
# makeself's. Offsets are patched into fixed-width fields, so that patching
# cannot change the size of the header they are measured from:
#
#     [ header ][ zstd ][ tar.zst ][ store.tar.zst ]

{
  lib,
  stdenv,
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
  # Both architectures land on one release page, so the file says which.
  arch = if stdenv.hostPlatform.isAarch64 then "aarch64" else "x86_64";
  info = closureInfo { rootPaths = [ closure ]; };
  staticZstd = pkgsStatic.zstd;
  # Files a relay never reads, dropped on the way into the payload. This
  # departs from Nix's usual contract: the installed paths are no longer
  # byte-for-byte what their hashes name. Sound only because the host has no
  # Nix at all, nothing that verifies or rebuilds a path, and an uninstall
  # that deletes the tree; the cost is that such a store cannot be an input to
  # Nix again, which nothing does.
  #
  # libtool .la files are kept: dovecot's module loader has historically read
  # them, and they are 0.2 MiB.
  deadWeight = [
    "*.a" # static libraries, 63 MiB of it libpython
    "*/include/*" # C headers
    "*/share/doc/*"
    "*/share/*/doc/*" # postfix keeps its 5.4 MiB of it one level deeper
    "*/share/man/*"
    "*/share/locale/*" # gettext catalogues; a relay logs in English
    # glibc's locale *sources*, which only localedef reads and localedef is
    # not in this closure. share/terminfo and share/zoneinfo look similar and
    # are not: readline, python, mtail and acmetool read those.
    "*/share/i18n/*"
    "*/lib/python3.14/test/*" # CPython's own test suite
    "*/idlelib/*" # the Tk IDE
    "*/lib/python3.14/tkinter/*" # no Tk in the closure to import
    "*/lib/python3.14/ensurepip/*" # bundled wheels; nothing here makes a venv
    "*/lib/python3.14/pydoc_data/*" # help() topic text
    "*/pkgconfig/*"
    "*/__pycache__/*" # unrefillable on a read-only store
    # gcc's runtime output ships every sanitizer and the OpenMP, quad-math and
    # transactional-memory runtimes beside libstdc++, and a relay links none of
    # them: across the 903 ELF files in the closure only libstdc++ and
    # libgcc_s appear in any DT_NEEDED, and the sole other mention of libasan
    # is a comment in CPython's own test suite, which is excluded above.
    "*/lib/lib*san.so*" # asan, tsan, hwasan, lsan, ubsan
    "*/lib/libgomp.so*"
    "*/lib/libquadmath.so*"
    "*/lib/libitm.so*"
    "*/lib/libatomic.so*"
    "*/lib/libssp.so*"
    # postfix uses db's C API; nothing links the C++ one.
    "*/lib/libdb_cxx*"
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

    # tar travels compressed, unpacked by the zstd above. zstd itself cannot,
    # since nothing exists yet that could unpack it.
    zstd -${toString zstdLevel} -q -o tar.zst ${bsdtar-minimal}/bin/bsdtar

    # Relative to /, so unpacking under a staging directory yields
    # <staging>/nix/store/...
    sed 's|^/||' ${info}/store-paths > paths.txt
    echo "packing $(wc -l < paths.txt) store paths"

    # The excludes have to precede --files-from: GNU tar applies them only to
    # names that come after them on the command line.
    #
    # --long=27 buys 6% over the same level with the default 8 MiB window,
    # because the closure repeats itself across store paths at distances a
    # small window cannot reach. It costs a 128 MiB decompression window,
    # which is exactly zstd's default memory ceiling, so the bundled 1.5.7
    # takes the frame without being asked for extra flags.
    tar --create \
        --directory=/ \
        ${lib.concatMapStringsSep " " (p: "--exclude='${p}'") deadWeight} \
        --files-from=paths.txt \
        --owner=0 --group=0 --numeric-owner \
        --sort=name \
        --mtime=@1 \
      | zstd -${toString zstdLevel} --long=27 -T0 -q -o store.tar.zst

    patch_size() {
      # Same width in, same width out; the header's length must not move.
      # Padded right, not left: a POSIX shell reads a leading zero as octal.
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
    run=$out/bin/cmrelay-installer-${version}-${arch}-linux.run
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
    ln -s cmrelay-installer-${version}-${arch}-linux.run $out/bin/cmrelay.run
  ''
