# Portable Fairbeam document foundation

This is a storage foundation for the planned `.fb` workflow, not a finished desktop feature.
It does not register a file association, add a Save command, execute a model, or attach results
to the current workspace. Existing design JSON and result bundles remain unchanged.

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
Case-insensitive duplicate result names are refused.
A document is limited to 40 MiB, an individual result to 200 MiB, and the reference list to 10,000.
The Python reader rejects duplicate JSON keys and non-finite numbers. Browser envelope checks are
preflight only: the server must validate the complete design and file contents before use.

## Storage API and checks

`fairbeam.documents.create(design, results_directory)` makes a separate snapshot.
`save(path, document)` creates a new file; overwriting requires the byte hash returned by `read` or
the previous successful `save`. Cooperating writers share an OS advisory lock. Writes are staged
and flushed in the destination directory before publication. A stale revision, deleted file,
failed flush, or failed replacement must not silently overwrite the previous document.

Windows replacement copies and verifies the destination DACL, including inherited ACEs and
inheritance protection, on the empty staging file before writing document bytes. If the DACL
cannot be read, applied, or preserved exactly, saving fails before replacing the destination.
A retained deletion-capable handle removes a failed stage even when the copied DACL denies
deletion; cleanup does not restore broader parent permissions onto document bytes. Publication
continues to use rename/replace, without `ReplaceFileW`'s partial-failure behavior.
This preserves the DACL, not every item of filesystem metadata: owner/group, SACLs, alternate data
streams, and other attributes are not copied. POSIX replacement preserves mode bits only.

`read(path)` returns `(document, byte_hash)` without opening results or executing Python.
`read_result(path, document, reference)` checks membership, relative boundaries, regular files,
and the content hash. Static symlinks and Windows reparse points in the result directory/file
are refused. This is not a sandbox against a malicious local process concurrently swapping
filesystem entries. A user-selected parent directory is resolved before access.

Missing results do not prevent opening the embedded design. A result with a mismatched checksum
must be reported as unavailable rather than silently loaded. The format does not include a
result writer or garbage collector yet, and no sidecar is deleted by this module.

From the repository root, with its own installed dependencies:

```sh
npm run check:documents
python -m unittest discover -s python/tests -p test_documents.py -v
```

The shared cases in `python/tests/fixtures/document_envelopes.json` exercise both languages.
Filesystem tests cover stale saves, simultaneous writers, Unicode, missing results, corrupted
checksums, links, size limits, and failed writes. They require neither a solver nor network access.
Windows cases also exercise protected and inherited destination DACLs, ACL failures before
publication, and cleanup with a DACL that denies deletion. Numeric identity cases cover nested
values, negative zero, exponents, and large integers rounded as browser JSON numbers.

## Remaining integration gates

- Native Save/Open and OS file-open events on Windows and macOS, including an already running app.
- Explicit access to selected files, dirty-draft confirmation, and safe result copying.
- Stable mapping between document identity and workspace identity; avoid importing a new duplicate
  on every double-click.
- UI distinction between missing, changed, and historical results; never present a previous
  design's result as a freshly computed one.
- Round-trip desktop checks, path/rename/move checks, and installer association checks on both OSes.
- Remaining native filesystem metadata needs an explicit policy before unrestricted overwrites:
  Windows DACLs and POSIX mode bits are preserved, but owner/group, SACLs, alternate data streams,
  POSIX extended ACLs, and other platform metadata are not preserved by this layer.

Do not advertise `.fb` desktop support or promote a release on this foundation alone.
