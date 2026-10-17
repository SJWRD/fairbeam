# Portable Fairbeam documents

The development desktop shell can open and save editable `.fb` documents, with result bundles
in an adjacent folder. This work targets 0.8.0 and still needs the platform checks below before
release. Existing design JSON and result bundles remain supported.

## Desktop workflow

- File > Open selects a `.fb` document or imports legacy JSON. A second launch forwards `.fb`
  paths to the running window. Queued requests wait while a modal window is open. Unsaved
  changes are handled before opening a queued document.
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
operation is supported, creation fails without publishing partial content.
Native macOS checks on APFS passed ordinary saves, concurrent saves through case aliases, Unicode
result-name collision checks, and safe refusal for ACLs, Finder tags, quarantine metadata, and
locked files. On the tested exFAT volume, neither exclusive
rename nor hardlinks was supported, so creation refused without publishing partial content.
Save on an internal APFS volume, then copy the `.fb` file and its adjacent results folder together.
Other volumes still need their own checks.
Other POSIX systems use an exclusive hardlink, and Windows uses a no-replace rename.

The file is flushed before publication, but the parent directory is not fsynced afterward. This
provides atomic visibility and conflict protection, not a guarantee that the rename survives
power loss.

Windows replacement refuses a destination with named data streams, including `Zone.Identifier`,
or when stream enumeration fails or is unsupported. It checks before staging and again before
publication; Save As to a new filename leaves the original file and its streams intact. This
policy has been tested on NTFS; other Windows volumes need their own checks. Identical-byte saves
do not replace the file and need no stream enumeration.

Windows replacement also compares the destination and empty staging file's owner and primary
group SIDs. A mismatch or unreadable identity refuses the save before writing bytes; the app
does not change ownership or request additional privileges. This also keeps owner-relative
access entries meaningful. Replacement then copies and verifies the destination DACL, including inherited ACEs and
inheritance protection, on the empty staging file before writing document bytes. If the DACL
cannot be read, applied, or preserved exactly, saving fails before replacing the destination.
A retained deletion-capable handle removes a failed stage even when the copied DACL denies
deletion; cleanup does not restore broader parent permissions onto document bytes. Publication
continues to use rename/replace, without `ReplaceFileW`'s partial-failure behavior.
This is a policy for ordinary local document files, not complete filesystem-metadata preservation.
The writer neither queries nor guarantees preservation of SACLs, auditing rules, mandatory labels,
or security resource attributes, and it does not enable security privileges. Files whose security
depends on that metadata require a separate-copy workflow; the app does not detect those policies.
Other attributes, including encryption and compression, are not copied or guaranteed.

POSIX replacement preserves ordinary mode bits and requires the empty staging file's owner/group to match
the destination before writing bytes. It refuses replacement when the destination or stage has
setuid/setgid/sticky mode bits, extended ACLs, listed extended attributes, or BSD file flags, or
when those metadata queries fail.
macOS queries the open file descriptor with `flistxattr` (including compression attributes) and
`acl_get_fd_np`; Linux uses `os.listxattr`, which also lists POSIX ACL attributes. Metadata is never
stripped or permissions escalated. The error directs the user to save a separate copy.
Finder tags, quarantine attributes, and inherited stage ACLs therefore cause refusal too: even a
downloaded document with quarantine metadata needs Save As to a new file. An identical-byte save
does not replace the file and needs no metadata changes. Queries cover metadata exposed to the
current user, within the existing advisory-lock and static-boundary guarantees described below.

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
cd python
python -m unittest discover -s tests -p 'test_document*.py' -v
python -m unittest discover -s tests -p test_native_documents_api.py -v
python -m unittest discover -s tests -p test_research_documents.py -v
cd ..
```

The shared cases in `python/tests/fixtures/document_envelopes.json` exercise both languages.
Filesystem tests cover stale saves, simultaneous writers, Unicode, missing results, corrupted
checksums, links, size limits, and failed writes. They require neither a solver nor network access.
Windows cases also exercise named-stream refusal, stream-query failures, ordinary and read-only
saves, and a stream added during staging. They verify original bytes, streams, owner/group, DACL,
and file attributes remain intact after refusal. Other cases cover matching and mismatched
owner/group SIDs, ownership read failures,
protected and inherited destination DACLs, ACL failures before publication, and cleanup with a
DACL that denies deletion. Numeric identity cases cover nested
values, negative zero, exponents, and large integers rounded as browser JSON numbers.
Platform-independent mocks check the macOS exclusive-rename contract, unsupported-volume refusal,
competing creates, and parent-directory lock identities; these do not replace tests on a Mac.
Metadata mocks cover descriptor query errors, ACLs, attributes, and ownership refusal. Native
platform tests check Linux xattr refusal and macOS ACL/Finder-tag refusal on temporary fixtures.

For manual local I/O measurements without a solver or network, run
`python scripts/benchmark-document-io.py --bundles 500 --padding-kib 4 --repeats 3 --output current.json`
from the repository root. Repeat with the same fixture arguments and either
`--per-reference-manifest` (a fresh validated reader per reference) or
`--legacy-result-reader` (an isolated second result read/hash). These are separate cost baselines,
not complete historical implementations. The default fixture is eight bundles with 8 MiB padding;
fixtures are limited to 1,000 bundles and 256 MiB total padding. Reports count actual reader bytes,
result hashes, design hashes, and manifest validations. Each Open must match an unmodified-reader
control exactly, including references, statuses, and copied bytes; sidecar hashes, sizes, and
modification times must stay unchanged. Use new report filenames and compare alternating runs;
cache priming and verification are outside the measured operation.

## Remaining release gates

- Round-trip desktop checks on Windows and macOS: Save/Open/cancel, an already running app,
  path/rename/move, dirty-draft confirmation, and installed file associations.
- Review the result-status presentation with missing, corrupt, historical, and newly computed
  results. Existing JSON import, export, and the openEMS workflow must remain usable.
- Validate the bounded metadata policy on target Windows and macOS volumes. Windows preserves
  DACLs and owner/group identities and refuses named streams or failed stream queries. POSIX
  preserves owner/group and ordinary mode bits and refuses extended metadata. Windows SACLs,
  audit/security policies, encryption, compression, and other attributes are outside the verified
  preservation contract; unrestricted metadata-preserving overwrites are not supported.

Do not promote a release on storage and unit checks alone.
