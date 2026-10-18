"""Portable .fb documents: editable design plus checksummed, relative result references.

This storage layer does not execute models or register OS file associations. Missing results
never prevent reading the embedded design. Result hashes establish file identity, not numerical
accuracy or convergence. Writers use optimistic concurrency and a shared OS advisory lock.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata
import uuid

from ._filelocks import model_lock
from .design import DesignError, check_design
from .modelfiles import _retry_busy

SCHEMA = "fairbeam.document/1"
MAX_DOCUMENT = 40 * 1024 * 1024
MAX_RESULT = 200 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class DocumentError(ValueError):
    pass


class DocumentConflict(DocumentError):
    """The selected file changed since the user opened it; never overwrite silently."""


def _json_bytes(value) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                           indent=2) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise DocumentError("Document must contain finite, UTF-8 JSON data") from exc


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def design_digest(design: dict) -> str:
    """Hash a design snapshot using the same finite-double number identity as the browser."""
    _check_design(design)
    try:
        return digest(_json_bytes(_design_numbers(design)))
    except (OverflowError, RecursionError) as exc:
        raise DocumentError("Design must contain finite JSON numbers") from exc


def _design_numbers(value):
    # Python's 32.0 and JavaScript's JSON 32 describe the same design. Normalize all
    # numbers to IEEE-754 doubles first, matching design.evaluate and JSON.parse;
    # integral doubles (including negative zero) then have one integer spelling.
    # This is only the design identity: result hashes still cover their exact bytes.
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise DocumentError("Design must contain finite JSON numbers")
        return int(number) if number.is_integer() else number
    if isinstance(value, dict):
        return {key: _design_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_design_numbers(item) for item in value]
    return value


def _check_design(design) -> None:
    if not isinstance(design, dict) or design.get("schema") != "fairbeam.design/1":
        raise DocumentError("Embedded design must use fairbeam.design/1")
    try:
        check_design(design)
    except (DesignError, TypeError, AttributeError) as exc:
        raise DocumentError(f"Invalid embedded design: {exc}") from exc


def _basename(value) -> str:
    try:
        size = len(value.encode("utf-8")) if isinstance(value, str) else 0
    except UnicodeError as exc:
        raise DocumentError("Document references must be valid UTF-8 names") from exc
    if (not 1 <= size <= 180
            or value in {".", ".."} or value.endswith((".", " "))
            or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in value)
            or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])", value.split(".")[0], re.I)):
        raise DocumentError("Document references must use portable single file names")
    return value


def validate(document: dict) -> None:
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise DocumentError(f"Expected {SCHEMA}")
    if set(document) != {"schema", "id", "design", "results_directory", "results"}:
        raise DocumentError("Unsupported document fields")
    try:
        if str(uuid.UUID(document["id"])) != document["id"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise DocumentError("Document id must be a canonical UUID") from None
    _check_design(document["design"])
    _basename(document["results_directory"])
    refs = document["results"]
    if not isinstance(refs, list) or len(refs) > 10000:
        raise DocumentError("Document results must be a list of at most 10000 references")
    names = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"file", "sha256", "design_sha256"}:
            raise DocumentError("Invalid result reference")
        name = _basename(ref["file"])
        identity = unicodedata.normalize("NFC", name).lower()
        if not name.endswith(".json") or identity in names:
            raise DocumentError("Result names must be distinct JSON files")
        names.add(identity)
        if any(not isinstance(ref[k], str) or not _HASH.fullmatch(ref[k])
               for k in ("sha256", "design_sha256")):
            raise DocumentError("Result references require SHA-256 content and design hashes")


def create(design: dict, results_directory: str) -> dict:
    document = {"schema": SCHEMA, "id": str(uuid.uuid4()), "design": design,
                "results_directory": results_directory, "results": []}
    validate(document)
    return json.loads(_json_bytes(document))  # own a snapshot, not the caller's mutable design


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _regular(path: Path) -> None:
    info = path.lstat()
    if _linked(info) or not stat.S_ISREG(info.st_mode):
        raise DocumentError("Document and result files must be regular files, not links")


def _read_bytes(path: Path, limit: int) -> bytes:
    _regular(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise DocumentError("Expected a regular file")
        if info.st_size < 0:
            raise DocumentError("File has an invalid size")
        if info.st_size > limit:
            raise DocumentError("File exceeds its size limit")
        # A MAX_RESULT-sized read can allocate/probe a large buffer even for a tiny
        # file. Bound it by this opened descriptor, with one byte to detect growth.
        data = stream.read(info.st_size + 1)
    if len(data) > limit:
        raise DocumentError("File exceeds its size limit")
    if len(data) != info.st_size:
        raise DocumentError("File changed size while it was being read")
    return data


def _path(path: Path) -> Path:
    path = Path(path).absolute()
    _basename(path.name)
    if path.suffix.lower() != ".fb":
        raise DocumentError("A Fairbeam document uses the .fb extension")
    return path.parent.resolve(strict=True) / path.name


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DocumentError("Duplicate JSON object field")
        result[key] = value
    return result


def _parse(data: bytes) -> dict:
    try:
        document = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DocumentError("Document is not valid UTF-8 JSON") from exc
    validate(document)
    # Reject non-finite values even in optional design metadata.
    _json_bytes(document)
    return document


def read(path: Path) -> tuple[dict, str]:
    """Read the embedded design without opening any sidecar or executing any code."""
    path = _path(path)
    data = _read_bytes(path, MAX_DOCUMENT)
    return _parse(data), digest(data)


def _windows_require_unnamed_stream(path: Path) -> None:
    """Refuse replacement when named data streams cannot be ruled out."""
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = (("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296))

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.FindFirstStreamW.argtypes = (wintypes.LPCWSTR, ctypes.c_int,
                                    ctypes.POINTER(StreamData), wintypes.DWORD)
    api.FindFirstStreamW.restype = wintypes.HANDLE
    api.FindNextStreamW.argtypes = (wintypes.HANDLE, ctypes.POINTER(StreamData))
    api.FindNextStreamW.restype = wintypes.BOOL
    api.FindClose.argtypes = (wintypes.HANDLE,)
    api.FindClose.restype = wintypes.BOOL
    query_error = "Cannot inspect document data streams; use Save As with a new filename"
    data = StreamData()
    handle = api.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        # This is an existing regular document, which has an unnamed data stream.
        # Unsupported enumeration and every other error fail closed.
        raise DocumentError(query_error) from ctypes.WinError(ctypes.get_last_error())
    try:
        while True:
            if data.name != "::$DATA":
                raise DocumentError("Document has named data streams; use Save As with a new filename")
            if not api.FindNextStreamW(handle, ctypes.byref(data)):
                error = ctypes.get_last_error()
                if error == 38:  # ERROR_HANDLE_EOF: enumeration completed.
                    break
                raise DocumentError(query_error) from ctypes.WinError(error)
    finally:
        if not api.FindClose(handle):
            raise DocumentError(query_error) from ctypes.WinError(ctypes.get_last_error())


def _windows_read_security(path: Path, information: int) -> bytes:
    """Read only the requested parts of a file's self-relative security descriptor."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    get.restype = wintypes.BOOL
    size = wintypes.DWORD()
    if get(str(path), information, None, 0, ctypes.byref(size)) or ctypes.get_last_error() != 122:
        raise ctypes.WinError(ctypes.get_last_error())
    data = ctypes.create_string_buffer(size.value)
    if not get(str(path), information, data, len(data), ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return data.raw[:size.value]


def _windows_read_dacl(path: Path) -> bytes:
    """Read a self-relative descriptor containing only the destination's DACL."""
    return _windows_read_security(path, 4)


def _windows_read_owner_group(path: Path) -> tuple[bytes, bytes]:
    """Read actual owner/group SIDs; descriptor offsets and control flags are not identity."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.IsValidSid.argtypes = (ctypes.c_void_p,)
    api.IsValidSid.restype = wintypes.BOOL
    api.GetLengthSid.argtypes = (ctypes.c_void_p,)
    api.GetLengthSid.restype = wintypes.DWORD
    data = ctypes.create_string_buffer(_windows_read_security(path, 1 | 2))
    values = []
    for name in ("GetSecurityDescriptorOwner", "GetSecurityDescriptorGroup"):
        get = getattr(api, name)
        get.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.BOOL))
        get.restype = wintypes.BOOL
        sid, defaulted = ctypes.c_void_p(), wintypes.BOOL()
        if not get(data, ctypes.byref(sid), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not sid.value or not api.IsValidSid(sid):
            raise DocumentError("Cannot determine the document owner and group")
        values.append(ctypes.string_at(sid, api.GetLengthSid(sid)))
    return values[0], values[1]


def _windows_require_owner_group(path: Path, expected: tuple[bytes, bytes]) -> None:
    """Refuse ownership changes rather than attempting to acquire or change ownership."""
    import ctypes
    from ctypes import wintypes

    actual = _windows_read_owner_group(path)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.EqualSid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    api.EqualSid.restype = wintypes.BOOL
    for original, staging in zip(expected, actual):
        if not api.EqualSid(ctypes.create_string_buffer(original), ctypes.create_string_buffer(staging)):
            raise DocumentError("Cannot preserve the document owner and group; save a separate copy")


def _windows_apply_dacl(path: Path, descriptor: bytes) -> None:
    """Copy and verify the DACL, including inherited ACEs and inheritance protection."""
    if _windows_read_dacl(path) == descriptor:
        return  # Do not change Windows' automatic-inheritance flags on an identical ACL.
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetSecurityDescriptorControl.argtypes = (ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD))
    api.GetSecurityDescriptorControl.restype = wintypes.BOOL
    api.GetSecurityDescriptorDacl.argtypes = (ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL))
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    api.SetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int,
        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    api.SetNamedSecurityInfoW.restype = wintypes.DWORD
    data = ctypes.create_string_buffer(descriptor)
    control, revision = wintypes.WORD(), wintypes.DWORD()
    present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
    if not api.GetSecurityDescriptorControl(data, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.GetSecurityDescriptorDacl(data, ctypes.byref(present), ctypes.byref(dacl),
                                       ctypes.byref(defaulted)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not present.value:
        raise DocumentError("Cannot preserve a destination without a DACL")
    protection = 0x80000000 if control.value & 0x1000 else 0x20000000
    error = api.SetNamedSecurityInfoW(str(path), 1, 4 | protection, None, None, dacl, None)
    if error:
        raise ctypes.WinError(error)
    if _windows_read_dacl(path) != descriptor:
        raise DocumentError("Cannot preserve the destination DACL exactly")


def _windows_staging_fd(fd: int, path: Path) -> int:
    """Retain deletion rights before restricting the empty staging file's DACL."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    # CRT-opened files do not share deletion. Reopen the still-empty staging file
    # with a handle that can be renamed while open and removed after an error,
    # even when the copied DACL denies the current user DELETE or WRITE_DAC.
    os.close(fd)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(path), 0x40000000 | 0x10000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise


def _windows_discard_staging(fd: int) -> None:
    """Delete through the retained handle, without relaxing permissions on saved bytes."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD)
    api.SetFileInformationByHandle.restype = wintypes.BOOL
    delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN, not BOOL.
    if not api.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), 4,
                                         ctypes.byref(delete), ctypes.sizeof(delete)):
        raise ctypes.WinError(ctypes.get_last_error())


def _macos_metadata(fd: int) -> tuple[dict[bytes, bytes], bytes | None]:
    """Snapshot every descriptor xattr and the complete Darwin ACL, failing closed."""
    import ctypes

    api = ctypes.CDLL(None, use_errno=True)
    listing, getting = api.flistxattr, api.fgetxattr
    listing.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    listing.restype = ctypes.c_ssize_t
    getting.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                       ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
    getting.restype = ctypes.c_ssize_t
    api.acl_get_fd_np.argtypes = (ctypes.c_int, ctypes.c_int)
    api.acl_get_fd_np.restype = ctypes.c_void_p
    api.acl_to_text.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t))
    api.acl_to_text.restype = ctypes.c_void_p
    api.acl_free.argtypes = (ctypes.c_void_p,)
    api.acl_free.restype = ctypes.c_int

    def read(call):
        size = call(None, 0)
        if not 0 <= size <= 16 * 1024 * 1024:
            raise OSError(ctypes.get_errno() or errno.EIO, "Cannot read document metadata")
        buffer = ctypes.create_string_buffer(max(1, size))
        if call(buffer, size) != size:
            raise OSError(errno.EIO, "Document metadata changed while reading")
        return buffer.raw[:size]

    names = read(lambda buf, size: listing(fd, buf, size, 0x20))
    if names and not names.endswith(b"\0"):
        raise OSError(errno.EIO, "Invalid document attribute list")
    keys = names[:-1].split(b"\0") if names else []
    if len(set(keys)) != len(keys) or any(not key for key in keys):
        raise OSError(errno.EIO, "Invalid document attribute list")
    attrs = {key: read(lambda buf, size: getting(fd, key, buf, size, 0, 0x20)) for key in keys}
    ctypes.set_errno(0)
    acl = api.acl_get_fd_np(fd, 0x100)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return attrs, None
        raise OSError(ctypes.get_errno() or errno.EIO, "Cannot read document ACL")
    text = None
    try:
        size = ctypes.c_ssize_t()
        text = api.acl_to_text(acl, ctypes.byref(size))
        if not text or not 0 <= size.value <= 16 * 1024 * 1024:
            raise OSError(ctypes.get_errno() or errno.EIO, "Cannot serialize document ACL")
        return attrs, ctypes.string_at(text, size.value)
    finally:
        text_error = api.acl_free(text) if text else 0
        acl_error = api.acl_free(acl)
        if text_error or acl_error:
            raise OSError(ctypes.get_errno() or errno.EIO, "Cannot release document ACL")


def _macos_preserve_metadata(source: Path, destination_fd: int, expected, snapshot=None):
    """Copy metadata on empty staging, or recheck it before publishing saved bytes."""
    import ctypes

    try:
        _regular(source)
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            original = os.fstat(source_fd)
            staging = os.fstat(destination_fd)
            if (not stat.S_ISREG(original.st_mode) or not stat.S_ISREG(staging.st_mode)
                    or (original.st_dev, original.st_ino) != (expected.st_dev, expected.st_ino)):
                raise DocumentError("Document changed while preserving metadata")
            identity = lambda info: (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
                                     getattr(info, "st_flags", 0))
            if identity(original) != identity(expected) or identity(staging) != identity(expected):
                raise DocumentError("Cannot preserve document owner, group and permissions")
            current = _macos_metadata(source_fd)
            if snapshot is None:
                api = ctypes.CDLL(None, use_errno=True)
                api.fcopyfile.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
                api.fcopyfile.restype = ctypes.c_int
                # COPYFILE_ACL | COPYFILE_XATTR; never copy data, times, or ownership.
                if api.fcopyfile(source_fd, destination_fd, None, 0x1 | 0x4) != 0:
                    raise OSError(ctypes.get_errno() or errno.EIO, "Cannot copy document metadata")
                snapshot = current
            if (current != snapshot or _macos_metadata(source_fd) != snapshot
                    or _macos_metadata(destination_fd) != snapshot
                    or identity(os.fstat(destination_fd)) != identity(expected)):
                raise DocumentError("Cannot verify preserved document ACLs and extended attributes")
            return snapshot
        finally:
            os.close(source_fd)
    except (OSError, AttributeError) as exc:
        raise DocumentError("Cannot preserve document metadata; save a separate copy") from exc


def _posix_plain_metadata(path: Path):
    """Refuse metadata this replacement layer cannot preserve; never strip it."""
    try:
        _regular(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise DocumentError("Document metadata requires a regular file")
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise DocumentError("Cannot preserve document special mode bits; save a separate copy")
            if getattr(info, "st_flags", 0):
                raise DocumentError("Cannot preserve document file flags; save a separate copy")
            if sys.platform == "darwin":
                _macos_metadata(fd)  # These are copied and verified on staging before replacement.
                extended = False
            else:
                extended = bool(os.listxattr(fd))
            if extended:
                raise DocumentError("Cannot preserve document ACLs or extended attributes; save a separate copy")
            return info
        finally:
            os.close(fd)
    except (OSError, AttributeError) as exc:
        raise DocumentError("Cannot verify document ACLs and extended attributes; save a separate copy") from exc


def _posix_prepare_staging(path: Path, expected) -> None:
    actual = _posix_plain_metadata(path)
    if (actual.st_uid, actual.st_gid) != (expected.st_uid, expected.st_gid):
        raise DocumentError("Cannot preserve the document owner and group; save a separate copy")
    os.chmod(path, stat.S_IMODE(expected.st_mode))


def _document_lock_key(path: Path) -> tuple[str, str]:
    if sys.platform == "darwin":
        # macOS filesystems have different case/Unicode alias rules. Serializing
        # documents in one canonical parent avoids approximating those rules,
        # including when the destination does not exist yet. Model locks keep
        # their existing identity and behavior.
        parent = path.parent.stat()
        return f"{parent.st_dev}:{parent.st_ino}", "fb-document-directory"
    return os.path.normcase(str(path)), "fb-document"


def _macos_rename_exclusive(source: Path, destination: Path) -> None:
    """Publish without replacement where the macOS filesystem supports RENAME_EXCL."""
    import ctypes

    try:
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "Exclusive rename is unavailable") from exc
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x00000004) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _publish_new(temporary: Path, destination: Path) -> None:
    """Publish complete staged bytes exclusively; FileExistsError means a competing file."""
    unsupported = {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}
    if sys.platform == "darwin":
        try:
            _macos_rename_exclusive(temporary, destination)
            return
        except OSError as exc:
            if exc.errno not in unsupported:
                raise
    elif os.name == "nt":
        # Windows rename fails if another writer created the destination meanwhile.
        _retry_busy(lambda: os.rename(temporary, destination))
        return
    try:
        os.link(temporary, destination)
    except OSError as exc:
        if exc.errno in unsupported:
            raise DocumentError(
                "This drive does not support the atomic file creation required by Fairbeam. "
                "Save on an internal disk, then copy the .fb file and its adjacent results folder together."
            ) from exc
        raise


def save(path: Path, document: dict, base_hash: str | None = None) -> str:
    """Atomically create or replace a document; replacing requires its current byte hash."""
    path = _path(path)
    validate(document)
    data = _json_bytes(document)
    if len(data) > MAX_DOCUMENT:
        raise DocumentError("Document exceeds its size limit")
    with model_lock(_document_lock_key(path)):
        try:
            previous = _read_bytes(path, MAX_DOCUMENT)
        except FileNotFoundError:
            previous = None
        if (previous is None and base_hash is not None) or (
                previous is not None and digest(previous) != base_hash):
            raise DocumentConflict("Document changed on disk; reopen or save a separate copy")
        if previous == data:
            return digest(data)
        if previous is not None and os.name == "nt":
            _windows_require_unnamed_stream(path)
        dacl = _windows_read_dacl(path) if previous is not None and os.name == "nt" else None
        owner_group = _windows_read_owner_group(path) if dacl is not None else None
        posix_metadata = _posix_plain_metadata(path) if previous is not None and os.name != "nt" else None
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            if os.name == "nt":
                fd = _windows_staging_fd(fd, temporary)
            with os.fdopen(fd, "wb") as stream:
                try:
                    if dacl is not None:
                        # Owner-relative ACEs must retain their meaning after replacement.
                        # Refuse a different owner/group before content or DACL changes.
                        _windows_require_owner_group(temporary, owner_group)
                        # Restrict the empty staging file before any document bytes are written.
                        _windows_apply_dacl(temporary, dacl)
                    if posix_metadata is not None:
                        _posix_prepare_staging(temporary, posix_metadata)
                    macos_metadata = None
                    if posix_metadata is not None and sys.platform == "darwin":
                        macos_metadata = _macos_preserve_metadata(path, stream.fileno(), posix_metadata)
                    stream.write(data)
                    stream.flush()
                    if macos_metadata is not None:
                        _macos_preserve_metadata(path, stream.fileno(), posix_metadata, macos_metadata)
                    os.fsync(stream.fileno())
                    if previous is not None:
                        def replace():
                            if os.name == "nt":
                                _windows_require_unnamed_stream(path)
                            os.replace(temporary, path)
                        _retry_busy(replace)
                    else:
                        try:
                            _publish_new(temporary, path)
                        except FileExistsError as exc:
                            raise DocumentConflict("Document changed on disk; reopen or save a separate copy") from exc
                except BaseException:
                    if os.name == "nt":
                        _windows_discard_staging(stream.fileno())
                    raise
        finally:
            temporary.unlink(missing_ok=True)
    return digest(data)


class _ResultReader:
    """One owned manifest snapshot; every result still gets fresh filesystem checks."""

    _FIELDS = ("file", "sha256", "design_sha256")
    _FIELD_SET = frozenset(_FIELDS)

    def __init__(self, path: Path, document: dict):
        path = _path(path)
        try:
            snapshot = copy.deepcopy(document)
        except (TypeError, ValueError, RecursionError) as exc:
            raise DocumentError("Document could not be snapshotted") from exc
        validate(snapshot)
        self._folder = path.parent / snapshot["results_directory"]
        self._references = tuple(tuple(ref[field] for field in self._FIELDS)
                                 for ref in snapshot["results"])
        self._allowed = frozenset(self._references)
        self._document_id = snapshot["id"]
        self._model_id = snapshot["design"]["model"]["id"]
        self._design_sha256 = design_digest(snapshot["design"])

    @property
    def document_id(self) -> str:
        return self._document_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def design_sha256(self) -> str:
        return self._design_sha256

    def references(self):
        # Yield fresh dictionaries without exposing the owned immutable triples.
        return (dict(zip(self._FIELDS, ref)) for ref in self._references)

    def read(self, reference: dict) -> tuple[bytes, dict]:
        if not isinstance(reference, Mapping):
            raise DocumentError("Result does not belong to this document")
        reference = dict(reference)
        if reference.keys() != self._FIELD_SET:
            raise DocumentError("Result does not belong to this document")
        key = tuple(reference[field] for field in self._FIELDS)
        if any(not isinstance(value, str) for value in key) or key not in self._allowed:
            raise DocumentError("Result does not belong to this document")
        name, checksum, _ = key
        info = self._folder.lstat()
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise DocumentError("Results directory must be a real directory, not a link")
        data = _read_bytes(self._folder / name, MAX_RESULT)
        if digest(data) != checksum:
            raise DocumentError("Result checksum does not match the document")
        try:
            result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise DocumentError("Result is not valid JSON") from exc
        if not isinstance(result, dict) or result.get("schema") != "fairbeam.project/1":
            raise DocumentError("Unsupported result schema")
        _json_bytes(result)
        return data, result


def _read_result_content(path: Path, document: dict, reference: dict) -> tuple[bytes, dict]:
    """Return the exact byte snapshot and parsed result that passed every read check."""
    return _ResultReader(path, document).read(reference)


def read_result(path: Path, document: dict, reference: dict) -> dict:
    """Read one referenced result after boundary and checksum checks; missing is recoverable."""
    return _read_result_content(path, document, reference)[1]
