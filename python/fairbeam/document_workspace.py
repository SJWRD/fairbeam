"""The native shell's bridge between portable documents and the run workspace.

Callers must authorize paths in the native shell before calling this module. It is not a
browser file API. A document UUID has one persistent workspace key; opening it is not a
JSON import. Result references describe their admitted source design, not convergence or
equality of per-run parameter overrides and solver settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from . import documents as doc, modelfiles
from ._filelocks import model_lock

STATE_SCHEMA = "fairbeam.document-workspace/1"
RUN_SCHEMA = "fairbeam.document-run/1"
MAX_STATE = 16 * 1024 * 1024


def _research_source(model: str, metadata: dict | None) -> dict | None:
    """Only server-bound driven runs have portable source provenance."""
    if (not isinstance(metadata, dict) or metadata.get("backend") != "elmer"
            or not isinstance(metadata.get("settings"), dict) or metadata["settings"].get("analysis") != "driven"):
        return None
    source = metadata.get("source")
    if (not isinstance(source, dict) or source.get("model_id") != model
            or not isinstance(source.get("backup_scope"), str) or not source["backup_scope"]
            or any(not isinstance(source.get(field), str) or not doc._HASH.fullmatch(source[field])
                   for field in ("workspace_hash", "design_sha256"))):
        return None
    return source


def _directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if doc._linked(info) or not stat.S_ISDIR(info.st_mode):
        raise doc.DocumentError("Document storage must use real directories, not links")


def _immutable(path: Path, data: bytes) -> None:
    """Publish complete bytes under a new name, or accept the identical existing file."""
    _directory(path.parent)
    try:
        old = doc._read_bytes(path, doc.MAX_RESULT)
    except FileNotFoundError:
        old = None
    if old is not None:
        if old != data:
            raise doc.DocumentConflict("A document result filename contains different data")
        return
    fd, name = tempfile.mkstemp(prefix=".fb-result-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            doc._publish_new(temporary, path)
        except FileExistsError:
            if doc._read_bytes(path, doc.MAX_RESULT) != data:
                raise doc.DocumentConflict("A document result filename contains different data") from None
    finally:
        temporary.unlink(missing_ok=True)


def _json_file(path: Path) -> dict:
    try:
        value = json.loads(doc._read_bytes(path, MAX_STATE), object_pairs_hook=doc._unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise doc.DocumentError("Document workspace metadata is invalid") from exc
    if not isinstance(value, dict):
        raise doc.DocumentError("Document workspace metadata is invalid")
    return value


def _uuid(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise doc.DocumentError("Document id must be a canonical UUID") from None
    return value


def _result_bytes(path: Path, document: dict, reference: dict) -> bytes:
    # Copy the same immutable byte snapshot whose boundary, checksum and JSON were checked.
    raw, result = doc._read_result_content(path, document, reference)
    if result.get("model", {}).get("id") != document["design"]["model"]["id"]:
        raise doc.DocumentError("Result model identity does not match this document")
    return raw


class DocumentWorkspace:
    def __init__(self, models_dir: Path, projects_dir: Path, history_dir: Path, manager=None, *, backup_scope: str | None = None):
        self.models_dir = Path(models_dir).resolve()
        self.backup_scope = backup_scope
        self.projects_dir = Path(projects_dir).resolve()
        self.history_dir = Path(history_dir).resolve()
        self.state_dir = self.history_dir / "documents"
        self.manager = manager

    def _lock(self):
        _directory(self.state_dir)
        return model_lock((os.path.normcase(str(self.state_dir)), "fb-workspace"))

    def _state_path(self, identity: str) -> Path:
        return self.state_dir / (_uuid(identity) + ".json")

    def _state(self, identity: str) -> dict | None:
        _directory(self.state_dir)
        try:
            value = _json_file(self._state_path(identity))
        except FileNotFoundError:
            return None
        if value.get("schema") != STATE_SCHEMA or value.get("id") != identity:
            raise doc.DocumentError("Document workspace identity is invalid")
        modelfiles.check_id(value.get("model_id"))
        if not isinstance(value.get("results"), list):
            raise doc.DocumentError("Document workspace results metadata is invalid")
        return value

    def _write_state(self, state: dict) -> None:
        _directory(self.state_dir)
        path = self._state_path(state["id"])
        if path.exists() or path.is_symlink():
            doc._regular(path)
        modelfiles._write_atomic(path, doc._json_bytes(state).decode("utf-8"))

    def document_id_for(self, model_id: str) -> str | None:
        """Read the existing portable identity without creating metadata or path authority."""
        modelfiles.check_id(model_id)
        try:
            info = self.state_dir.lstat()
        except FileNotFoundError:
            return None
        if doc._linked(info) or not stat.S_ISDIR(info.st_mode):
            raise doc.DocumentError("Document storage must use real directories, not links")
        identity = None
        # State publication is atomic. Reading the existing files needs no write lock or
        # directory creation, so ordinary design reads also work in a read-only workspace.
        for path in self.state_dir.iterdir():
            if path.suffix != ".json":
                continue
            candidate = _uuid(path.stem)
            value = _json_file(path)
            if value.get("schema") != STATE_SCHEMA or value.get("id") != candidate:
                raise doc.DocumentError("Document workspace identity is invalid")
            modelfiles.check_id(value.get("model_id"))
            if value["model_id"] != model_id:
                continue
            if identity is not None:
                raise doc.DocumentConflict("Multiple documents use this workspace key; reopen the intended document")
            identity = candidate
        return identity

    @staticmethod
    def _reference_records(document: dict, state: dict | None) -> list[dict]:
        """The portable envelope is authoritative even after a metadata-write failure."""
        cached = {(r["file"], r["sha256"], r["design_sha256"]): r for r in state["results"]} if state else {}
        records = []
        for reference in document["results"]:
            old = cached.get((reference["file"], reference["sha256"], reference["design_sha256"]), {})
            records.append({**reference, **({"project_file": old["project_file"]} if old.get("project_file") else {})})
        return records

    def _released_copy(self, source: dict, file: dict) -> bool:
        """Recover a failed source-watermark update only from a fully committed independent copy."""
        for path in self.state_dir.glob("*.json"):
            try:
                candidate = _json_file(path)
                copied = candidate.get("copied_from") or {}
                if (candidate.get("schema") != STATE_SCHEMA or copied.get("document_id") != source["id"]
                        or copied.get("model_id") != source["model_id"] or copied.get("workspace_hash") != file["hash"]
                        or candidate.get("model_id") == source["model_id"]):
                    continue
                portable, revision = doc.read(Path(candidate["path"]))
                independent_key = modelfiles.check_id(candidate["model_id"])
                doc._regular(self.models_dir / (independent_key + modelfiles.DESIGN_SUFFIX))
                independent = modelfiles.read_design_file(self.models_dir, candidate["model_id"])
                if (portable["id"] == candidate["id"] and revision == candidate.get("revision")
                        and portable["design"] == file["design"] and independent["design"] == file["design"]
                        and independent["hash"] == candidate.get("workspace_hash")):
                    return True
            except (OSError, ValueError, KeyError, TypeError, modelfiles.ModelFileError):
                continue
        return False

    def _create_design(self, key: str, design: dict) -> dict:
        # create_design deliberately assigns an import a new model.id. A native document
        # keeps the embedded identity, including its existing result associations.
        with modelfiles._transaction(self.models_dir, key):
            if (self.models_dir / (key + modelfiles.DESIGN_SUFFIX)).is_symlink():
                raise doc.DocumentConflict("This document's workspace key is a symbolic link")
            modelfiles.check_free(self.models_dir, key)
            path = modelfiles.design_path(self.models_dir, key)
            modelfiles._write_atomic(path, modelfiles._design_text(design), exclusive=True)
            return modelfiles.read_design_file(self.models_dir, key)

    def _project_name(self, identity: str, checksum: str) -> str:
        return f"fb_{uuid.UUID(identity).hex}_{checksum}.json"

    @staticmethod
    def _status(reference: dict, current: str, project_file: str | None = None,
                error: Exception | None = None) -> dict:
        value = {**reference, "provenance": "source-design", "status":
                 ("missing" if isinstance(error, FileNotFoundError) else "invalid") if error else
                 ("current" if reference["design_sha256"] == current else "stale")}
        if project_file is not None:
            value["project_file"] = project_file
        if error is not None:
            value["error"] = str(error)
        return value

    def _import_results(self, path: Path, document: dict) -> tuple[list[dict], list[dict], list[str]]:
        current = doc.design_digest(document["design"])
        records, statuses, warnings = [], [], []
        for reference in document["results"]:
            record = dict(reference)
            try:
                raw = _result_bytes(path, document, reference)
                name = self._project_name(document["id"], reference["sha256"])
                _immutable(self.projects_dir / name, raw)
                record["project_file"] = name
                statuses.append(self._status(reference, current, name))
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                statuses.append(self._status(reference, current, error=exc))
                warnings.append(f"Result {reference['file']} is unavailable: {exc}")
            records.append(record)
        return records, statuses, warnings

    @staticmethod
    def _reply(file: dict, path: Path, document: dict, revision: str,
               statuses: list[dict], warnings: list[str]) -> dict:
        return {"file": {**file, "document_id": document["id"]}, "document": {"id": document["id"], "path": str(path),
                "revision": revision, "results_directory": document["results_directory"]},
                "results": statuses, "warnings": warnings}

    def open(self, path: Path) -> dict:
        path = doc._path(path)
        with self._lock():
            document, revision = doc.read(path)
            state = self._state(document["id"])
            key = state["model_id"] if state else "fb_" + uuid.UUID(document["id"]).hex
            if state is None:
                try:
                    modelfiles.check_free(self.models_dir, key)
                except modelfiles.ModelFileError as exc:
                    if exc.status == 409:
                        raise doc.DocumentConflict("This document's workspace key is already in use") from exc
                    raise
                # A crash between workspace creation and the final metadata update must
                # not turn the next open into an unrelated import or a key collision.
                state = {"schema": STATE_SCHEMA, "id": document["id"], "model_id": key,
                         "path": str(path), "revision": revision, "workspace_hash": None,
                         "results_directory": document["results_directory"], "results": []}
                self._write_state(state)
            try:
                file = modelfiles.read_design_file(self.models_dir, key)
            except modelfiles.ModelFileError as exc:
                if exc.status != 404:
                    raise
                file = self._create_design(key, document["design"])
            else:
                if file["design"] != document["design"]:
                    if file["hash"] != state.get("workspace_hash") and not self._released_copy(state, file):
                        raise doc.DocumentConflict("The workspace has newer saved edits; save them before reopening the document")
                    modelfiles.save_design(self.models_dir, self.history_dir, key, document["design"], file["hash"])
                    file = modelfiles.read_design_file(self.models_dir, key)
            records, statuses, warnings = self._import_results(path, document)
            self._write_state({"schema": STATE_SCHEMA, "id": document["id"], "model_id": key,
                               "path": str(path), "revision": revision, "workspace_hash": file["hash"],
                               "results_directory": document["results_directory"], "results": records,
                               **({"copied_from": state["copied_from"]} if "copied_from" in state else {})})
            return self._reply(file, path, document, revision, statuses, warnings)

    def record_completed(self, job) -> bool:
        """Capture source/content identity at completion, before a later named run can replace it.

        Legacy jobs without a verified immutable design input are deliberately omitted. The
        source-design hash does not assert that solver overrides match the design defaults.
        Exceptions should be reported by the server hook without changing the job's outcome.
        """
        research = job.kind == "research"
        admitted = _research_source(job.model, job.research) if research else None
        if job.status != "done" or not job.bundle or (not admitted if research else not job.input_snapshot):
            return False
        if research and admitted["backup_scope"] != self.backup_scope:
            raise doc.DocumentError("The completed research belongs to a different workspace scope")
        modelfiles.check_id(job.model)
        if self.manager is None:
            return False
        name = doc._basename(job.bundle)
        if not name.endswith(".json"):
            raise doc.DocumentError("A completed result must be a JSON file")
        source = Path(self.manager.input_path(job))
        source_raw = doc._read_bytes(source, modelfiles.MAX_DESIGN)
        if research:
            from .research import load_result
            if doc.digest(source_raw) != job.research.get("snapshot_sha256"):
                raise doc.DocumentError("The completed research input changed")
            spec = json.loads(source_raw, object_pairs_hook=doc._unique_object)
            if (spec.get("source") != admitted or spec.get("backend") != "elmer"
                    or spec.get("settings", {}).get("analysis") != "driven"):
                raise doc.DocumentError("The completed research source identity changed")
            design = spec.get("design")
            source_hash = doc.design_digest(design)
            if source_hash != admitted["design_sha256"]:
                raise doc.DocumentError("The completed research design differs from its original source")
            research_result, checksum = load_result(job)
            if (checksum != job.research.get("result_sha256")
                    or research_result.get("schema") != "fairbeam.elmer-driven-result/1"
                    or research_result.get("input_sha256") != job.research["snapshot_sha256"]):
                raise doc.DocumentError("The completed research result identity changed")
        else:
            if doc.digest(source_raw) != job.input_snapshot.get("sha256"):
                raise doc.DocumentError("The completed run's admitted input changed")
            design = json.loads(source_raw, object_pairs_hook=doc._unique_object)
            source_hash = doc.design_digest(design)
        raw = doc._read_bytes(self.projects_dir / name, doc.MAX_RESULT)
        result = json.loads(raw, object_pairs_hook=doc._unique_object)
        if (not isinstance(result, dict) or result.get("schema") != "fairbeam.project/1"
                or result.get("model", {}).get("id") != design["model"]["id"]):
            raise doc.DocumentError("The completed result does not match its admitted model")
        if research and (doc.digest(raw) != research_result.get("bundle_sha256")
                         or result.get("experimental", {}).get("input_sha256") != job.research["snapshot_sha256"]):
            raise doc.DocumentError("The completed research bundle does not match its admitted input")
        doc._json_bytes(result)
        record = {"schema": RUN_SCHEMA, "job_id": job.id, "model_id": job.model,
                  "project_file": name, "sha256": doc.digest(raw), "design_sha256": source_hash,
                  **({"backup_scope": admitted["backup_scope"]} if research else {})}
        with self._lock():
            folder = self.state_dir / "runs"
            _directory(folder)
            _immutable(folder / (doc.digest(str(job.id).encode("utf-8")) + ".json"), doc._json_bytes(record))
        return True

    def _completed(self, key: str) -> tuple[list[dict], list[str]]:
        _directory(self.state_dir)
        records, warnings = [], []
        folder = self.state_dir / "runs"
        if not folder.exists():
            return records, warnings
        _directory(folder)
        for path in sorted(folder.glob("*.json")):
            try:
                record = _json_file(path)
                if record.get("schema") != RUN_SCHEMA:
                    raise doc.DocumentError("Invalid completed-run metadata")
                if record.get("model_id") != key:
                    continue
                if "backup_scope" in record and record["backup_scope"] != self.backup_scope:
                    continue
                doc._basename(record["project_file"])
                if any(not isinstance(record.get(field), str) or not doc._HASH.fullmatch(record[field])
                       for field in ("sha256", "design_sha256")):
                    raise doc.DocumentError("Invalid completed-run checksum")
                records.append(record)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                warnings.append(f"A completed result's provenance is unavailable: {exc}")
        return records, warnings

    def _cached_bytes(self, record: dict) -> bytes:
        name = doc._basename(record["project_file"])
        raw = doc._read_bytes(self.projects_dir / name, doc.MAX_RESULT)
        if doc.digest(raw) != record["sha256"]:
            raise doc.DocumentError("Result bytes no longer match their recorded source design")
        return raw

    def save(self, path: Path, model_id: str, workspace_hash: str, base_hash: str | None = None,
             document_id: str | None = None, save_as: bool = False) -> dict:
        path = doc._path(path)
        modelfiles.check_id(model_id)
        # Snapshot run metadata before taking the document lock: the completion callback
        # records provenance while it owns the job lock, so never acquire job locks inside.
        list_jobs = getattr(self.manager, "list", None)
        jobs = list_jobs() if callable(list_jobs) else []
        with self._lock(), modelfiles._transaction(self.models_dir, model_id):
            file = modelfiles.read_design_file(self.models_dir, model_id)
            if workspace_hash != file["hash"]:
                raise doc.DocumentConflict("The workspace design changed before the document could be saved")
            doc.design_digest(file["design"])
            source_state = self._state(document_id) if document_id is not None else None
            if document_id is not None and (source_state is None or source_state["model_id"] != model_id):
                raise doc.DocumentConflict("Reopen this document before saving it")
            existing_identity = self.document_id_for(model_id) if document_id is None else None
            if save_as or document_id is None:
                if base_hash is not None or path.exists() or path.is_symlink():
                    raise doc.DocumentConflict("Save a separate document under a new filename")
                results_name = path.stem.encode("utf-8")[:160].decode("utf-8", errors="ignore") + ".results"
                document = doc.create(file["design"], results_name)
                key = "fb_" + uuid.UUID(document["id"]).hex if (save_as and source_state) or existing_identity or file["readonly"] else model_id
                if key != model_id:
                    modelfiles.check_free(self.models_dir, key)
            else:
                document, actual_revision = doc.read(path)
                if actual_revision != base_hash or document["id"] != document_id:
                    raise doc.DocumentConflict("Document changed on disk; reopen or save a separate copy")
                document["design"] = file["design"]
                key = model_id
            if source_state:
                if save_as:
                    try:
                        source_document, _ = doc.read(Path(source_state["path"]))
                    except FileNotFoundError:
                        # A deleted/moved original must not trap the editable workspace:
                        # Save As can still carry the last known verified references.
                        source_document = {"id": source_state["id"], "results": [
                            {k: r[k] for k in ("file", "sha256", "design_sha256")} for r in source_state["results"]]}
                    if source_document["id"] != source_state["id"]:
                        raise doc.DocumentConflict("The source document identity changed before Save As")
                else:
                    source_document = document
                records = self._reference_records(source_document, source_state)
                # The supplied authorized path is current after a file move. Cache metadata
                # may lag a successful prior save; never drop its committed references.
                source_state = {**source_state, "results": list(records),
                                **({"path": str(path), "results_directory": document["results_directory"]}
                                   if not save_as else {})}
            else:
                records = []
            existing_references = {(r["file"], r["sha256"], r["design_sha256"]) for r in records}
            completed, warnings = self._completed(model_id)
            if existing_identity is not None:
                warnings.append("A separate document copy was saved. Existing document result references require explicitly opening the original; only recorded completed results were considered")
            recorded_jobs = {record.get("job_id") for record in completed}
            untracked = sum(1 for job in jobs if job.get("model") == model_id and job.get("status") == "done"
                            and job.get("bundle") and (job.get("kind") != "research"
                                or (self.backup_scope is not None and
                                    (_research_source(model_id, job.get("research")) or {}).get("backup_scope") == self.backup_scope))
                            and job.get("id") not in recorded_jobs)
            if untracked:
                warnings.append(f"{untracked} completed result(s) were not attached because their source design was not recorded")
            # References already in the document retain their filenames and original source
            # hash, including missing files. Newly captured outputs get immutable names.
            known = {(r["sha256"], r["design_sha256"]) for r in records}
            for record in completed:
                identity = (record["sha256"], record["design_sha256"])
                if identity not in known:
                    records.append({"file": "run-" + record["sha256"] + "-" + record["design_sha256"][:16] + ".json",
                                    **{k: record[k] for k in ("sha256", "design_sha256", "project_file")}})
                    known.add(identity)
            current = doc.design_digest(document["design"])
            references, statuses, kept_records = [], [], []
            for record in records:
                reference = {k: record[k] for k in ("file", "sha256", "design_sha256")}
                existing_reference = (record["file"], record["sha256"], record["design_sha256"]) in existing_references
                try:
                    try:
                        raw = self._cached_bytes(record)
                    except (OSError, ValueError, KeyError, TypeError):
                        if not existing_reference:
                            raise
                        source_path = doc._path(Path(source_state["path"]))
                        source_doc, _ = doc.read(source_path)
                        raw = _result_bytes(source_path, source_doc, reference)
                        # A named run may have replaced the original workspace output;
                        # the already-exported immutable sidecar is still that old run.
                        cache_name = self._project_name(document["id"], record["sha256"])
                        _immutable(self.projects_dir / cache_name, raw)
                        record = {**record, "project_file": cache_name}
                    _immutable(path.parent / document["results_directory"] / reference["file"], raw)
                    statuses.append(self._status(reference, current, record.get("project_file")))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    # A newly discovered result that changed is not attached to this document:
                    # its bytes cannot be attributed to this recorded source. Existing missing
                    # references remain recoverable and are not silently dropped.
                    if not existing_reference:
                        warnings.append(f"Result {reference['file']} was not attached: {exc}")
                        continue
                    statuses.append(self._status(reference, current, error=exc))
                    warnings.append(f"Result {reference['file']} is unavailable: {exc}")
                references.append(reference)
                kept_records.append(record)
            document["results"] = references
            doc.validate(document)
            state = {"schema": STATE_SCHEMA, "id": document["id"], "model_id": key,
                     "path": str(path), "revision": doc.digest(doc._json_bytes(document)), "workspace_hash": file["hash"],
                     "results_directory": document["results_directory"], "results": kept_records}
            if save_as and source_state:
                state["copied_from"] = {"document_id": source_state["id"], "model_id": model_id,
                                        "workspace_hash": workspace_hash}
            elif source_state and "copied_from" in source_state:
                state["copied_from"] = source_state["copied_from"]
            # Record the UUID binding before publishing the portable file. If the process
            # stops immediately after publication, reopening still finds the original key.
            created = None
            try:
                if key != model_id:
                    file = self._create_design(key, document["design"])
                    created = file
                    state["workspace_hash"] = file["hash"]
                if source_state is None or document["id"] != source_state["id"]:
                    self._write_state(state)
                revision = doc.save(path, document, None if save_as else base_hash)
            except Exception:
                if created is not None:
                    # Only remove the exact private clone this attempt created. A concurrent
                    # edit must survive even when publishing the portable file failed.
                    try:
                        with modelfiles._transaction(self.models_dir, key):
                            created_path = self.models_dir / (key + modelfiles.DESIGN_SUFFIX)
                            doc._regular(created_path)
                            if modelfiles.read_design_file(self.models_dir, key)["hash"] == created["hash"]:
                                created_path.unlink()
                                state_path = self._state_path(state["id"])
                                if _json_file(state_path) == state:
                                    state_path.unlink()
                    except (OSError, ValueError, modelfiles.ModelFileError):
                        pass
                raise
            state.update(revision=revision, workspace_hash=file["hash"])
            try:
                self._write_state(state)
            except Exception as exc:
                warnings.append(f"The document was saved; workspace metadata needs recovery: {type(exc).__name__}")
            if save_as and source_state:
                # The edits now have their own saved copy and workspace key. Reopening
                # the original may restore its last portable revision without discarding
                # the only saved copy of those edits.
                source_state["workspace_hash"] = workspace_hash
                try:
                    self._write_state(source_state)
                except Exception as exc:
                    warnings.append(f"The copy was saved; the original workspace needs recovery: {type(exc).__name__}")
            return self._reply(file, path, document, revision, statuses, warnings)
