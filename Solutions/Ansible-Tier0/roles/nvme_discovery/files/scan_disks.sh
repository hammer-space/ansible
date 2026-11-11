#!/usr/bin/env bash
# Classify every block device on this host into the lists nvme_discovery needs.
#
# Emits one tagged line per (list, disk):
#
#   PROTECTED <kname>   disk backing a SYSTEM mountpoint or active swap.
#                       STRICT — never touched by RAID / mkfs.
#   MD        <kname>   disk that is a member of an existing md array.
#                       ADOPTED by raid_setup, NOT excluded from discovery.
#   LVMPV     <kname>   disk backing an LVM PV. Informational only.
#   ROOT      <kname>   the single disk hosting /.
#
# WHY THIS IS A SCRIPT AND NOT INLINE SHELL
# -----------------------------------------
# The previous inline implementation resolved a mountpoint by taking
# `findmnt -n -o SOURCE <mp>` and gating on `[ -b "$src" ]`. Any mount whose
# SOURCE string is not a literal, existing block-device path was SILENTLY
# dropped — and that is a large set in the real world:
#
#   /dev/root            kernel-mounted root before udev populates /dev
#                        (no such node exists → -b fails)
#   /dev/sda2[/@]        btrfs subvolume — findmnt appends the subvol
#   UUID=... / LABEL=... unevaluated fstab source
#   overlay, rootfs      no backing node at all
#
# When every SYSTEM_MOUNTS entry drops out, PROTECTED comes back empty and
# nvme_discovery hard-stops with "Boot / protected-disk detection returned an
# EMPTY list" — see the 2026-08-26 Azure HBv3 report. The stop is correct;
# the detection was not.
#
# The fix: resolve through MAJ:MIN, which the kernel always reports, and walk
# the sysfs topology instead of parsing lsblk paths. A mount always has a real
# MAJ:MIN even when its SOURCE string is unusable.
#
# TESTABILITY
# -----------
#   SCAN_SYSROOT=/path   override /sys  (fake trees in tests)
#   SCAN_PROCROOT=/path  override /proc
#   SCAN_DEVROOT=/path   override /dev
#   scan_disks.sh --resolve-majmin 8:2   run majmin_to_name() alone
#   scan_disks.sh --walk sda2            run walk_to_disks() alone
#
# LC_ALL/LANG are pinned: this parses findmnt / pvs output and a Korean or
# Japanese system locale reorders or translates it (2026-05-14 incident).

set -uo pipefail
export LC_ALL=C LANG=C

SYSROOT="${SCAN_SYSROOT:-/sys}"
PROCROOT="${SCAN_PROCROOT:-/proc}"
DEVROOT="${SCAN_DEVROOT:-/dev}"

# Explicit allow-list of SYSTEM mountpoints. Do NOT replace this with a walk
# of every mount: a Hammerspace data array (/dev/md127 on /hammerspace/hsvol0)
# would drag its members into PROTECTED and break array adoption on re-runs
# (2026-05-20 regression). Data mounts are never walked.
#
# /mnt and /mnt/resource are the Azure ephemeral resource disk, which cloud-init
# formats and mounts automatically. It is wiped on deallocate, so it must never
# be pulled into a Tier 0 RAID set. Matching is on EXACT mountpoints, so listing
# /mnt does not protect a data volume mounted below it at /mnt/hammerspace/*.
SYSTEM_MOUNTS="/ /boot /boot/efi /usr /usr/local /var /home /etc /opt /srv /tmp /lib /lib64 /sbin /bin /root /mnt /mnt/resource"

# The subset of SYSTEM_MOUNTS that may NEVER be released, no matter what an
# operator puts in release_ephemeral_mountpoints. Backs --critical-disks, which
# release_ephemeral_mounts.yml uses as its non-negotiable refuse-list.
#
# Deliberately excluded from this list (i.e. releasable BY EXPLICIT NAME only):
#   /tmp              cloud images routinely put /tmp on fast local scratch —
#                     this is the Azure HBv3 / CycleCloud case
#   /mnt, /mnt/resource   the Azure ephemeral resource disk
#   /opt, /srv        occasionally used as scratch
# Those stay in SYSTEM_MOUNTS, so they are still PROTECTED by default. Naming
# one in release_ephemeral_mountpoints is what makes it eligible.
HARD_SYSTEM_MOUNTS="/ /boot /boot/efi /usr /usr/local /var /home /etc /lib /lib64 /sbin /bin /root"

# Pseudo block devices that are never real storage. These must not reach
# PROTECTED: zram swap is standard on modern distros, and letting zram0 satisfy
# the "protected list is non-empty" gate would disarm the boot-drive check on a
# host where detection genuinely found no disks.
PSEUDO_RE='^(ram|zram|loop|fd)[0-9]+$'

# ---------------------------------------------------------------------------
# Path helpers. macOS `readlink` has no -f, and the test suite runs on Berat's
# Mac, so these are written portably rather than shelling out to readlink -f.
# ---------------------------------------------------------------------------

# Resolve a directory (or a symlink to one) to its physical path.
_realdir() {
    ( cd -P "$1" 2>/dev/null && pwd -P )
}

# Resolve a symlink chain to a final path. Used for device nodes, which are
# files rather than directories, so _realdir cannot be used.
_reallink() {
    local p="$1" target dir i=0
    while [ -L "$p" ] && [ "$i" -lt 16 ]; do
        target=$(readlink "$p") || break
        case "$target" in
            /*) p="$target" ;;
            *)  dir=$(dirname "$p"); p="$dir/$target" ;;
        esac
        i=$((i + 1))
    done
    printf '%s\n' "$p"
}

# ---------------------------------------------------------------------------
# MAJ:MIN -> kernel device name (sda2, nvme0n1p1, dm-0, md127)
#
# This is the whole point of the rewrite. /sys/dev/block/<maj>:<min> exists for
# every mounted block device regardless of what its SOURCE string looks like,
# so /dev/root, btrfs subvolumes and deleted device nodes all resolve here.
#
# Major 0 is the anonymous major — tmpfs, overlay, nfs, proc. There is no
# backing block device, so those are correctly skipped.
# ---------------------------------------------------------------------------
majmin_to_name() {
    local mm="$1" link real
    [ -n "$mm" ] || return 0
    case "$mm" in
        0:*)   return 0 ;;
        *:*)   : ;;
        *)     return 0 ;;
    esac
    link="${SYSROOT}/dev/block/${mm}"
    [ -e "$link" ] || return 0
    real=$(_realdir "$link")
    [ -n "$real" ] && basename "$real"
    return 0
}

# ---------------------------------------------------------------------------
# Device node path -> kernel device name.
# Strips a btrfs "[/subvol]" suffix and follows /dev/mapper/* symlinks.
# ---------------------------------------------------------------------------
path_to_name() {
    local p="$1" real
    [ -n "$p" ] || return 0
    p=$(printf '%s' "$p" | sed 's/\[.*$//')
    case "$p" in "${DEVROOT}"/*) : ;; *) return 0 ;; esac
    [ -e "$p" ] || return 0
    real=$(_reallink "$p")
    [ -n "$real" ] && basename "$real"
    return 0
}

# ---------------------------------------------------------------------------
# kernel device name -> the physical whole disk(s) underneath it.
#   partition  -> parent disk       (sda2 -> sda, nvme0n1p1 -> nvme0n1)
#   md / dm    -> recurse slaves/*  (md127 -> sdb sdc; dm-0 -> sda2 -> sda)
#   whole disk -> itself
# Depth-capped so a malformed sysfs tree cannot spin.
# ---------------------------------------------------------------------------
walk_to_disks() {
    local dev="$1" depth="${2:-0}" sys real parent slave has_slaves
    [ -n "$dev" ] || return 0
    [ "$depth" -lt 8 ] || return 0

    sys="${SYSROOT}/class/block/${dev}"
    [ -d "$sys" ] || return 0

    if [ -f "$sys/partition" ]; then
        real=$(_realdir "$sys")
        [ -n "$real" ] || return 0
        parent=$(basename "$(dirname "$real")")
        [ -n "$parent" ] && [ "$parent" != "$dev" ] \
            && walk_to_disks "$parent" "$((depth + 1))"
        return 0
    fi

    has_slaves=0
    if [ -d "$sys/slaves" ]; then
        for slave in "$sys"/slaves/*; do
            # -L as well as -e: a slaves/ entry can be a dangling symlink while a
            # device is being torn down, and the member NAME is still correct.
            # Without this the glob falls through and the md/dm device itself
            # gets reported as if it were a physical disk.
            [ -e "$slave" ] || [ -L "$slave" ] || continue
            has_slaves=1
            walk_to_disks "$(basename "$slave")" "$((depth + 1))"
        done
    fi
    [ "$has_slaves" -eq 1 ] && return 0

    printf '%s\n' "$dev"
    return 0
}

# ---------------------------------------------------------------------------
# Exact mountpoint -> kernel device name, with a three-step fallback chain so
# a missing or misbehaving findmnt cannot blank out the whole scan.
#   1. findmnt MAJ:MIN   (authoritative)
#   2. /proc/self/mountinfo field 3 (same MAJ:MIN, no findmnt binary needed)
#   3. /proc/mounts device path     (last resort, string-based)
# ---------------------------------------------------------------------------
mp_devname() {
    local mp="$1" mm="" name="" src=""

    # findmnt always reads the REAL /proc, so it must be skipped when the caller
    # has redirected PROCROOT at a fixture — otherwise a test asking about the
    # fake tree would silently get answers about the host it runs on.
    if [ "$PROCROOT" = "/proc" ] && command -v findmnt >/dev/null 2>&1; then
        mm=$(findmnt -n -o MAJ:MIN "$mp" 2>/dev/null | head -1 | tr -d '[:space:]')
    fi
    if [ -z "$mm" ] && [ -r "${PROCROOT}/self/mountinfo" ]; then
        mm=$(awk -v m="$mp" '$5 == m {print $3}' "${PROCROOT}/self/mountinfo" | tail -1)
    fi
    name=$(majmin_to_name "$mm")

    if [ -z "$name" ] && [ -r "${PROCROOT}/mounts" ]; then
        src=$(awk -v m="$mp" '$2 == m {print $1}' "${PROCROOT}/mounts" | tail -1)
        name=$(path_to_name "$src")
    fi

    printf '%s' "$name"
}

# ---------------------------------------------------------------------------
# Sub-command hooks for the unit test — exercise the production functions
# directly against a fake SCAN_SYSROOT tree.
# ---------------------------------------------------------------------------
case "${1:-}" in
    --resolve-majmin) majmin_to_name "${2:-}"; exit 0 ;;
    --walk)           walk_to_disks "${2:-}";  exit 0 ;;
    --path-to-name)   path_to_name "${2:-}";   exit 0 ;;

    # Physical disk(s) backing one exact mountpoint. Used by
    # release_ephemeral_mounts.yml to work out what a release would free.
    --disk-for-mount)
        _n=$(mp_devname "${2:-}")
        [ -n "$_n" ] && walk_to_disks "$_n" | grep -Ev "$PSEUDO_RE" | sort -u
        exit 0
        ;;

    # Disks that may NEVER be released: those backing a HARD_SYSTEM_MOUNTS
    # entry or active swap. The refuse-list an operator cannot override.
    --critical-disks)
        {
            for _mp in $HARD_SYSTEM_MOUNTS; do
                _n=$(mp_devname "$_mp")
                [ -n "$_n" ] && walk_to_disks "$_n"
            done
            if [ -r "${PROCROOT}/swaps" ]; then
                tail -n +2 "${PROCROOT}/swaps" | while read -r _sp _st _rest; do
                    [ -n "$_sp" ] || continue
                    case "$_st" in
                        file)
                            _mm=""
                            [ "$PROCROOT" = "/proc" ] && _mm=$(findmnt -n -o MAJ:MIN -T "$_sp" 2>/dev/null \
                                  | head -1 | tr -d '[:space:]')
                            _n=$(majmin_to_name "$_mm")
                            ;;
                        *)  _n=$(path_to_name "$_sp") ;;
                    esac
                    [ -n "$_n" ] && walk_to_disks "$_n"
                done
            fi
        } | grep -Ev "$PSEUDO_RE" | sort -u
        exit 0
        ;;
esac

# ---------------------------------------------------------------------------
# PROTECTED — system mountpoints + active swap
# ---------------------------------------------------------------------------
{
    for mp in $SYSTEM_MOUNTS; do
        name=$(mp_devname "$mp")
        [ -n "$name" ] && walk_to_disks "$name"
    done

    # Swap is system-critical. Swap FILES matter as much as swap partitions:
    # the disk holding /mnt/swapfile (Azure's cloud-init default) is just as
    # fatal to mkfs as a swap partition would be.
    if [ -r "${PROCROOT}/swaps" ]; then
        tail -n +2 "${PROCROOT}/swaps" | while read -r sw_path sw_type _rest; do
            [ -n "$sw_path" ] || continue
            case "$sw_type" in
                file)
                    mm=""
                    [ "$PROCROOT" = "/proc" ] && mm=$(findmnt -n -o MAJ:MIN -T "$sw_path" 2>/dev/null \
                         | head -1 | tr -d '[:space:]')
                    name=$(majmin_to_name "$mm")
                    ;;
                *)
                    name=$(path_to_name "$sw_path")
                    ;;
            esac
            [ -n "$name" ] && walk_to_disks "$name"
        done
    fi
} | grep -Ev "$PSEUDO_RE" | sort -u | sed 's/^/PROTECTED /'

# ---------------------------------------------------------------------------
# MD — members of existing arrays. Read from sysfs rather than /proc/mdstat so
# that members which are partitions (sdb1) resolve to their parent disk.
# ---------------------------------------------------------------------------
{
    for md in "${SYSROOT}"/class/block/md*; do
        [ -d "$md/slaves" ] || continue
        for slave in "$md"/slaves/*; do
            [ -e "$slave" ] || continue
            walk_to_disks "$(basename "$slave")"
        done
    done
} | sort -u | sed 's/^/MD /'

# ---------------------------------------------------------------------------
# LVMPV — informational. PVs that host a system mount or swap are already in
# PROTECTED via the walk above.
# ---------------------------------------------------------------------------
if command -v pvs >/dev/null 2>&1; then
    {
        pvs --noheadings -o pv_name 2>/dev/null | while read -r pv; do
            pv=$(printf '%s' "$pv" | xargs)
            name=$(path_to_name "$pv")
            [ -n "$name" ] && walk_to_disks "$name"
        done
    } | sort -u | sed 's/^/LVMPV /'
fi

# ---------------------------------------------------------------------------
# ROOT — the single disk hosting /
# ---------------------------------------------------------------------------
root_name=$(mp_devname /)
if [ -n "$root_name" ]; then
    walk_to_disks "$root_name" \
        | grep -Ev "$PSEUDO_RE" | head -1 | sed 's/^/ROOT /'
fi

exit 0
