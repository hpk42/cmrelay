# acmetool 0.2.2 as in Debian bookworm, so /var/lib/acme is shared with a
# cmdeploy relay. Upstream has no go.mod; the two manifests beside this file
# come from `go mod init`, `go mod edit -require=gopkg.in/alecthomas/kingpin.v2@v2.2.6`
# and `go mod tidy`.

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

  # for dropping capabilities after binding port 402
  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ libcap ];

  # cgo: without it acmetool cannot resolve the account names in its unit.
  env.CGO_ENABLED = 1;

  ldflags = [
    "-s"
    "-w"
  ];

  # proxy.golang.org cut module zips short over HTTP/2
  env.GODEBUG = "http2client=0";

  meta = {
    description = "ACME client (Let's Encrypt), the release Debian bookworm ships";
    license = lib.licenses.mit;
    mainProgram = "acmetool";
  };
}
