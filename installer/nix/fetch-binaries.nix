# The binaries that are fetched rather than built, with the hashes from
# host/cmrelay/stack/pins.json. A hash is of the binary extracted from its
# tarball, so the extraction is part of the fixed-output derivation.

{
  lib,
  stdenvNoCC,
  fetchurl,
  binutils,
  arch,
}:

let
  pins = lib.importJSON ../../host/cmrelay/stack/pins.json;

  # a pin is a [url, sha256] list
  artifact = table: key: {
    url = builtins.elemAt pins.${table}.${key} 0;
    sha256 = builtins.elemAt pins.${table}.${key} 1;
  };

  pin = table: artifact table arch;

  # tarballMember is the one file to take out of a release tarball
  fetched =
    {
      name,
      version,
      pin,
      tarballMember ? null,
    }:
    let
      src = fetchurl (
        pin
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
    };

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

  filtermail-mtail = stdenvNoCC.mkDerivation {
    pname = "filtermail-mtail";
    version = version "FILTERMAIL_VERSION";
    src = fetchurl (artifact "FILTERMAIL_ARTIFACTS" "mtail");
    dontUnpack = true;
    installPhase = "install -Dm644 $src $out/filtermail.mtail";
  };
}
