# The binaries relay fetches rather than packages, fetched the same way.
#
# Nothing here is compiled. Each is a fetchurl with the sha256 relay already
# records, read out of nix/generated/relay-pins.json, which
# scripts/extract-from-relay parses out of relay's deployers. A relay bump
# therefore moves these versions with no edit here.
#
# Fetching rather than building means cmrelay ships byte-identical binaries
# to what deployed relays run today, and buys no exposure to whether a given
# iroh release still compiles against a current rustc.
#
# One subtlety decides the shape of this file. relay's download_executable()
# pipes the download through an extraction command *before* checking the
# sha256, so for the two components shipped as tarballs the recorded hash is
# of the extracted binary, not of the archive. Reproducing that exactly means
# making the extraction part of the fixed-output derivation, so the hash Nix
# verifies is the same number relay verifies. Hashing the tarball instead
# would need a second pin that relay does not have.

{
  lib,
  stdenvNoCC,
  fetchurl,
  system,
}:

let
  pins = (lib.importJSON ../generated/relay-pins.json).binary_pins;

  arch =
    {
      "x86_64-linux" = "x86_64";
      "aarch64-linux" = "aarch64";
    }
    .${system} or (throw "cmrelay does not support ${system}");

  pin =
    component:
    pins.${component}.${arch} or (throw "relay pins no ${component} binary for ${arch}");

  # A pinned upstream artefact, laid down as bin/<name>.
  #
  # tarballMember mirrors the `extract=` argument relay passes for the
  # components published as tarballs; the member name is the only thing here
  # relay's data does not carry, and a wrong one fails the hash check.
  fetched =
    {
      name,
      version,
      component,
      tarballMember ? null,
    }:
    let
      p = pin component;
      src = fetchurl (
        {
          inherit (p) url;
          sha256 = p.sha256;
        }
        // lib.optionalAttrs (tarballMember != null) {
          downloadToTemp = true;
          postFetch = ''
            gunzip < "$downloadedFile" | tar -xf - ${tarballMember} -O > "$out"
          '';
        }
      );
    in
    stdenvNoCC.mkDerivation {
      pname = name;
      inherit version src;
      dontUnpack = true;
      installPhase = ''
        install -Dm755 $src $out/bin/${name}
      '';
      meta = {
        description = "${name}, pinned by relay";
        platforms = [
          "x86_64-linux"
          "aarch64-linux"
        ];
      };
    };

  # Cosmetic, for `cmrelay --version`; the sha256 is what pins the artefact.
  versionOf =
    component: regex:
    let
      matched = builtins.match regex (pin component).url;
    in
    if matched == null then "unknown" else builtins.head matched;

  releaseVersion = ".*/download/v?([^/]+)/.*";

in
{
  filtermail = fetched {
    name = "filtermail";
    component = "filtermail";
    version = versionOf "filtermail" releaseVersion;
  };

  chatmail-turn = fetched {
    name = "chatmail-turn";
    component = "turn";
    version = versionOf "turn" releaseVersion;
  };

  iroh-relay = fetched {
    name = "iroh-relay";
    component = "iroh";
    version = versionOf "iroh" releaseVersion;
    tarballMember = "./iroh-relay";
  };

  # relay tracks a pinned mtail release rather than a distribution package,
  # and cmrelay follows the same pin so the mtail programs in
  # vendor/config/mtail are run by the version they were written against.
  mtail = fetched {
    name = "mtail";
    component = "mtail";
    version = versionOf "mtail" releaseVersion;
    tarballMember = "mtail";
  };
}
