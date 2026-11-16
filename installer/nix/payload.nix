# The installer file: [ header ][ bsdtar ][ store.tar.zst ][ signature ]
# The signature line is appended later, by scripts/3_sign-installer.
# Offsets are patched into fixed-width fields, so the header keeps its size.

{
  lib,
  arch,
  runCommand,
  zstd,
  gnutar,
  python3,
  closureInfo,
  closure,
  bsdtar-minimal,
  version,
}:

let
  info = closureInfo { rootPaths = [ closure ]; };
  # Files a cmrelay never reads. libtool .la files stay: dovecot reads them.
  deadWeight = [
    "*.a" # static libraries, 63 MiB of it libpython
    "*/include/*" # C headers
    "*/share/doc/*"
    "*/share/*/doc/*" # postfix keeps its 5.4 MiB of it one level deeper
    "*/share/man/*"
    "*/share/locale/*" # gettext catalogues; a cmrelay logs in English
    # share/terminfo and share/zoneinfo stay: readline, python, acmetool.
    "*/share/i18n/*"
    "*/lib/${python3.libPrefix}/test/*" # CPython's own test suite
    "*/idlelib/*" # the Tk IDE
    "*/lib/${python3.libPrefix}/tkinter/*" # no Tk in the closure to import
    "*/lib/${python3.libPrefix}/ensurepip/*" # bundled wheels; nothing here makes a venv
    "*/lib/${python3.libPrefix}/pydoc_data/*" # help() topic text
    "*/pkgconfig/*"
    "*/__pycache__/*" # root's first run writes them again
    # gcc runtimes no DT_NEEDED of the closure names
    "*/lib/lib*san.so*" # asan, tsan, hwasan, lsan, ubsan
    "*/lib/libgomp.so*"
    "*/lib/libquadmath.so*"
    "*/lib/libitm.so*"
    "*/lib/libatomic.so*"
    "*/lib/libssp.so*"
    # postfix uses db's C API only
    "*/lib/libdb_cxx*"
  ];
in
runCommand "cmrelay-${version}"
  {
    nativeBuildInputs = [
      zstd
      gnutar
      python3
    ];
    meta.description = "self-extracting chatmail relay installer";
  }
  ''
    sed 's|^/||' ${info}/store-paths > paths.txt
    echo "packing $(wc -l < paths.txt) store paths"

    # GNU tar applies excludes only to names that come after them.
    mkdir tree
    tar --create \
        --directory=/ \
        ${lib.concatMapStringsSep " " (p: "--exclude='${p}'") deadWeight} \
        --files-from=paths.txt \
      | tar --extract --directory=tree
    chmod -R u+w tree
    # dovecot uses glibc's charset converters: the Unicode ones and the
    # common Western legacy ones stay, 2_test-distros checks them
    find tree/nix/store -path '*/lib/gconv/*.so' ! -name 'UTF-*' ! -name UNICODE.so \
        ! -name ISO8859-1.so ! -name ISO8859-15.so ! -name CP1252.so -delete
    relocated=$(python3 ${./relocate.py} tree/nix/store ${closure})
    mkdir tree/lib
    mv tree/nix/store tree/lib/store
    rmdir tree/nix
    chmod -R a-w tree/lib/store

    # The closure path changes the header's length: substitute first, then measure.
    sed "s|@CLOSURE@|$relocated|" ${../header.sh} > header.sh

    # uncompressed: nothing exists yet that could unpack it
    cp ${bsdtar-minimal}/bin/bsdtar tar

    (cd tree && find lib/store -mindepth 1 -maxdepth 1 | sort) > relocated.txt

    # --long=27 saves 6% within libzstd's default memory ceiling.
    # --hard-dereference: same archive on a builder with auto-optimise-store.
    tar --create \
        --directory=tree \
        --hard-dereference \
        --files-from=relocated.txt \
        --owner=0 --group=0 --numeric-owner \
        --sort=name \
        --mtime=@1 \
      | zstd -19 --long=27 -T0 -q -o store.tar.zst

    patch_size() {
      # padded right: a shell reads a leading zero as octal
      value=$(printf '%-10s' "$2")
      sed -i "s|^$1=0\{10\}$|$1=$value|" header.sh
      grep -q "^$1=$2 *$" header.sh || {
        echo "payload: could not patch $1 into the header" >&2
        exit 1
      }
    }

    patch_size CMRELAY_HEADER_BYTES "$(stat -c%s header.sh)"
    patch_size CMRELAY_TAR_BYTES    "$(stat -c%s tar)"
    patch_size CMRELAY_STORE_BYTES  "$(stat -c%s store.tar.zst)"
    # the files' own sizes: no builder's filesystem changes them
    patch_size CMRELAY_UNPACKED_KB \
      "$(find tree/lib/store -type f -printf '%s\n' | awk '{ s += $1 } END { print int(s / 1024) }')"

    mkdir -p $out/bin
    run=$out/bin/cmrelay-installer-${version}-${arch}-linux.run
    cat header.sh tar store.tar.zst > $run
    chmod 755 $run

    recorded=$(sed -n 's|^CMRELAY_HEADER_BYTES=\([0-9]*\) *$|\1|p' header.sh)
    actual=$(stat -c%s header.sh)
    [ "$recorded" = "$actual" ] || {
      echo "payload: header is $actual bytes but records $recorded" >&2
      exit 1
    }

    ls -l $out/bin
    ln -s cmrelay-installer-${version}-${arch}-linux.run $out/bin/cmrelay.run
  ''
