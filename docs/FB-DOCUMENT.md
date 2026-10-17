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

## Remaining integration gates

- Native Save/Open and OS file-open events on Windows and macOS, including an already running app.
- Explicit access to selected files, dirty-draft confirmation, and safe result copying.
- Stable mapping between document identity and workspace identity; avoid importing a new duplicate
  on every double-click.
- UI distinction between missing, changed, and historical results; never present a previous
  design's result as a freshly computed one.
- Round-trip desktop checks, path/rename/move checks, and installer association checks on both OSes.
- Native destination permissions/ACLs and file metadata must be reviewed before exposing saves
  outside the managed workspace; the prototype only preserves POSIX mode bits on replacement.

Do not advertise `.fb` desktop support or promote a release on this foundation alone.
