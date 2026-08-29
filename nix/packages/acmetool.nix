# acmetool 0.2.2, the exact version Debian bookworm packages as 0.2.2-1.
#
# Keeping acmetool rather than migrating lets cmrelay vendor its templates
# and units verbatim, and lets both share /var/lib/acme in place with no
# state-format skew and no re-issuance on rollback.
#
# Upstream never finished its Go modules migration, so the source tree has no
# go.mod. The two manifests beside this file are generated once:
#
#   go mod init git.devever.net/hlandau/acmetool
#   go mod edit -require=gopkg.in/alecthomas/kingpin.v2@v2.2.6
#   go mod tidy
#
# The kingpin pin is the non-obvious step: v2.4.0 renamed its module path, and
# v2.2.6 is the last tag matching the import path acmetool uses.

{
  lib,
  buildGoModule,
  fetchFromGitHub,
  pkg-config,
  libcap,
}:

buildGoModule {
  pname = "acmetool";
  version = "0.2.2";

  src = fetchFromGitHub {
    owner = "hlandau";
    repo = "acmetool";
    rev = "v0.2.2";
    hash = "sha256-r6lLOKmrsY/9mYUW/FRCllm217VcbMuVpB/iEybnQE4=";
  };

  postPatch = ''
    cp ${./acmetool-go.mod} go.mod
    cp ${./acmetool-go.sum} go.sum
  '';

  vendorHash = "sha256-R9yhgdzpOJfhooLSw/wRVThK8CpCxxYwViAPUqvaFn0=";
  subPackages = [ "cmd/acmetool" ];

  # acmetool drops capabilities after binding port 402, so the cgo build
  # needs libcap's headers.
  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ libcap ];

  # cgo on, deliberately. A static build looks tempting, but Go without cgo
  # cannot resolve account names through NSS, and acmetool then refuses
  # relay's own unit:
  #
  #   ExecStart=... redirector --service.uid=daemon --bind=127.0.0.1:402
  #   Error in service: because this binary was built without cgo, UIDs/GIDs
  #   must be specified numerically and not as names
  #
  # The distribution-independence a static build would buy is already there,
  # since the closure carries its own glibc.
  env.CGO_ENABLED = 1;

  # HTTP/1.1 for the module download: proxy.golang.org repeatedly cut the zips
  # short mid-stream with an HTTP/2 INTERNAL_ERROR. Inherited by
  # buildGoModule's fetch derivation and leaves vendorHash untouched.
  env.GODEBUG = "http2client=0";

  meta = {
    description = "ACME client (Let's Encrypt), the release Debian bookworm ships";
    license = lib.licenses.mit;
    mainProgram = "acmetool";
  };
}
