# The binaries relay fetches rather than packages, fetched the same way.
#
# Nothing here is compiled. Each is a fetchurl with the sha256 relay already
# records, read out of nix/generated/relay-pins.json, which
# scripts/extract-from-relay parses out of relay's deployers. A relay bump
# therefore moves these versions with no edit here.
#
# Fetching rather than building buys no exposure to whether a given iroh
# release still compiles against a current rustc. The install strips each
# binary: that happens after the fixed-output hash check, so the pin still
# proves the download is exactly what relay records, and the shipped bytes
# differ from a cmdeploy relay's by the stripping alone. iroh-relay and
# chatmail-turn publish unstripped releases, together 450 KB of installer.
#
# One subtlety decides the shape of this file. relay's download_executable()
# pipes the download through an extraction command *before* checking the
# sha256, so for a component shipped as a tarball the recorded hash is of the
# extracted binary, not of the archive. Reproducing that exactly means making
# the extraction part of the fixed-output derivation, so the hash Nix
# verifies is the same number relay verifies.
#
# mtail is absent: the operator installs it, and the preflight prints the
# release relay pins. See cmrelay.preflight.check_mtail.

{
  lib,
  stdenvNoCC,
  fetchurl,
  binutils,
  system,
}:

let
  pins = (lib.importJSON ../generated/relay-pins.json).pins;

  arch =
    {
      "x86_64-linux" = "x86_64";
      "aarch64-linux" = "aarch64";
    }
    .${system} or (throw "cmrelay does not support ${system}");

  # relay writes each artefact as (url, sha256); JSON makes that a list.
  artifact =
    table: key:
    let
      entry = pins.${table}.${key} or (throw "relay pins no ${table}.${key}");
    in
    {
      url = builtins.elemAt entry 0;
      sha256 = builtins.elemAt entry 1;
    };

  pin = table: artifact table arch;

  # A pinned upstream artefact, laid down as bin/<name>.
  #
  # tarballMember mirrors the `extract=` argument relay passes for the
  # components published as tarballs; the member name is the only thing here
  # relay's data does not carry, and a wrong one fails the hash check.
  fetched =
    {
      name,
      version,
      pin,
      tarballMember ? null,
    }:
    let
      p = pin;
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
        ${binutils}/bin/strip $out/bin/${name}
      '';
      meta = {
        description = "${name}, pinned by relay";
        platforms = [
          "x86_64-linux"
          "aarch64-linux"
        ];
      };
    };

  # relay states each version beside its artefacts, so nothing has to be
  # recovered from a URL.
  version = name: lib.removePrefix "v" (pins.${name} or "unknown");

in
{
  filtermail = fetched {
    name = "filtermail";
    pin = pin "FILTERMAIL_ARTIFACTS";
    version = version "FILTERMAIL_VERSION";
  };

  chatmail-turn = fetched {
    name = "chatmail-turn";
    pin = pin "TURN_ARTIFACTS";
    version = version "TURN_VERSION";
  };

  iroh-relay = fetched {
    name = "iroh-relay";
    pin = pin "IROH_ARTIFACTS";
    version = version "IROH_VERSION";
    tarballMember = "./iroh-relay";
  };

  # An mtail program rather than a binary: relay fetches it from the
  # filtermail release it pins, so filtermail's own metrics are monitored by
  # the rules that shipped with it.
  filtermail-mtail = stdenvNoCC.mkDerivation {
    pname = "filtermail-mtail";
    version = version "FILTERMAIL_VERSION";
    src = fetchurl (artifact "FILTERMAIL_ARTIFACTS" "mtail");
    dontUnpack = true;
    installPhase = "install -Dm644 $src $out/filtermail.mtail";
  };
}
