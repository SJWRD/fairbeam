"""Portable .fb documents: editable design plus checksummed, relative result references.

This storage layer does not execute models or register OS file associations. Missing results
never prevent reading the embedded design. Result hashes establish file identity, not numerical
accuracy or convergence. Writers use optimistic concurrency and a shared OS advisory lock.
"""
from __future__ import annotations

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
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise DocumentError("Expected a regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise DocumentError("File exceeds its size limit")
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
            raise DocumentError("Filesystem cannot atomically create a document without replacement") from exc
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
        dacl = _windows_read_dacl(path) if previous is not None and os.name == "nt" else None
        owner_group = _windows_read_owner_group(path) if dacl is not None else None
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
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    if previous is not None:
                        # Windows chmod copies only the readonly bit; keep the stage writable.
                        if os.name != "nt":
                            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
                        _retry_busy(lambda: os.replace(temporary, path))
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


def _read_result_content(path: Path, document: dict, reference: dict) -> tuple[bytes, dict]:
    """Return the exact byte snapshot and parsed result that passed every read check."""
    path = _path(path)
    validate(document)
    if reference not in document["results"]:
        raise DocumentError("Result does not belong to this document")
    folder = path.parent / document["results_directory"]
    info = folder.lstat()
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
        raise DocumentError("Results directory must be a real directory, not a link")
    data = _read_bytes(folder / reference["file"], MAX_RESULT)
    if digest(data) != reference["sha256"]:
        raise DocumentError("Result checksum does not match the document")
    try:
        result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DocumentError("Result is not valid JSON") from exc
    if not isinstance(result, dict) or result.get("schema") != "fairbeam.project/1":
        raise DocumentError("Unsupported result schema")
    _json_bytes(result)
    return data, result


def read_result(path: Path, document: dict, reference: dict) -> dict:
    """Read one referenced result after boundary and checksum checks; missing is recoverable."""
    return _read_result_content(path, document, reference)[1]
