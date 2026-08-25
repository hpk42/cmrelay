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

    tar --create \
        --directory=/ \
        --files-from=paths.txt \
        --owner=0 --group=0 --numeric-owner \
        --sort=name \
        --mtime=@1 \
      | zstd -${toString zstdLevel} -T0 -q -o archive/store.tar.zst

    ls -l archive/

    mkdir -p $out/bin
    makeself \
      --nomd5 --nocrc --nocomp \
      --tar-extra "--owner=0 --group=0 --numeric-owner" \
      archive \
      $out/bin/cmrelay-${version}.run \
      "cmrelay ${version}: chatmail relay installer" \
      ./setup.sh

    ln -s cmrelay-${version}.run $out/bin/cmrelay.run
  ''
