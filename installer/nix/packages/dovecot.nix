{
  lib,
  stdenv,
  fetchFromGitHub,

  autoreconfHook,
  bison,
  flex,
  perl,
  pkg-config,
  quilt,
  rpcsvc-proto,

  bzip2,
  cyrus_sasl,
  inotify-tools,
  libcap,
  libsodium,
  libtirpc,
  libxcrypt,
  lua5_4,
  lz4,
  openssl,
  systemd,
  xz,
  zlib,
  zstd,
}:

stdenv.mkDerivation {
  pname = "dovecot";
  # as AC_INIT in configure.ac; the Debian revision is not part of it
  version = "2.3.21";

  src = fetchFromGitHub {
    owner = "chatmail";
    repo = "dovecot";
    tag = "upstream/2.3.21+dfsg1-3+chatmail3";
    hash = "sha256-kv82lu8pokyRFeGWSxCm1/qWEiMANoeLw7JpU20r0ow=";
  };

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
    systemd
  ];

  strictDeps = true;
  enableParallelBuilding = true;

  # The gbp tree keeps debian/patches unapplied; apply the series before autoreconf.
  # No --fuzz=0: quilt 0.69 in nixpkgs hangs on it.
  prePatch = ''
    export QUILT_PATCHES=debian/patches
    quilt --quiltrc /dev/null push -a
    expected=$(grep -cve '^ *#' -e '^ *$' debian/patches/series)
    test "$(wc -l < .pc/applied-patches)" = "$expected"
    rm -rf .pc
  '';

  postPatch = ''
    patchShebangs src/config/settings-get.pl

    # fail if the chatmail IDLE latency patch stopped applying
    ! grep -q NOTIFY_DELAY_MSECS src/lib-storage/mailbox-watch.c

    export systemdsystemunitdir=$out/etc/systemd/system
  '';

  # the m4 probe knows only lua5.3/5.1 pkg-config names, so this bypasses it
  env = {
    LUA_CFLAGS = "-I${lib.getDev lua5_4}/include";
    LUA_LIBS = "-L${lib.getLib lua5_4}/lib -llua -lm";
  };

  # debian/rules minus what chatmail does not deploy
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
    # passdb and userdb are lua and dict, so no PAM
    "--without-pam"
    "--without-libwrap"
    "--disable-static"
    "--disable-rpath"
    # chatmail's dovecot.conf assumes Debian's runtime layout
    "--with-rundir=/run/dovecot"
    "--with-moduledir=${placeholder "out"}/lib/dovecot/modules"
    "--libexecdir=${placeholder "out"}/lib"
    "--sysconfdir=/etc"
    "--localstatedir=/var"
  ]
  ++ lib.optional stdenv.hostPlatform.isLinux "--with-systemd";

  # sysconfdir is outside $out: stage the install and fold it back in
  installFlags = [ "DESTDIR=$(out)" ];

  postInstall = ''
    cp -r $out/$out/* $out
    rm -rf $out/$(echo "$out" | cut -d "/" -f2)

    # dovecot-core's static dh.pem, for ssl_dh
    install -Dm444 debian/dh.pem $out/share/dovecot/dh.pem
  '';

  # upstream's suite is long and flaky; cmlxc gates instead
  doCheck = false;

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
}
