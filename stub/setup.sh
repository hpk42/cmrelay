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

# Written last, immediately before the staging tree is published under its
# canonical name, and deleted by the CLI before it starts moving store paths
# out again. Its presence therefore means "complete and untouched". The name
# is shared with src/cmrelay/bundle.py.
ready=.cmrelay-ready

# The unpacked store is ~260 MB, and /tmp is a tmpfs on several of the target
# distributions. Stage on a real filesystem instead, and prefer one that
# usually shares a mount with /opt, so installing the closure is a rename
# rather than a copy.
parent=${CMRELAY_STAGE_DIR:-/var/tmp}
shared=$parent/cmrelay

# Staging is keyed by the closure's store hash, so a given payload unpacks
# once and later runs reuse it, and two different payloads never share a tree.
hash=${closure##*/}
hash=${hash%%-*}

stage=
# Non-empty only when $stage is the canonical name. A tree under any other
# name can never be found again, so it is scratch and always goes.
canonical=

# chmod before rm: store paths are r-xr-xr-x, so a non-root user running a
# verb that needs no root -- `--version`, `download` -- could not delete what
# it had just unpacked, and left 380 MiB behind while exiting non-zero for a
# command that had in fact succeeded.
# `if`, not `&&`: under `set -e` a bare `A && B` whose A is false makes the
# whole list fail, which would end the script rather than skip the branch.
discard() {
    if [ -z "${1:-}" ] || [ ! -d "$1" ]; then
        return 0
    fi
    chmod -R u+w "$1" 2>/dev/null || true
    rm -rf "$1" 2>/dev/null || true
}

cleanup() {
    if [ -n "${CMRELAY_KEEP_STAGE:-}" ] || [ -z "$stage" ]; then
        return 0
    fi
    # A canonical tree survives for the next run, but only while it is still
    # whole. `install` renames store paths out of it and drops the marker
    # before the first rename, so a marker-less tree is a husk.
    if [ -n "$canonical" ] && [ -f "$stage/$ready" ]; then
        return 0
    fi
    discard "$stage"
    return 0
}
trap cleanup EXIT INT TERM

# A canonical name under a world-writable /var/tmp is only safe if the
# directory is already ours and closed to everybody else. Without this an
# unprivileged user could pre-create it -- the hash is public, it is printed
# on the tin -- fill it with their own interpreter and bootstrap.env, and
# have root exec out of it. The sticky bit on /var/tmp is what stops them
# replacing the directory once it exists and is ours.
usable_dir() {
    if [ ! -d "$1" ] || [ -L "$1" ]; then
        return 1
    fi
    command -v stat >/dev/null 2>&1 || return 1
    [ "$(stat -c %u "$1" 2>/dev/null)" = "$(id -u)" ] || return 1
    [ "$(stat -c %a "$1" 2>/dev/null)" = "700" ] || return 1
    return 0
}

if mkdir "$shared" 2>/dev/null; then
    chmod 700 "$shared"
elif ! usable_dir "$shared"; then
    echo "cmrelay: $shared is not a private directory owned by this user," \
         "so this run unpacks to a throwaway staging directory" >&2
    shared=
fi

# One closure at a time. Everything here belongs to some payload, and only
# this payload's tree is of use to this run: older versions left by an
# upgrade, and scratch trees from runs that died before their EXIT trap could
# fire, are both unreachable and 255 MiB each. Without this the directory
# grows by a full closure on every version an operator ever runs.
#
# cmrelay is single-instance by nature -- two runs would be fighting over
# /opt/cmrelay, the unit files and the ports long before they got here -- so
# nothing here is expected to be in use by anyone else.
if [ -n "$shared" ]; then
    for old in "$shared"/*; do
        if [ "$old" != "$shared/$hash" ]; then
            discard "$old"
        fi
    done
fi

if [ -n "$shared" ] && [ -f "$shared/$hash/$ready" ]; then
    stage=$shared/$hash
    canonical=1
    echo "cmrelay: reusing the closure already unpacked in $stage"
else
    # A marker-less tree under the canonical name is a husk left by an
    # install that moved store paths out of it, or by a run killed between
    # unpacking and publishing. Nothing can use it, and leaving it would
    # block every future run from publishing, so it goes.
    if [ -n "$shared" ]; then
        discard "$shared/$hash"
    fi

    # Unpack somewhere private and publish by rename, so the canonical name
    # only ever appears once the tree behind it is whole. An interrupted run
    # leaves a scratch directory, never a half-populated store.
    if [ -n "$shared" ]; then
        scratch=$(mktemp -d "$shared/$hash.incoming.XXXXXXXX")
    else
        scratch=$(mktemp -d "$parent/cmrelay-stage.XXXXXXXX")
    fi
    [ -n "$scratch" ] || fail "cannot create a staging directory under $parent"

    # zstd travels with the payload because a bare Debian 12 has only tar and
    # gzip; assuming a decompressor on the host is exactly the sort of
    # dependency this design exists to avoid.
    chmod +x "$here/zstd" 2>/dev/null || true
    [ -x "$here/zstd" ] || fail \
        "the bundled zstd is not executable; is $here on a noexec mount?"

    echo "cmrelay: unpacking the closure into $scratch ..."
    # A pipeline reports only its last command, so a truncated download would
    # decompress partially, tar would succeed on what it got, and the install
    # would proceed against a store quietly missing files. zstd verifies its
    # own frame checksums, which makes its exit status the integrity check, so
    # carry it out of the pipeline by hand. POSIX sh has no PIPESTATUS.
    zstd_status="$scratch/.cmrelay-zstd-status"
    { "$here/zstd" -qdc "$here/store.tar.zst"; echo $? >"$zstd_status"; } \
        | tar -x -C "$scratch" \
        || fail "could not unpack the closure"
    read -r status <"$zstd_status" || status=unknown
    rm -f "$zstd_status"
    [ "$status" = 0 ] \
        || fail "the payload is corrupt or truncated (zstd exited $status)"

    [ -d "$scratch/nix/store" ] \
        || fail "the closure did not unpack to $scratch/nix/store"
    [ -f "$scratch$closure/bootstrap.env" ] \
        || fail "the unpacked closure has no bootstrap.env"

    : > "$scratch/$ready"

    if [ -z "$shared" ]; then
        stage=$scratch
    # -T is what keeps `mv` from moving the tree *inside* an existing
    # directory rather than failing. Where it is not supported the guard
    # ahead of the plain mv does the same job, with a race nobody can win
    # from outside a directory only this user may write to.
    elif mv -T "$scratch" "$shared/$hash" 2>/dev/null \
        || { [ ! -e "$shared/$hash" ] && mv "$scratch" "$shared/$hash" 2>/dev/null; }
    then
        stage=$shared/$hash
        canonical=1
    elif [ -f "$shared/$hash/$ready" ]; then
        # Another run published the identical tree between the discard above
        # and this rename. Theirs is ours by construction, so keep one.
        discard "$scratch"
        stage=$shared/$hash
        canonical=1
    else
        # Could not take the name. Run from our own copy, which cleanup will
        # remove, rather than leave an unreachable tree behind.
        stage=$scratch
    fi
fi

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
