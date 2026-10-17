"""Portable .fb documents: editable design plus checksummed, relative result references.

This storage layer does not execute models or register OS file associations. Missing results
never prevent reading the embedded design. Result hashes establish file identity, not numerical
accuracy or convergence. Writers use optimistic concurrency and a shared OS advisory lock.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
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
    _check_design(design)
    return digest(_json_bytes(design))


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
        if not name.endswith(".json") or name.lower() in names:
            raise DocumentError("Result names must be distinct JSON files")
        names.add(name.lower())
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


def save(path: Path, document: dict, base_hash: str | None = None) -> str:
    """Atomically create or replace a document; replacing requires its current byte hash."""
    path = _path(path)
    validate(document)
    data = _json_bytes(document)
    if len(data) > MAX_DOCUMENT:
        raise DocumentError("Document exceeds its size limit")
    with model_lock((os.path.normcase(str(path)), "fb-document")):
        try:
            previous = _read_bytes(path, MAX_DOCUMENT)
        except FileNotFoundError:
            previous = None
        if (previous is None and base_hash is not None) or (
                previous is not None and digest(previous) != base_hash):
            raise DocumentConflict("Document changed on disk; reopen or save a separate copy")
        if previous == data:
            return digest(data)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if previous is not None:
                # POSIX permissions survive replacement. On Windows chmod copies only the
                # readonly bit: a refused replacement would also make our temp undeletable.
                if os.name != "nt":
                    os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
                _retry_busy(lambda: os.replace(temporary, path))
            elif os.name == "nt":
                # Windows rename fails when another writer created the destination meanwhile.
                _retry_busy(lambda: os.rename(temporary, path))
            else:
                # An exclusive link publishes a complete file without overwriting another writer.
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return digest(data)


def read_result(path: Path, document: dict, reference: dict) -> dict:
    """Read one referenced result after boundary and checksum checks; missing is recoverable."""
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
    return result
