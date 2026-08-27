# bsdtar with the tar format and nothing else.
#
# Something has to unpack 8000 files, and a POSIX shell cannot, so the
# payload carries its own extractor rather than requiring the host's tar --
# which AlmaLinux's minimal image, among others, does not ship.
#
# As packaged, static bsdtar is 9.2 MiB, and almost none of that is tar. Its
# version string names the reason: zlib, liblzma, bz2lib, libzstd, openssl,
# libb2, and libxml2 behind XAR. cmrelay needs one format and zero filters,
# because the zstd sliced out beside this binary has already decompressed the
# stream before libarchive sees it. Dropping them takes it to 0.9 MiB, which
# is smaller than busybox and, unlike busybox, extracts correctly as a
# non-root user: nix store directories are mode 555, and busybox tar applies
# directory permissions as it goes rather than at the end, so it cannot
# create children inside a directory it has just made read-only.

{
  lib,
  libarchiveStatic,
}:

let
  # Each of these is a codec or a hash. None is reachable through the one
  # entry point cmrelay uses: bsdtar -x on an already-decompressed stream.
  dropped = [
    "openssl"
    "bz2lib"
    "lzma"
    "lzo2"
    "zlib"
    "zstd"
    "lz4"
    "xml2"
    "expat"
    "libb2"
    "iconv"
    "nettle"
  ];
in
libarchiveStatic.overrideAttrs (old: {
  pname = "bsdtar-minimal";

  configureFlags = (old.configureFlags or [ ]) ++ map (f: "--without-${f}") dropped ++ [
    "--disable-bsdcpio"
    "--disable-bsdcat"
    "--disable-bsdunzip"
  ];

  doCheck = false;

  # bsdtar prints what it was linked against, which makes the intent
  # checkable rather than merely stated. A libarchive bump that starts
  # finding one of these again would otherwise put several MiB back into
  # every payload without anybody noticing.
  postFixup = (old.postFixup or "") + ''
    banner=$($out/bin/bsdtar --version)
    echo "bsdtar-minimal: $banner"
    for codec in zlib liblzma bz2lib libzstd openssl lz4 xml2; do
      case "$banner" in
        *"$codec"*)
          echo "bsdtar-minimal: $codec came back into the build" >&2
          exit 1
          ;;
      esac
    done
    test -x $out/bin/bsdtar
  '';
})
