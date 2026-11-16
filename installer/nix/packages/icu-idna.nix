# ICU with only the IDNA data postfix reads for SMTPUTF8; stock ICU is 40 MiB.
# nixpkgs takes the prebuilt icudt*.dat verbatim, so icupkg trims it before configure.

{
  lib,
  icu,
  buildPackages,
}:

let
  keep = [
    "uts46.nrm"
    "nfkc.nrm"
    "nfkc_cf.nrm"
  ];
  icupkg = "${buildPackages.icu.dev}/bin/icupkg";
in
icu.overrideAttrs (old: {
  pname = "icu4c-idna";

  preConfigure = (old.preConfigure or "") + ''
    dat=$(ls data/in/icudt*.dat)
    echo "filtering $dat: $(${icupkg} -l "$dat" | wc -l) items"

    printf '%s\n' ${lib.escapeShellArgs keep} > keep.txt
    # by subtraction, so a new item is dropped; icupkg wants the .txt suffix
    ${icupkg} -l "$dat" | grep -vxF -f keep.txt > remove.txt

    ${icupkg} -r remove.txt --ignore-deps "$dat"

    remaining=$(${icupkg} -l "$dat")
    echo "kept: $(echo "$remaining" | tr '\n' ' ')"
    for item in ${lib.escapeShellArgs keep}; do
      echo "$remaining" | grep -qxF "$item" \
        || { echo "icu-idna: $item did not survive filtering" >&2; exit 1; }
    done
  '';

  # fail if a future ICU moves what UTS46 reads into a dropped item
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    cat > idna-check.c <<'EOF'
    #include <unicode/uidna.h>
    #include <unicode/ustring.h>
    #include <stdio.h>
    #include <string.h>

    /* Exactly the five entry points postfix's util/midna_domain.c calls. */
    int main(void) {
        UErrorCode e = U_ZERO_ERROR;
        UIDNA *idna = uidna_openUTS46(UIDNA_DEFAULT, &e);
        if (U_FAILURE(e)) {
            printf("uidna_openUTS46: %s\n", u_errorName(e));
            return 1;
        }
        /* Octal, not hex: a \x escape has no length limit in C, so
           "B\xc3\xbccher" reads the following 'c' as part of the escape. */
        static const char *const cases[][2] = {
            { "m\303\274nchen.de",           "xn--mnchen-3ya.de" },
            { "\344\276\213\343\201\210.jp", "xn--r8jz45g.jp" },
            { "B\303\274cher.example.org",   "xn--bcher-kva.example.org" },
            { "plain.example.com",           "plain.example.com" },
        };
        int bad = 0;
        for (unsigned k = 0; k < sizeof cases / sizeof *cases; k++) {
            char ascii[256];
            UIDNAInfo info = UIDNA_INFO_INITIALIZER;
            e = U_ZERO_ERROR;
            uidna_nameToASCII_UTF8(idna, cases[k][0], -1, ascii, sizeof ascii,
                                   &info, &e);
            if (U_FAILURE(e) || info.errors || strcmp(ascii, cases[k][1])) {
                printf("  %s -> %s, wanted %s (%s)\n", cases[k][0],
                       U_SUCCESS(e) ? ascii : "<error>", cases[k][1],
                       u_errorName(e));
                bad = 1;
                continue;
            }
            char back[256];
            UIDNAInfo rev = UIDNA_INFO_INITIALIZER;
            e = U_ZERO_ERROR;
            uidna_nameToUnicodeUTF8(idna, cases[k][1], -1, back, sizeof back,
                                    &rev, &e);
            if (U_FAILURE(e)) {
                printf("  %s does not map back: %s\n", cases[k][1],
                       u_errorName(e));
                bad = 1;
                continue;
            }
            printf("  %s <-> %s\n", cases[k][0], ascii);
        }
        uidna_close(idna);
        return bad;
    }
    EOF
    $CC idna-check.c -I"$dev/include" -L"$out/lib" \
        -licuuc -licui18n -licudata -Wl,-rpath,"$out/lib" -o idna-check
    ./idna-check || {
      echo "icu-idna: the filtered data no longer supports IDNA" >&2
      exit 1
    }
    runHook postInstallCheck
  '';
})
