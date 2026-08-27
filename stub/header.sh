#!/bin/sh
# The whole of cmrelay that is not Python: a shell header with a store
# archive, a zstd and a tar bolted onto the end of it.
#
# The premise is a single file copied to a fresh host, but a Python CLI needs
# an interpreter, and the interpreter is inside the payload. That is circular,
# and resolving it is all this script does.
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
# Two tricks make it work.
#
# The file is its own archive. Everything after this header is binary, at
# offsets nix/payload.nix computes and patches into the fixed-width fields
# below, and `head -c END | tail -c +START` slices a piece out. That is why
# there is no makeself here: its stub unpacks with the host's tar before
# any of our code runs, so a host without tar -- AlmaLinux's minimal image,
# among others -- fails with "Extraction failed" and no way for cmrelay to
# say why. Every tool this needs instead is coreutils, which those hosts do
# have. Owning the format also means the offsets are ours, which is what a
# signature trailer would need.
#
# The other: a store binary names its ELF interpreter as an absolute
# /nix/store/... path that does not exist yet, so it cannot simply be
# executed. Invoking ld.so directly with the target as an argument bypasses
# PT_INTERP, and --library-path takes precedence over the DT_RUNPATH nix
# writes, so the staged interpreter resolves its own libraries out of staging.

set -eu

# Patched by nix/payload.nix. The widths are fixed so that patching cannot
# change the size of the header the offsets are measured from.
CMRELAY_CLOSURE=@CLOSURE@
CMRELAY_HEADER_BYTES=0000000000
CMRELAY_ZSTD_BYTES=0000000000
CMRELAY_TAR_BYTES=0000000000
CMRELAY_STORE_BYTES=0000000000

self=$0
case $self in
    /*) ;;
    *) self=$(cd "$(dirname "$self")" && pwd)/$(basename "$self") ;;
esac

fail() {
    echo "cmrelay: $*" >&2
    exit 1
}

# `cmrelay.run --quiet -- install ...` is how the wheel and cmlxc invoke
# this, from when a makeself stub was parsing those. Both are accepted and
# ignored rather than passed to the CLI, which knows neither.
while [ $# -gt 0 ]; do
    case $1 in
        --quiet) shift ;;
        --) shift; break ;;
        *) break ;;
    esac
done

ready=.cmrelay-ready

parent=${CMRELAY_STAGE_DIR:-/var/tmp}
shared=$parent/cmrelay

hash=${CMRELAY_CLOSURE##*/}
hash=${hash%%-*}

stage=
canonical=
tools=

discard() {
    if [ -z "${1:-}" ] || [ ! -d "$1" ]; then
        return 0
    fi
    chmod -R u+w "$1" 2>/dev/null || true
    rm -rf "$1" 2>/dev/null || true
}

cleanup() {
    discard "$tools"
    if [ -n "${CMRELAY_KEEP_STAGE:-}" ] || [ -z "$stage" ]; then
        return 0
    fi
    if [ -n "$canonical" ] && [ -f "$stage/$ready" ]; then
        return 0
    fi
    discard "$stage"
    return 0
}
trap cleanup EXIT INT TERM

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
    if [ -n "$shared" ]; then
        discard "$shared/$hash"
        scratch=$(mktemp -d "$shared/$hash.incoming.XXXXXXXX")
    else
        scratch=$(mktemp -d "$parent/cmrelay-stage.XXXXXXXX")
    fi
    [ -n "$scratch" ] || fail "cannot create a staging directory under $parent"
    stage=$scratch

    # The tools live apart from the staging tree, because that tree is
    # published by rename and must contain nothing but the store.
    tools=$(mktemp -d "${TMPDIR:-/tmp}/cmrelay-tools.XXXXXXXX") \
        || fail "cannot create a directory for the bundled tools"

    # head first, then tail. The other way round -- tail to the offset, head
    # for the length -- has head close the pipe while tail is still streaming
    # the remaining 60 MiB, and tail says so on stderr:
    #
    #     tail: error writing 'standard output': Broken pipe
    #
    # which is harmless and reads exactly like a failure. This way head stops
    # at a byte count of a regular file and tail sees an ordinary EOF.
    zstd_at=$((CMRELAY_HEADER_BYTES + 1))
    zstd_end=$((CMRELAY_HEADER_BYTES + CMRELAY_ZSTD_BYTES))
    tar_at=$((zstd_end + 1))
    tar_end=$((zstd_end + CMRELAY_TAR_BYTES))
    store_at=$((tar_end + 1))
    store_end=$((tar_end + CMRELAY_STORE_BYTES))

    # zstd is stored uncompressed; nothing exists yet that could unpack it.
    head -c "$zstd_end" "$self" | tail -c "+$zstd_at" >"$tools/zstd" \
        || fail "could not read the bundled zstd out of $self"
    chmod 755 "$tools/zstd"
    [ -s "$tools/zstd" ] || fail "the bundled zstd is empty"
    "$tools/zstd" --version >/dev/null 2>&1 \
        || fail "the bundled zstd will not run; is $tools on a noexec mount?"

    # tar is, by the zstd above, which halves it.
    head -c "$tar_end" "$self" | tail -c "+$tar_at" \
        | "$tools/zstd" -qdc >"$tools/tar" \
        || fail "could not unpack the bundled tar"
    chmod 755 "$tools/tar"
    "$tools/tar" --version >/dev/null 2>&1 || fail "the bundled tar will not run"

    echo "cmrelay: unpacking the closure into $scratch ..."
    # A pipeline reports only its last command, so a truncated file would
    # decompress partially, tar would succeed on what it got, and the install
    # would proceed against a store quietly missing files. zstd verifies its
    # own frame checksums, which makes its exit status the integrity check,
    # so carry it out of the pipeline by hand. POSIX sh has no PIPESTATUS.
    status_file="$tools/zstd-status"
    {
        head -c "$store_end" "$self" | tail -c "+$store_at" \
            | "$tools/zstd" -qdc
        echo $? >"$status_file"
    } | "$tools/tar" -x -f - -C "$scratch" \
        || fail "could not unpack the closure"
    read -r status <"$status_file" || status=unknown
    [ "$status" = 0 ] \
        || fail "the payload is corrupt or truncated (zstd exited $status)"

    [ -d "$scratch/nix/store" ] \
        || fail "the closure did not unpack to $scratch/nix/store"
    [ -f "$scratch$CMRELAY_CLOSURE/bootstrap.env" ] \
        || fail "the unpacked closure has no bootstrap.env"

    : >"$scratch/$ready"

    if [ -z "$shared" ]; then
        :
    elif mv -T "$scratch" "$shared/$hash" 2>/dev/null \
        || { [ ! -e "$shared/$hash" ] && mv "$scratch" "$shared/$hash" 2>/dev/null; }
    then
        stage=$shared/$hash
        canonical=1
    elif [ -f "$shared/$hash/$ready" ]; then
        discard "$scratch"
        stage=$shared/$hash
        canonical=1
    fi
fi

discard "$tools"
tools=

# Written by nix/closure.nix, because neither piece can be found by globbing.
# python3.withPackages produces an env whose bin/python3 is a wrapper that
# execs an absolute store path, so during staging only the raw interpreter is
# runnable; and the env's site-packages is a symlink farm pointing at absolute
# store paths that do not exist yet, so CMRELAY_SITE names each module's own
# store path instead.
. "$stage$CMRELAY_CLOSURE/bootstrap.env"

ld="$stage$CMRELAY_LD"
python="$stage$CMRELAY_PYTHON"
[ -f "$ld" ] || fail "no dynamic loader at $ld"
[ -f "$python" ] || fail "no interpreter at $python"

libs=$(ls -d "$stage"/nix/store/*/lib 2>/dev/null | tr '\n' ':')

CMRELAY_STAGE="$stage"
export CMRELAY_STAGE CMRELAY_CLOSURE
staged_site=$(printf '%s' "$CMRELAY_SITE" | sed "s|:|:$stage|g")
PYTHONPATH="$stage$CMRELAY_CLOSURE:$stage$staged_site"
export PYTHONPATH
PYTHONNOUSERSITE=1
export PYTHONNOUSERSITE

# The CLI installs the store out of $stage and re-execs itself from the
# installed copy, so this process must not hold the staging tree open.
exec "$ld" --library-path "$libs" "$python" -m cmrelay "$@"
