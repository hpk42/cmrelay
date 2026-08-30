# ICU carrying only the data postfix reads, which is IDNA and nothing else.
#
# postfix is the sole reason ICU is in this closure, and it calls exactly five
# ICU functions: uidna_openUTS46, uidna_close, uidna_nameToASCII_UTF8,
# uidna_nameToUnicodeUTF8 and u_errorName. That is the whole of SMTPUTF8
# support -- turning an internationalized domain name into the A-label form
# that goes into DNS.
#
# Stock ICU answers that with 40 MiB, 32 of it libicudata: 4073 locale
# bundles, 190 charset converters, collation tables, time zone names,
# currencies and transliterators. A mail relay reads one item out of the 4305,
# uts46.nrm, and NFC comes compiled into libicuuc rather than from the archive.
#
# ICU's documented data filter (ICU_DATA_FILTER_FILE) is not the mechanism
# here, because it only applies when data is regenerated from source: nixpkgs
# builds ICU without Python, so the build takes the prebuilt icudt*.dat
# verbatim, and the sources tarball ships no locale data to regenerate from.
# What works instead is icupkg, ICU's own archive editor, run over that
# prebuilt .dat before configure.
#
# ICU stays so postfix keeps advertising SMTPUTF8, as a cmdeploy host does;
# only the data behind it goes.

{
  lib,
  icu,
  buildPackages,
}:

let
  # uts46.nrm is what UTS46 consults. The two NFKC tables are kept because
  # they are a few KB and normalization is the one area where being wrong is
  # silent.
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
    # Derived by subtraction rather than listed, so a version that renames or
    # adds an item drops it instead of quietly keeping it. The .txt suffix is
    # load-bearing: it is how icupkg tells a list file from an item name.
    ${icupkg} -l "$dat" | grep -vxF -f keep.txt > remove.txt

    # --ignore-deps because the resource bundles reference each other and
    # almost all of them are going; the dependency graph being unsatisfiable
    # is the intent, not an accident.
    ${icupkg} -r remove.txt --ignore-deps "$dat"

    remaining=$(${icupkg} -l "$dat")
    echo "kept: $(echo "$remaining" | tr '\n' ' ')"
    for item in ${lib.escapeShellArgs keep}; do
      echo "$remaining" | grep -qxF "$item" \
        || { echo "icu-idna: $item did not survive filtering" >&2; exit 1; }
    done
  '';

  # The point of the exercise, proven rather than assumed. If a future ICU
  # moves what UTS46 reads into an item the filter drops, postfix would still
  # link and start, and would fail only on the first mail from an
  # internationalized domain. That failure belongs at build time.
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
