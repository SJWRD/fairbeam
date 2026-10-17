# Portable Fairbeam documents

The development desktop shell can open and save editable `.fb` documents, with result bundles
in an adjacent folder. This work targets 0.8.0 and still needs the platform checks below before
release. Existing design JSON and result bundles remain supported.

## Desktop workflow

- File > Open selects a `.fb` document or imports legacy JSON. A second launch forwards `.fb`
  paths to the running window. Unsaved changes are handled before opening a queued document.
- Save writes the workspace revision first, then the selected `.fb` file. The first save opens
  a native file picker. Canceling or failing that second step keeps the draft marked unsaved;
  retry uses the workspace revision that was actually written.
- Save remains available for an unchanged writable design, so newly completed runs can be added
  without editing geometry. Completions observed in the current document's workspace mark it
  unsaved. A successful save acknowledges the results it started with; later completions still
  need another save. Results skipped by the storage adapter produce a persistent warning.
- Save As creates a separate document identity and editable workspace copy. Choose a new filename:
  replacing an existing destination through Save As is currently refused, even if the native
  picker offered an overwrite confirmation.
- Reopening a document uses its stable UUID instead of importing another workspace copy. Moving
  the `.fb` file and its adjacent result folder together preserves relative result references.
- Project file details in the model tree show the location and each referenced result's status.
  A result can match the saved design, belong to an earlier design, be missing, or fail validation.
  Matching the design does not establish mesh convergence or physical accuracy.

The native shell retains the selected path and revision behind an opaque handle. Browser code
cannot submit arbitrary external paths. The private bridge is restricted to the shell's current
server session. Browser uploads and drops import the embedded design as a workspace copy and
do not grant access to adjacent files.

The completion reminder uses a bounded per-window session journal of job identifiers, not result
contents. It survives a renderer reload, but is not an authoritative attachment ledger and does
not survive a full app restart. Explicit Open establishes a fresh baseline from the selected
document; Save can still collect eligible completed runs from that document's workspace history.

## Format

A UTF-8 JSON `.fb` document uses `schema: "fairbeam.document/1"` and exactly these fields:

| Field | Meaning |
| --- | --- |
| `id` | Canonical lowercase UUID identifying this document |
| `design` | Embedded `fairbeam.design/1` editable design; validated by the existing server checks |
| `results_directory` | A single portable folder name beside the document |
| `results` | List of result references, which may be empty |

Each result reference has exactly `file`, `sha256`, and `design_sha256`. The first is a JSON
filename in the adjacent folder. `sha256` identifies the exact result file bytes. The design
hash identifies the design snapshot used for that run, and can differ from the current design.
It must not be recomputed from the current geometry when attaching an older run. Hashes establish
identity, not physical accuracy, solver equivalence, or convergence.

`design_sha256` uses the Python `design_digest` helper. For this identity only, JSON numbers are
rounded to finite IEEE-754 doubles, then integral values and negative zero are written as integers.
Normalization is recursive; booleans remain booleans. The normalized snapshot is serialized as
sorted-key, two-space-indented UTF-8 JSON with a final newline. This makes an unchanged browser
round trip (`32.0` to `32`, for example) retain its design identity. It is not a hash of browser
`JSON.stringify` output. Result-file `sha256` continues to hash the exact original bytes.

Folder and result names cannot contain path separators, traversal, alternate streams, Windows
device names, or trailing dots/spaces, and are limited to 180 UTF-8 bytes.
Result names with the same NFC-normalized lowercase spelling are refused; stored filenames keep
their original spelling. This catches canonical Unicode and ordinary case duplicates, not every
filesystem's Unicode alias rule.
A document is limited to 40 MiB, an individual result to 200 MiB, and the reference list to 10,000.
The Python reader rejects duplicate JSON keys and non-finite numbers. Browser envelope checks are
preflight only: the server must validate the complete design and file contents before use.

## Storage API and checks

`fairbeam.documents.create(design, results_directory)` makes a separate snapshot.
`save(path, document)` creates a new file; overwriting requires the byte hash returned by `read` or
the previous successful `save`. Cooperating writers share an OS advisory lock. Writes are staged
and flushed in the destination directory before publication. A stale revision, deleted file,
failed flush, or failed replacement must not silently overwrite the previous document.

On macOS, document saves in the same parent directory share a lock identified by the directory's
device and inode. This intentionally serializes separate documents too, avoiding differences in
filesystem case and Unicode alias rules. New files use `renamex_np(RENAME_EXCL)` where supported,
falling back to an exclusive hardlink only when exclusive rename is unavailable. If neither
operation is supported, creation fails without publishing partial content. Actual volume support,
including exFAT, requires testing on that volume; it is not implied by the macOS API's availability.
Other POSIX systems use an exclusive hardlink, and Windows uses a no-replace rename.

The file is flushed before publication, but the parent directory is not fsynced afterward. This
provides atomic visibility and conflict protection, not a guarantee that the rename survives
power loss.

Windows replacement first compares the destination and empty staging file's owner and primary
group SIDs. A mismatch or unreadable identity refuses the save before writing bytes; the app
does not change ownership or request additional privileges. This also keeps owner-relative
access entries meaningful. Replacement then copies and verifies the destination DACL, including inherited ACEs and
inheritance protection, on the empty staging file before writing document bytes. If the DACL
cannot be read, applied, or preserved exactly, saving fails before replacing the destination.
A retained deletion-capable handle removes a failed stage even when the copied DACL denies
deletion; cleanup does not restore broader parent permissions onto document bytes. Publication
continues to use rename/replace, without `ReplaceFileW`'s partial-failure behavior.
This preserves matching owner/group identities and the DACL, not every item of filesystem
metadata: SACLs, alternate data streams, and other attributes are not copied. POSIX replacement
preserves mode bits only.

`read(path)` returns `(document, byte_hash)` without opening results or executing Python.
`read_result(path, document, reference)` checks membership, relative boundaries, regular files,
and the content hash. Static symlinks and Windows reparse points in the result directory/file
are refused. This is not a sandbox against a malicious local process concurrently swapping
filesystem entries. A user-selected parent directory is resolved before access.

Missing results do not prevent opening the embedded design. A result with a mismatched checksum
is reported as unavailable rather than silently loaded. The workspace adapter captures the
immutable input snapshot and exact completed result bytes, then copies verified bundles into
the sidecar when saving. Older jobs without that provenance are skipped with a warning.
Driven Elmer runs started from a saved workspace can also be attached. The server validates
the workspace scope, saved revision, and original design before recording source identity in
the checksummed research input. Capture verifies that original design separately from the
experimental solver settings, plus the research result and exported bundle checksums. The
experimental result remains experimental; attachment does not establish numerical accuracy.
Source-less research calls retain their existing workspace history behavior and are not
attached to `.fb` documents. Other research modes have no portable viewer bundle.
Capture assumes exclusive output-directory ownership during the worker's write and completion;
external writers replacing an output before its first capture are not provenance-attested.
No sidecar garbage collector runs: unreferenced files are retained.

From the repository root, with its own installed dependencies:

```sh
npm run check:documents
python -m unittest discover -s python/tests -p test_documents.py -v
python -m unittest discover -s python/tests -p 'test_document_workspace*.py' -v
python -m unittest discover -s python/tests -p test_native_documents_api.py -v
```

The shared cases in `python/tests/fixtures/document_envelopes.json` exercise both languages.
Filesystem tests cover stale saves, simultaneous writers, Unicode, missing results, corrupted
checksums, links, size limits, and failed writes. They require neither a solver nor network access.
Windows cases also exercise matching and mismatched owner/group SIDs, ownership read failures,
protected and inherited destination DACLs, ACL failures before publication, and cleanup with a
DACL that denies deletion. Numeric identity cases cover nested
values, negative zero, exponents, and large integers rounded as browser JSON numbers.
Platform-independent mocks check the macOS exclusive-rename contract, unsupported-volume refusal,
competing creates, and parent-directory lock identities; these do not replace tests on a Mac.

## Remaining release gates

- Round-trip desktop checks on Windows and macOS: Save/Open/cancel, an already running app,
  path/rename/move, dirty-draft confirmation, and installed file associations.
- Review the result-status presentation with missing, corrupt, historical, and newly computed
  results. Existing JSON import, export, and the openEMS workflow must remain usable.
- Remaining native filesystem metadata needs an explicit policy before unrestricted overwrites:
  Windows owner/group mismatches are refused and DACLs are preserved, but SACLs, alternate data
  streams, POSIX ownership and extended ACLs, and other platform metadata are not preserved by
  this layer. POSIX replacement preserves mode bits only.

Do not promote a release on storage and unit checks alone.
