import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fairbeam import documents as doc
from fairbeam.design import blank_design
from fairbeam.document_exports import export_sections
from fairbeam.document_workspace import _directory, _immutable


class DocumentExportsTests(unittest.TestCase):
    def test_stale_design_is_not_exported_as_result_source(self):
        source = blank_design('patch', 'Original patch')
        current = blank_design('patch', 'Edited patch')
        raw = doc._json_bytes({'model': source['model'], 'results': {'frequency': [1]}})
        ref = {'file': 'bundle.json', 'sha256': doc.digest(raw), 'design_sha256': doc.design_digest(source)}
        self.assertNotEqual(doc.design_digest(current), ref['design_sha256'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Patch.results'
            export_sections(root, raw, ref, current, _directory, _immutable)
            folder = root / 'runs' / ('run-' + ref['sha256'] + '-' + ref['design_sha256'][:16] + '-v2')
            self.assertFalse((folder / 'geometry/source.design.json').exists())
            self.assertEqual(json.loads((folder / 'geometry/geometry.json').read_text())['model'], source['model'])
            self.assertEqual(json.loads((folder / 'metadata/run.json').read_text())['design_sha256'], ref['design_sha256'])
            self.assertTrue((folder / 's-parameters/data.json').is_file())

    def test_distinct_results_have_independent_immutable_run_folders(self):
        design = blank_design('patch', 'Patch')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Patch.results'
            folders = []
            for index, samples in enumerate(([0.1, 0.2], [0.3, 0.4])):
                raw = doc._json_bytes({'model': design['model'],
                                      'results': {'frequency': [1, 2], 'sparams': {'s': samples}}})
                ref = {'file': f'bundle-{index}.json', 'sha256': doc.digest(raw),
                       'design_sha256': doc.design_digest(design)}
                export_sections(root, raw, ref, design, _directory, _immutable)
                folder = root / 'runs' / ('run-' + ref['sha256'] + '-' + ref['design_sha256'][:16] + '-v2')
                folders.append(folder)
                self.assertEqual(json.loads((folder / 's-parameters/data.json').read_text())['sparams']['s'], samples)
                if index == 0:
                    first_bytes = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            self.assertNotEqual(folders[0], folders[1])
            self.assertEqual(set((root / 'runs').iterdir()), set(folders))
            self.assertEqual(first_bytes, {p.relative_to(folders[0]): p.read_bytes()
                                          for p in folders[0].rglob('*') if p.is_file()})

    def test_sections_preserve_data_and_repeat_without_overwrite(self):
        design = blank_design('patch', 'Patch')
        raw = doc._json_bytes({'schema': 'fairbeam.project/1', 'model': design['model'],
                              'results': {'frequency': [1, 2], 'sparams': {'s': [0.1, 0.2]},
                                          'farfield': [{'f': 1, 'dmax_dbi': 6}]}})
        ref = {'file': 'bundle.json', 'sha256': doc.digest(raw), 'design_sha256': doc.design_digest(design)}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Patch.results'
            root.mkdir()
            (root / ref['file']).write_bytes(raw)
            old_folder = root / 'runs' / ('run-' + ref['sha256'] + '-' + ref['design_sha256'][:16])
            (old_folder / 'metadata').mkdir(parents=True)
            old_metadata = b'{"schema":"fairbeam.result-export/1","bundle_file":"../../bundle.json"}'
            (old_folder / 'metadata/run.json').write_bytes(old_metadata)
            export_sections(root, raw, ref, design, _directory, _immutable)
            folder = root / 'runs' / ('run-' + ref['sha256'] + '-' + ref['design_sha256'][:16] + '-v2')
            metadata_path = folder / 'metadata/run.json'
            metadata = json.loads(metadata_path.read_text())
            self.assertEqual(metadata['schema'], 'fairbeam.result-export/2')
            self.assertEqual((old_folder / 'metadata/run.json').read_bytes(), old_metadata)
            bundle_path = (metadata_path.parent / metadata['bundle_file']).resolve()
            self.assertEqual(bundle_path, (root / ref['file']).resolve())
            self.assertEqual(doc.digest(bundle_path.read_bytes()), metadata['sha256'])
            self.assertEqual(json.loads((folder / 's-parameters/data.json').read_text())['sparams']['s'], [0.1, 0.2])
            self.assertEqual(json.loads((folder / 'geometry/source.design.json').read_text()), design)
            before = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            export_sections(root, raw, ref, design, _directory, _immutable)
            self.assertEqual(before, {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()})
            (folder / 's-parameters/data.json').write_text('user edit')
            with self.assertRaises(doc.DocumentConflict):
                export_sections(root, raw, ref, design, _directory, _immutable)
            self.assertEqual((folder / 's-parameters/data.json').read_text(), 'user edit')

    def test_linked_run_directory_is_not_followed(self):
        design = blank_design('patch', 'Patch')
        raw = doc._json_bytes({'results': {}})
        ref = {'file': 'bundle.json', 'sha256': doc.digest(raw), 'design_sha256': 'a'*64}
        with tempfile.TemporaryDirectory() as tmp:
            root, outside = Path(tmp)/'results', Path(tmp)/'outside'
            root.mkdir(); outside.mkdir()
            try:
                (root/'runs').symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('symlinks unavailable')
            with self.assertRaises(doc.DocumentError):
                export_sections(root, raw, ref, design, _directory, _immutable)
            self.assertEqual(list(outside.iterdir()), [])
