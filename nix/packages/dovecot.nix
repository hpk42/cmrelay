{
  lib,
  stdenv,
  fetchFromGitHub,

  # build tools
  autoreconfHook,
  bison,
  flex,
  perl,
  pkg-config,
  quilt,
  rpcsvc-proto,

  # runtime libraries
  bzip2,
  coreutils,
  cyrus_sasl,
  inotify-tools,
  libcap,
  libsodium,
  libtirpc,
  libxcrypt,
  lua5_4,
  lz4,
  openssl,
  pam,
  systemd,
  xz,
  zlib,
  zstd,

  # override point: swap in a local checkout while iterating
  srcOverride ? null,
}:

let
  # chatmail/dovecot, tag upstream/2.3.21+dfsg1-3+chatmail2.
  # Identical to origin/master outside .github/ as of 2026-08-25.
  rev = "a59f4f4f4fdf6bd19c9d04d42d3cc5d2dde93a82";
  defaultSrc = fetchFromGitHub {
    owner = "chatmail";
    repo = "dovecot";
    inherit rev;
    hash = "sha256-YQS1HVvgAOvYEnj9cdRYq2/GZWfkn/EsWNW94RhdS7I=";
  };
in
stdenv.mkDerivation (finalAttrs: {
  pname = "dovecot";
  # AC_INIT([Dovecot],[2.3.21]) in configure.ac; the Debian revision
  # (+dfsg1-3+chatmail2) is packaging metadata and not part of it.
  version = "2.3.21";

  src = if srcOverride != null then srcOverride else defaultSrc;

  outputs = [ "out" ];

  nativeBuildInputs = [
    autoreconfHook
    bison
    flex
    perl
    pkg-config
    quilt
  ]
  ++ lib.optional stdenv.hostPlatform.isLinux rpcsvc-proto;

  buildInputs = [
    bzip2
    cyrus_sasl.dev
    libsodium
    libxcrypt
    lua5_4
    lz4
    openssl
    xz
    zlib
    zstd
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    inotify-tools
    libcap
    libtirpc
    pam
    systemd
  ];

  strictDeps = true;
  enableParallelBuilding = true;

  # The gbp tree keeps debian/patches unapplied, so apply the quilt series
  # before autoreconf: four of the 53 patches touch configure.ac, m4/*.m4 or
  # Makefile.am and only take effect once configure is regenerated.
  #
  # Do not add --fuzz=0: quilt 0.69 in nixpkgs falls back to its bundled
  # compat/getopt, which mis-parses that option and then hangs forever
  # instead of applying anything. `quilt push -a` already fails on a reject,
  # and the count check below fails on a short series.
  prePatch = ''
    export QUILT_PATCHES=debian/patches
    quilt --quiltrc /dev/null push -a
    expected=$(grep -cve '^ *#' -e '^ *$' debian/patches/series)
    test "$(wc -l < .pc/applied-patches)" = "$expected"
    rm -rf .pc
  '';

  postPatch = ''
    sed -i -E \
      -e 's!/bin/sh\b!${stdenv.shell}!g' \
      -e 's!([^[:alnum:]/_-])/bin/([[:alnum:]]+)\b!\1${coreutils}/bin/\2!g' \
      -e 's!([^[:alnum:]/_-])(head|sleep|cat)\b!\1${coreutils}/bin/\2!g' \
      src/lib-program-client/test-program-client-local.c

    patchShebangs src/lib-smtp/test-bin/*.sh
    sed -i -s -E 's!\bcat\b!${coreutils}/bin/cat!g' src/lib-smtp/test-bin/*.sh

    patchShebangs src/config/settings-get.pl

    # DES-encrypted passwords are not supported by nixpkgs' libxcrypt
    sed '/test_password_scheme("CRYPT"/d' -i src/auth/test-libpassword.c

    # Fail loudly if the chatmail IDLE latency patch stopped applying.
    ! grep -q NOTIFY_DELAY_MSECS src/lib-storage/mailbox-watch.c

    export systemdsystemunitdir=$out/etc/systemd/system
  '';

  # The m4 probe only looks for lua5.3/5.1 pkg-config names and silently
  # leaves LUA_LIBS empty otherwise, which fails much later at link time.
  # Setting these bypasses the probe (m4/want_lua.m4).
  env = {
    LUA_CFLAGS = "-I${lib.getDev lua5_4}/include";
    LUA_LIBS = "-L${lib.getLib lua5_4}/lib -llua -lm";
  };

  # Mirrors debian/rules override_dh_auto_configure minus everything chatmail
  # does not deploy: no ldap/sql/gssapi/solr/libwrap/apparmor/icu/lucene.
  configureFlags = [
    "--with-ssl=openssl"
    "--with-ioloop=best"
    "--with-lua=plugin"
    "--with-sodium"
    "--with-zlib"
    "--with-bzlib"
    "--with-lz4"
    "--with-zstd"
    "--without-icu"
    "--without-lucene"
    "--without-textcat"
    "--without-stemmer"
    "--without-libwrap"
    "--disable-static"
    "--disable-rpath"
    # Debian's runtime layout, which chatmail's dovecot.conf assumes.
    "--with-rundir=/run/dovecot"
    "--with-moduledir=${placeholder "out"}/lib/dovecot/modules"
    "--libexecdir=${placeholder "out"}/lib"
    # Reading /etc/dovecot/dovecot.conf by default keeps `doveconf -n` and
    # `dovecot -n` usable without -c, exactly as the Debian package does.
    "--sysconfdir=/etc"
    "--localstatedir=/var"
  ]
  ++ lib.optional stdenv.hostPlatform.isLinux "--with-systemd";

  # sysconfdir is outside $out, so stage the install and fold it back in.
  installFlags = [ "DESTDIR=$(out)" ];

  postInstall = ''
    cp -r $out/$out/* $out
    rm -rf $out/$(echo "$out" | cut -d "/" -f2)

    # chatmail's dovecot.conf sets ssl_dh = </usr/share/dovecot/dh.pem, which
    # is a static file shipped by dovecot-core, not something generated on the
    # host. Carry it so cmrelay can point ssl_dh at the store copy.
    install -Dm444 debian/dh.pem $out/share/dovecot/dh.pem
  '';

  # Upstream's suite is long and has known-flaky cases the Debian series only
  # partly disables; cmrelay gates on the cmlxc functional suite instead.
  doCheck = false;

  passthru = { inherit rev; };

  meta = {
    homepage = "https://github.com/chatmail/dovecot";
    description = "chatmail's Debian rebuild of Dovecot 2.3.21 with the IMAP IDLE latency patch and 26 CVE backports";
    license = with lib.licenses; [
      mit
      lgpl21Only
    ];
    mainProgram = "dovecot";
    platforms = lib.platforms.linux;
  };
})
