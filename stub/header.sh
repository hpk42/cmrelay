#!/bin/sh
# The whole of the installer that is not Python: a shell header with a store
# archive, a zstd and a tar bolted onto the end of it. A Python CLI needs an
# interpreter and the interpreter is inside the payload; resolving that
# circularity is all this script does.
#
# Extraction goes to staging first, and `cmrelay installer` publishes it only
# after the operator has agreed, which is what keeps "modified nothing" true.
#
# Two tricks make it work. The file is its own archive: everything after this
# header is binary at offsets nix/payload.nix patches into the fields below,
# and `head -c END | tail -c +START` slices a piece out. That is why there is
# no makeself, whose stub needs the host's tar before any of our code runs and
# fails with "Extraction failed" on an image that has none.
#
# And a store binary names an ELF interpreter that does not exist yet, so it
# cannot be executed directly. Invoking ld.so with the target as an argument
# bypasses PT_INTERP, and --library-path outranks the DT_RUNPATH nix writes.

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

# The wheel still invokes `--quiet -- ...`, from when a makeself stub parsed
# those. Accepted and ignored; the CLI knows neither.
while [ $# -gt 0 ]; do
    case $1 in
        --quiet) shift ;;
        --) shift; break ;;
        *) break ;;
    esac
done

# Root only: everything cmrelay does needs it, and the alternative was a
# second staging path for the verbs that do not.
if [ "$(id -u)" != 0 ]; then
    fail "cmrelay must run as root"
fi

ready=.cmrelay-ready

# Stage inside cmrelay's own tree, so the publish stays a rename: across
# filesystems store.install() falls back to copying, 2171 ms against 292 for
# a 232 MiB store, and nothing in the output said which had happened. This is
# still staging, and a refusal takes /opt/cmrelay back down to what was there.
opt_existed=1
[ -d /opt/cmrelay ] || opt_existed=

if [ -n "${CMRELAY_STAGE_DIR:-}" ]; then
    shared=$CMRELAY_STAGE_DIR/cmrelay
    opt_existed=1
else
    mkdir -p /opt/cmrelay || fail "cannot create /opt/cmrelay"
    shared=/opt/cmrelay/.staging
fi

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
    # Worth keeping so the next run skips the unpack, but only where
    # /opt/cmrelay already existed: keeping a directory this run created and
    # then refused over would make "nothing was modified" false.
    if [ -n "$canonical" ] && [ -f "$stage/$ready" ] && [ -n "$opt_existed" ]; then
        return 0
    fi
    discard "$stage"
    # rmdir, not rm -rf: it succeeds only while the directory is still empty,
    # so a run that got as far as installing keeps everything it installed.
    if [ -z "$opt_existed" ] && [ ! -d /opt/cmrelay/nix/store ]; then
        rmdir /opt/cmrelay/.staging /opt/cmrelay 2>/dev/null || true
    fi
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

# The marker alone is not enough: publishing moves the store out, and a tree
# whose closure has gone would fail much later on a missing bootstrap.env.
if [ -n "$shared" ] && [ -f "$shared/$hash/$ready" ] \
    && [ -f "$shared/$hash$CMRELAY_CLOSURE/bootstrap.env" ]; then
    stage=$shared/$hash
    canonical=1
    echo "cmrelay: reusing the closure already unpacked in $stage"
else
    if [ -n "$shared" ]; then
        discard "$shared/$hash"
        scratch=$(mktemp -d "$shared/$hash.incoming.XXXXXXXX")
    else
        scratch=$(mktemp -d "${TMPDIR:-/var/tmp}/cmrelay-stage.XXXXXXXX")
    fi
    [ -n "$scratch" ] || fail "cannot create a staging directory under $shared"
    stage=$scratch

    # Apart from the staging tree, which is published by rename and must
    # contain nothing but the store.
    tools=$(mktemp -d "${TMPDIR:-/tmp}/cmrelay-tools.XXXXXXXX") \
        || fail "cannot create a directory for the bundled tools"

    # head first, then tail. The other way round has head close the pipe
    # while tail still streams the remaining 60 MiB, and tail reports a broken
    # pipe on stderr, which is harmless and reads exactly like a failure.
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
    # A pipeline reports only its last command, so a truncated payload would
    # decompress partially and tar would succeed on what it got. zstd verifies
    # its own frame checksums, so its status is the integrity check; POSIX sh
    # has no PIPESTATUS, hence carrying it out by hand.
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

# Written by nix/closure.nix, because neither piece can be globbed for:
# withPackages' bin/python3 is a wrapper execing an absolute store path, and
# its site-packages is a symlink farm into paths that do not exist yet, so
# CMRELAY_SITE names each module's own store path.
. "$stage$CMRELAY_CLOSURE/bootstrap.env"

ld="$stage$CMRELAY_LD"
python="$stage$CMRELAY_PYTHON"
[ -f "$ld" ] || fail "no dynamic loader at $ld"
[ -f "$python" ] || fail "no interpreter at $python"

libs=$(ls -d "$stage"/nix/store/*/lib 2>/dev/null | tr '\n' ':')

CMRELAY_STAGE="$stage"
# The installer's own path, which argv cannot carry once the CLI is running
# out of the staged closure.
CMRELAY_INSTALLER="$self"
export CMRELAY_STAGE CMRELAY_CLOSURE CMRELAY_INSTALLER
staged_site=$(printf '%s' "$CMRELAY_SITE" | sed "s|:|:$stage|g")
PYTHONPATH="$stage$CMRELAY_CLOSURE:$stage$staged_site"
export PYTHONPATH
PYTHONNOUSERSITE=1
export PYTHONNOUSERSITE

# Deliberately not exec: the EXIT trap above removes the staging tree, and
# exec would replace the shell before it could run.
#
# The explicit exit is load-bearing rather than tidy. Everything past this
# line is the zstd binary, and a shell running off the end of its last command
# reads straight on into it and reports a syntax error. `set -e` hid that for
# a long time, because only successful runs got far enough to hit it.
status=0
"$ld" --library-path "$libs" "$python" -m cmrelay installer "$@" || status=$?
exit "$status"
