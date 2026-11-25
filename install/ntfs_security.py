# SPDX-License-Identifier: GPL-3.0-or-later
"""Give files written from Ubuntu onto Windows' NTFS partition admin-only permissions.

Files that Linux's ntfs3 driver creates get a default Windows security
descriptor that lets Everyone change them. For C:\\Program Files\\RGB-Hub (the
RAM helper there runs with full rights) that is not good enough, not even
until the first sign-in. ntfs3 lets root set a file's descriptor through the
`system.ntfs_security` extended attribute; this builds one and sets it:

    owner Administrators, group SYSTEM, the permission list protected from
    inheritance:
        Administrators  full control
        SYSTEM          full control
        Users           read and run
    (on folders also passed on to what Windows later creates in them)

    python3 ntfs_security.py PATH...     every folder and file under each PATH

Each descriptor is read back and checked. Exit status 1 if any could not be
set: install-windows.sh then stops before it copies anything.

Only the standard library. The layout is Windows' self-relative
SECURITY_DESCRIPTOR (MS-DTYP 2.4.6): a 20-byte header, then the owner SID, the
group SID and the DACL, every number little-endian.
"""

import os
import struct
import sys
from pathlib import Path

XATTR = "system.ntfs_security"

ADMINISTRATORS = "S-1-5-32-544"
SYSTEM = "S-1-5-18"
USERS = "S-1-5-32-545"

FULL_CONTROL = 0x001F01FF  # FILE_ALL_ACCESS
READ_AND_EXECUTE = 0x001200A9  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE

SE_DACL_PRESENT = 0x0004
SE_DACL_PROTECTED = 0x1000
SE_SELF_RELATIVE = 0x8000
CONTROL = SE_SELF_RELATIVE | SE_DACL_PROTECTED | SE_DACL_PRESENT

ACCESS_ALLOWED = 0
OBJECT_INHERIT = 0x01
CONTAINER_INHERIT = 0x02
ACL_REVISION = 2
HEADER = struct.Struct("<BBHIIII")  # revision, sbz1, control, owner, group, sacl, dacl

GRANTS = ((ADMINISTRATORS, FULL_CONTROL), (SYSTEM, FULL_CONTROL), (USERS, READ_AND_EXECUTE))


def sid_bytes(sid: str) -> bytes:
    """S-1-<authority>-<sub>... as a binary SID."""
    parts = sid.split("-")
    if len(parts) < 3 or parts[0] != "S" or parts[1] != "1":
        raise ValueError(f"not a SID: {sid}")
    authority, subs = int(parts[2]), [int(p) for p in parts[3:]]
    return (
        struct.pack("<BB", 1, len(subs))
        + authority.to_bytes(6, "big")
        + b"".join(struct.pack("<I", s) for s in subs)
    )


def sid_text(data: bytes, offset: int = 0) -> tuple[str, int]:
    """(S-1-... text, size in bytes) of the binary SID at `offset`."""
    revision, count = struct.unpack_from("<BB", data, offset)
    if revision != 1:
        raise ValueError("bad SID revision")
    authority = int.from_bytes(data[offset + 2 : offset + 8], "big")
    subs = struct.unpack_from(f"<{count}I", data, offset + 8)
    return "-".join(["S", "1", str(authority), *map(str, subs)]), 8 + 4 * count


def descriptor(folder: bool) -> bytes:
    """The self-relative security descriptor for a folder or a file."""
    flags = OBJECT_INHERIT | CONTAINER_INHERIT if folder else 0
    aces = b""
    for sid, mask in GRANTS:
        body = struct.pack("<I", mask) + sid_bytes(sid)
        aces += struct.pack("<BBH", ACCESS_ALLOWED, flags, 4 + len(body)) + body
    acl = struct.pack("<BBHHH", ACL_REVISION, 0, 8 + len(aces), len(GRANTS), 0) + aces
    owner, group = sid_bytes(ADMINISTRATORS), sid_bytes(SYSTEM)
    owner_at = HEADER.size
    group_at = owner_at + len(owner)
    dacl_at = group_at + len(group)
    head = HEADER.pack(1, 0, CONTROL, owner_at, group_at, 0, dacl_at)
    return head + owner + group + acl


def decode(data: bytes) -> dict:
    """What a self-relative descriptor says: owner, group, control, and the DACL's
    entries as (type, flags, mask, SID). ValueError if it is malformed."""
    if len(data) < HEADER.size:
        raise ValueError("too short")
    revision, _, control, owner_at, group_at, sacl_at, dacl_at = HEADER.unpack_from(data)
    if revision != 1 or not control & SE_SELF_RELATIVE:
        raise ValueError("not a self-relative descriptor")
    result = {
        "control": control,
        "owner": sid_text(data, owner_at)[0] if owner_at else None,
        "group": sid_text(data, group_at)[0] if group_at else None,
        "sacl": bool(sacl_at),
        "aces": [],
    }
    if dacl_at:
        revision, _, size, count, _ = struct.unpack_from("<BBHHH", data, dacl_at)
        if dacl_at + size > len(data):
            raise ValueError("DACL runs past the end")
        at = dacl_at + 8
        for _ in range(count):
            kind, flags, ace_size = struct.unpack_from("<BBH", data, at)
            (mask,) = struct.unpack_from("<I", data, at + 4)
            sid, _ = sid_text(data, at + 8)
            result["aces"].append((kind, flags, mask, sid))
            at += ace_size
    return result


def is_ours(data: bytes, folder: bool) -> bool:
    """Does the descriptor read back from a file say what descriptor() set?"""
    try:
        got, want = decode(data), decode(descriptor(folder))
    except (ValueError, struct.error):
        return False
    return got["owner"] == want["owner"] and got["aces"] == want["aces"]


def secure(path: Path, setxattr=os.setxattr, getxattr=os.getxattr) -> None:
    """Set and check the descriptor of `path` (not following a link). OSError if not."""
    folder = path.is_dir() and not path.is_symlink()
    setxattr(path, XATTR, descriptor(folder), follow_symlinks=False)
    if not is_ours(getxattr(path, XATTR, follow_symlinks=False), folder):
        raise OSError(f"{path}: the permissions read back are not the ones set")


def secure_tree(top: Path, setxattr=os.setxattr, getxattr=os.getxattr) -> int:
    """`top` and everything under it; returns how many were done. OSError on the first failure."""
    done = 0
    for path in [Path(top), *sorted(Path(top).rglob("*"))]:
        if path.is_symlink():
            raise OSError(f"{path}: a link, not expected here")
        secure(path, setxattr, getxattr)
        done += 1
    return done


def main(argv=None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    for top in paths:
        try:
            secure_tree(Path(top))
        except OSError as e:
            print(f"Could not make {top} admin-only: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
