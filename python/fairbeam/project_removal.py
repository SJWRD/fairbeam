"""Recoverable removal of a desktop-authorized portable project, never arbitrary paths."""
from pathlib import Path
import os
import stat
import uuid

from . import documents as doc, modelfiles
from ._filelocks import model_lock


def _real(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if doc._linked(info) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise doc.DocumentError("Project removal does not follow links or special files")


def _tree(path: Path) -> None:
    _real(path, directory=True)
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in directories:
            _real(Path(parent) / name, directory=True)
        for name in files:
            _real(Path(parent) / name, directory=False)


def archive(workspace, model_id: str, expected_path: str, expected_results: str) -> dict:
    """Move the .fb and its sidecar together, with rollback if workspace removal fails.

    The user confirms both displayed paths. Sibling documents must not share the sidecar.
    Recovery is on the same volume: no recursive deletion, copy or cross-volume fallback.
    Local history/cached run data deliberately remains available for recovery.
    """
    modelfiles.check_id(model_id)
    with workspace._lock(), modelfiles._transaction(workspace.models_dir, model_id):
        identity = workspace.document_id_for(model_id)
        state = workspace._state(identity) if identity else None
        if not state or state.get("path") != expected_path or state.get("results_directory") != expected_results:
            raise doc.DocumentConflict("Project location changed; refresh the list before removing it")
        path = Path(expected_path)
        if not path.is_absolute() or path.suffix.lower() != ".fb":
            raise doc.DocumentError("Invalid project location")
        for parent in path.parents:
            _real(parent, directory=True)
        with model_lock(doc._document_lock_key(path)):
            document, revision = doc.read(path)
            if (document["id"] != identity or revision != state.get("revision")
                    or document["results_directory"] != expected_results):
                raise doc.DocumentConflict("Project changed on disk; reopen it before removing it")
            folder = path.parent / doc._basename(expected_results)
            exists = folder.exists() or folder.is_symlink()
            if exists:
                _tree(folder)
                results_stat = folder.lstat()
                document_stat = path.lstat()
                # A copied .fb may still refer to this sidecar. Keep both intact in that case.
                for sibling in path.parent.iterdir():
                    # AppleDouble files contain filesystem metadata, not a document.
                    # They commonly accompany Finder-opened projects on exFAT/SMB.
                    if sibling.name.startswith("._"):
                        continue
                    if sibling.suffix.lower() == ".fb":
                        try:
                            # Directory entries can use a different case or Unicode
                            # normalization from the opened path. Compare filesystem
                            # identity without following links, not names or document IDs.
                            if os.path.samestat(sibling.lstat(), document_stat):
                                continue
                            other, _ = doc.read(sibling)
                            other_folder = path.parent / doc._basename(other["results_directory"])
                            try:
                                other_stat = other_folder.lstat()
                            except FileNotFoundError:
                                other_stat = None
                            if other_stat is not None and (doc._linked(other_stat)
                                    or not stat.S_ISDIR(other_stat.st_mode)):
                                raise doc.DocumentError("Sibling results must use a real directory, not a link")
                        except (doc.DocumentError, OSError) as exc:
                            # An unreadable real document might share the sidecar. Keep
                            # the protection, but identify what the user needs to check.
                            raise doc.DocumentConflict(
                                f"Cannot check sibling document '{sibling.name}' for shared results; "
                                "check that file or remove only the list entry"
                            ) from exc
                        # APFS/exFAT/SMB can resolve different case or Unicode
                        # spellings to the same directory even on POSIX, where
                        # normcase does nothing. lstat avoids following a link.
                        if (other_stat is not None and os.path.samestat(other_stat, results_stat)):
                            raise doc.DocumentConflict("Another document shares this results folder; remove only the list entry")
            recovery_root = path.parent / "Fairbeam Deleted"
            if recovery_root == folder:
                raise doc.DocumentError("Results folder conflicts with the recovery location")
            recovery_root.mkdir(exist_ok=True)
            _real(recovery_root, directory=True)
            recovery = recovery_root / uuid.uuid4().hex
            recovery.mkdir()
            moved = []
            try:
                for source in ([folder, path] if exists else [path]):
                    target = recovery / source.name
                    modelfiles._retry_busy(lambda: source.rename(target))
                    moved.append((source, target))
                result = modelfiles.delete_design(workspace.models_dir, workspace.history_dir, model_id)
            except Exception:
                for source, target in reversed(moved):
                    if source.exists() or source.is_symlink():
                        raise doc.DocumentConflict(f"Removal interrupted; files are safe in {recovery}") from None
                    modelfiles._retry_busy(lambda: target.rename(source))
                recovery.rmdir()
                raise
            return {**result, "recovery_path": str(recovery)}
