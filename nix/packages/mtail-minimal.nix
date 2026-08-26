# mtail, without the three development tools beside it.
#
# nixpkgs builds all four commands the mtail repository ships: mtail itself,
# plus mdot which renders a program's control-flow graph, mfmt which
# reformats one, and mgen which generates synthetic logs. A relay runs
# exactly one of them. The other three are 22 MiB of Go binary that every
# operator downloads and no relay executes.
#
# This copies rather than rebuilds, deliberately. Overriding subPackages
# would work and would mean compiling mtail here, which trades a cached,
# byte-identical upstream artefact for a local build that has to be trusted
# on its own. Copying keeps the binary nixpkgs published; Nix scans it for
# store references, so the discarded commands simply stop being reachable
# and the closure loses them.

{
  lib,
  runCommand,
  mtail,
}:

runCommand "mtail-${mtail.version}"
  {
    inherit (mtail) version;
    passthru = { unminimised = mtail; };
    meta = mtail.meta // {
      description = "${mtail.meta.description} (mtail only, without mdot, mfmt and mgen)";
    };
  }
  ''
    install -Dm755 ${lib.getExe mtail} $out/bin/mtail

    # A rename upstream would otherwise leave an empty bin/ and a relay whose
    # mtail unit fails to start.
    test -x $out/bin/mtail
  ''
