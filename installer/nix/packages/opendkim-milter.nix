# opendkim without opendkim-genkey, and so without Perl (55 MiB).
# cmrelay makes the DKIM key with openssl.

{ opendkim }:

opendkim.overrideAttrs (_: {
  pname = "opendkim-milter";

  postInstall = "";

  # postFixup: bin/ is populated from sbin/ after postInstall
  postFixup = ''
    for tool in opendkim-genkey opendkim-genzone opendkim-testkey opendkim-testmsg; do
      rm -f "$out/bin/$tool" "$out/sbin/$tool"
      rm -f "$out/bin/.$tool-wrapped" "$out/sbin/.$tool-wrapped"
    done

    # a later nixpkgs might wrap the milter in a script
    if grep -rlI '/nix/store/[a-z0-9]\{32\}-perl' $out; then
      echo "opendkim-milter still reaches perl through the files above" >&2
      exit 1
    fi
    test -x $out/sbin/opendkim
  '';
})
