# opendkim without its key-generation script, and so without Perl.
#
# A relay runs the milter. `opendkim-genkey` is a Perl script, run once per
# host to make the signing keypair, and it is the only thing in the whole
# closure that reaches Perl: 55 MiB carried by every operator for a single
# invocation. cmrelay makes the key with openssl instead, which is already
# in the closure and is what relay's own DNS tooling uses to read the key
# back out again.
#
# nixpkgs wraps `opendkim-genkey` in its postInstall to put openssl on its
# PATH, and that wrapper is the one file in the output that names Perl. So
# this replaces the postInstall rather than adding to it: appending a
# deletion would remove the script but leave the reference that dragged Perl
# in. The three diagnostic tools go with it, none being on any cmrelay path.
#
# When DKIM signing moves into filtermail, this file and opendkim both go.

{ opendkim }:

opendkim.overrideAttrs (_: {
  pname = "opendkim-milter";

  # Replaces nixpkgs' `wrapProgram $out/sbin/opendkim-genkey`.
  postInstall = "";

  # postFixup, because the generic builder populates bin/ from sbin/ after
  # postInstall has run.
  postFixup = ''
    for tool in opendkim-genkey opendkim-genzone opendkim-testkey opendkim-testmsg; do
      rm -f "$out/bin/$tool" "$out/sbin/$tool"
      rm -f "$out/bin/.$tool-wrapped" "$out/sbin/.$tool-wrapped"
    done
    rm -rf $out/share/doc $out/include $out/lib/pkgconfig

    # The point of the exercise, asserted rather than assumed: a later
    # nixpkgs might wrap the milter itself in something scripted.
    if grep -rlI '/nix/store/[a-z0-9]\{32\}-perl' $out; then
      echo "opendkim-milter still reaches perl through the files above" >&2
      exit 1
    fi
    test -x $out/sbin/opendkim
  '';
})
