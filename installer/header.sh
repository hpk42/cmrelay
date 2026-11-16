#!/bin/sh
# The installer's header. bsdtar and the store archive follow at the offsets
# installer/nix/payload.nix patches in; a signature line after them is not read
# here. Extraction goes to $stage, which stands in for /opt/cmrelay;
# `cmrelay installer` publishes it by rename.

set -eu

# patched by payload.nix, at fixed width
CMRELAY_CLOSURE=@CLOSURE@
CMRELAY_HEADER_BYTES=0000000000
CMRELAY_TAR_BYTES=0000000000
CMRELAY_STORE_BYTES=0000000000
CMRELAY_UNPACKED_KB=0000000000

self=$0
case $self in
    /*) ;;
    *) self=$(cd "$(dirname "$self")" && pwd)/$(basename "$self") ;;
esac

fail() {
    echo "cmrelay: $*" >&2
    exit 1
}

usage() {
    echo "usage: $0"
    echo "Unpacks the chatmail relay stack into /opt/cmrelay and links the cmrelay command."
    echo "Then configure the host with: cmrelay init DOMAIN, and cmrelay deploy"
}

stage_only=
for arg in "$@"; do
    case $arg in
        --stage-only) stage_only=1 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; fail "the installer takes no arguments" ;;
    esac
done

[ "$(id -u)" = 0 ] || fail "the installer must run as root"

# A populated store is replaced by `cmrelay upgrade`, through --stage-only.
if [ -z "$stage_only" ] && [ -n "$(ls -A /opt/cmrelay/lib/store 2>/dev/null)" ]; then
    [ -f /opt/cmrelay/chatmail.ini ] \
        || fail "/opt/cmrelay holds the relay stack; next: cmrelay init DOMAIN"
    fail "/opt/cmrelay holds an installed cmrelay; to replace it: cmrelay upgrade $self"
fi

# Stage inside /opt/cmrelay so publishing is a rename.
opt_existed=1
[ -d /opt/cmrelay ] || opt_existed=
mkdir -p -m 700 /opt/cmrelay/lib/.staging || fail "cannot create /opt/cmrelay/lib/.staging"
stage=$(mktemp -d /opt/cmrelay/lib/.staging/XXXXXXXX) \
    || fail "cannot create a staging directory under /opt/cmrelay/lib/.staging"

cleanup() {
    chmod -R u+w "$stage" 2>/dev/null || true
    rm -rf "$stage"
    # rmdir: only a still empty /opt/cmrelay goes
    rmdir /opt/cmrelay/lib/.staging /opt/cmrelay/lib 2>/dev/null || true
    [ -n "$opt_existed" ] || rmdir /opt/cmrelay 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# a tenth more than the unpacked files; df -P has what is free in column four
needed_kb=$((CMRELAY_UNPACKED_KB * 11 / 10))
free_kb=$(df -Pk "$stage" | awk 'NR == 2 { print $4 }')
[ "$free_kb" -ge "$needed_kb" ] \
    || fail "/opt/cmrelay has $((free_kb / 1024)) MiB free, and unpacking needs $((needed_kb / 1024)) MiB"

# head before tail: the other way round tail reports a broken pipe
tar_at=$((CMRELAY_HEADER_BYTES + 1))
tar_end=$((CMRELAY_HEADER_BYTES + CMRELAY_TAR_BYTES))
store_at=$((tar_end + 1))
store_end=$((tar_end + CMRELAY_STORE_BYTES))

tar=$stage/tar
head -c "$tar_end" "$self" | tail -c "+$tar_at" >"$tar" \
    || fail "could not read the bundled tar out of $self"
chmod 755 "$tar"
"$tar" --version >/dev/null 2>&1 \
    || fail "the bundled tar will not run; is /opt/cmrelay on a noexec mount?"

echo "cmrelay: unpacking the closure into $stage ..." >&2
# bsdtar's status is the integrity check
head -c "$store_end" "$self" | tail -c "+$store_at" \
    | "$tar" -x -f - -C "$stage" \
    || fail "the payload is corrupt or truncated"
rm -f "$tar"
[ -f "$stage${CMRELAY_CLOSURE#/opt/cmrelay}/bootstrap.env" ] \
    || fail "the unpacked closure has no bootstrap.env"

if [ -n "$stage_only" ]; then
    trap - EXIT INT TERM
    echo "$stage"
    exit 0
fi

# from closure.nix: where the interpreter and its modules are
. "$stage${CMRELAY_CLOSURE#/opt/cmrelay}/bootstrap.env"

ld="$stage${CMRELAY_LD#/opt/cmrelay}"
python="$stage${CMRELAY_PYTHON#/opt/cmrelay}"
[ -f "$ld" ] || fail "no dynamic loader at $ld"
[ -f "$python" ] || fail "no interpreter at $python"

libs=$(ls -d "$stage"/lib/store/*/lib 2>/dev/null | tr '\n' ':')

CMRELAY_STAGE="$stage"
export CMRELAY_STAGE CMRELAY_CLOSURE
PYTHONPATH=$(printf '%s:%s' "$CMRELAY_CLOSURE" "$CMRELAY_SITE" \
    | sed "s|/opt/cmrelay/|$stage/|g")
export PYTHONPATH
PYTHONNOUSERSITE=1
export PYTHONNOUSERSITE

# Not exec: the EXIT trap removes the stage. The explicit exit keeps the shell
# from reading on into the bundled tar.
status=0
"$ld" --library-path "$libs" "$python" -c 'from cmrelay.cli.main import main; raise SystemExit(main())' installer || status=$?
exit "$status"
