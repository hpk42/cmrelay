#!/bin/sh
# The launcher makeself runs after unpacking, and the only part of cmrelay
# that is not Python.
#
# The premise is a single file copied to a fresh host, but a Python CLI needs
# an interpreter and the interpreter lives inside the payload. That is
# circular, and resolving it is all this script does.
#
# It does as little as possible: unpack the store into a staging directory,
# then exec the *bundled* interpreter, never the host's, so behaviour is
# pinned by flake.lock rather than by whatever the distribution ships. A bare
# Debian container has no python3 at all, which is the case this exists for.
#
# Ordering is what preserves the install guarantee. Extraction goes to
# staging first, the preflight runs from there, and only after it passes does
# anything touch /nix/store or /opt/cmrelay. A temporary directory is not the
# system, so "modified nothing at all" stays literally true.
#
# The one trick: a store binary names its ELF interpreter as an absolute
# /nix/store/... path that does not exist yet, so it cannot simply be
# executed. Invoking ld.so directly with the target as an argument bypasses
# PT_INTERP, and --library-path takes precedence over the DT_RUNPATH nix
# writes, so the staged interpreter resolves its own libraries out of
# staging.

set -eu

here=$(cd "$(dirname "$0")" && pwd)

fail() {
    echo "cmrelay: $*" >&2
    exit 1
}

[ -f "$here/store.tar.zst" ] || fail "payload is incomplete: no store.tar.zst"
[ -f "$here/closure" ] || fail "payload is incomplete: no closure marker"

closure=$(cat "$here/closure")

# The unpacked store is ~650 MB, and /tmp is a tmpfs on several of the target
# distributions. Stage on a real filesystem instead, and prefer one that
# usually shares a mount with /opt, so installing the closure is a rename
# rather than a copy.
# mktemp, not a name built from $PID: /var/tmp is world writable, a pid is
# guessable, and `mkdir -p` succeeds on a directory somebody else already
# created. Root would then unpack a closure into a tree an unprivileged user
# owns and execute out of it.
stage=$(mktemp -d "${CMRELAY_STAGE_DIR:-/var/tmp}/cmrelay-stage.XXXXXXXX") \
    || fail "cannot create a staging directory under ${CMRELAY_STAGE_DIR:-/var/tmp}"

cleanup() {
    # A refusal removes the staging directory and exits, having touched
    # nothing else.
    [ -n "${CMRELAY_KEEP_STAGE:-}" ] || rm -rf "$stage"
}
trap cleanup EXIT INT TERM

# zstd travels with the payload because a bare Debian 12 has only tar and
# gzip; assuming a decompressor on the host is exactly the sort of dependency
# this design exists to avoid.
chmod +x "$here/zstd" 2>/dev/null || true
[ -x "$here/zstd" ] || fail \
    "the bundled zstd is not executable; is $here on a noexec mount?"

echo "cmrelay: unpacking the closure into $stage ..."
# A pipeline reports only its last command, so a truncated download would
# decompress partially, tar would succeed on what it got, and the install
# would proceed against a store quietly missing files. zstd verifies its own
# frame checksums, which makes its exit status the integrity check, so carry
# it out of the pipeline by hand. POSIX sh has no PIPESTATUS.
zstd_status="$stage/.cmrelay-zstd-status"
{ "$here/zstd" -qdc "$here/store.tar.zst"; echo $? >"$zstd_status"; } \
    | tar -x -C "$stage" \
    || fail "could not unpack the closure"
read -r status <"$zstd_status" || status=unknown
rm -f "$zstd_status"
[ "$status" = 0 ] \
    || fail "the payload is corrupt or truncated (zstd exited $status)"

[ -d "$stage/nix/store" ] || fail "the closure did not unpack to $stage/nix/store"

# Written by nix/closure.nix, because neither piece can be found by
# globbing. python3.withPackages produces an env whose bin/python3 is a
# wrapper that execs an absolute store path, so during staging only the raw
# interpreter is runnable; and the env's site-packages is a symlink farm
# pointing at absolute store paths that do not exist yet, so CMRELAY_SITE
# names each module's own store path instead.
. "$stage$closure/bootstrap.env"

ld="$stage$CMRELAY_LD"
python="$stage$CMRELAY_PYTHON"
[ -f "$ld" ] || fail "no dynamic loader at $ld"
[ -f "$python" ] || fail "no interpreter at $python"

libs=$(ls -d "$stage"/nix/store/*/lib 2>/dev/null | tr '\n' ':')

CMRELAY_STAGE="$stage"
CMRELAY_CLOSURE="$closure"
export CMRELAY_STAGE CMRELAY_CLOSURE
# CMRELAY_SITE is a colon-separated list of store paths; every one of them
# needs the staging prefix in front of it.
staged_site=$(printf '%s' "$CMRELAY_SITE" | sed "s|:|:$stage|g")
PYTHONPATH="$stage$closure:$stage$staged_site"
export PYTHONPATH
# Nothing in the payload should ever pick up a host module by accident.
PYTHONNOUSERSITE=1
export PYTHONNOUSERSITE

# The CLI installs the store out of $stage and re-execs itself from the
# installed copy, so this process must not hold the staging tree open.
"$ld" --library-path "$libs" "$python" -m cmrelay "$@"
