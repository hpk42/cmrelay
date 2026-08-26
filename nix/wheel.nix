# The wheel, with the payload inside it.
#
# `pip install cmrelay` is meant to be the last step that needs the network,
# so the .run file travels in the wheel rather than being fetched on first
# use. That makes the wheel per-architecture and roughly the size of the
# payload; the tags are what express which host it is for.
#
# The work is done by scripts/wheel-cmrelay.py, using nothing but the standard
# library, so CI can run the same script directly after `nix build` without
# a Python environment to set up. This derivation is the convenience wrapper
# for building one locally.
{
  lib,
  runCommand,
  python3,
  stdenv,
  payload,
  cmrelaySrc,
  # Overridden by CI, which knows the release tag. A local build is not a
  # release, and `.dev0` is what says so in a way pip will still install.
  version ? "0.0.0.dev0",
}:

let
  arch = if stdenv.hostPlatform.isAarch64 then "aarch64" else "x86_64";
in
runCommand "cmrelay-wheel-${version}"
  {
    nativeBuildInputs = [ python3 ];
    passthru = { inherit payload version arch; };
    meta.description = "cmrelay wheel carrying its own payload";
  }
  ''
    run=$(echo ${payload}/bin/cmrelay-*.run)
    mkdir -p $out
    python3 ${cmrelaySrc}/scripts/wheel-cmrelay.py \
      --payload "$run" \
      --arch ${arch} \
      --version ${lib.escapeShellArg version} \
      --root ${cmrelaySrc} \
      --outdir $out
    ls -l $out
  ''
