# bsdtar with tar and zstd only, 1.7 MiB static, so the host needs no tar.
# busybox tar cannot extract mode-555 store directories as non-root.

{ libarchiveStatic }:

let
  dropped = [
    "openssl"
    "bz2lib"
    "lzma"
    "lzo2"
    "zlib"
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

  configureFlags =
    (old.configureFlags or [ ])
    ++ map (f: "--without-${f}") dropped
    ++ [
      "--disable-bsdcpio"
      "--disable-bsdcat"
      "--disable-bsdunzip"
    ];

  doCheck = false;

  # fail if a libarchive bump brings a codec back
  postFixup = (old.postFixup or "") + ''
    banner=$($out/bin/bsdtar --version)
    echo "bsdtar-minimal: $banner"
    for codec in zlib liblzma bz2lib openssl lz4 xml2; do
      case "$banner" in
        *"$codec"*)
          echo "bsdtar-minimal: $codec came back into the build" >&2
          exit 1
          ;;
      esac
    done
  '';
})
