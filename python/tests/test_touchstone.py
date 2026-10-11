import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from fairbeam.touchstone import format_snp, full_matrix, parse_touchstone, read_s1p, read_snp, read_touchstone, reflection, write_s1p, write_snp

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "public" / "projects" / "patch-antenna.json"
# shared with the browser importer's check (scripts/check-import.mjs)
FIXTURES = ROOT / "examples" / "import-fixtures" / "touchstone"


def bundle(z_port=73.0):
    f = np.linspace(1e9, 2e9, 11)
    zin = 60 + 1j * np.linspace(-40, 40, 11)
    s = (zin - z_port) / (zin + z_port)
    return {"name": "test", "model": {"id": "t"}, "ports": [{"number": 1, "excite": True}],
            "results": {"frequency": f.tolist(),
                        "ports": {"1": {"s11_re": s.real.tolist(), "s11_im": s.imag.tolist(),
                                        "zin_re": zin.real.tolist(), "zin_im": zin.imag.tolist(),
                                        "z_ref": z_port}}}}, f, zin


def matrix_bundle():
    b, f, _ = bundle(50.)
    b["results"]["sparams"] = {"ports": [1, 2], "z_ref": [50., 50.], "excited": [1, 2],
        "s": {f"{i},{j}": {"re": [.1 if i == j else .5]*len(f), "im": [0.]*len(f)}
              for i in (1, 2) for j in (1, 2)}}
    return b


class FrequencyReferenceExport(unittest.TestCase):
    fixture = json.loads((ROOT / "python/tests/fixtures/touchstone_references.json").read_text())

    def one_port(self, impedance=600 + 0j):
        refs = np.array(self.fixture["references"][0], float)
        gamma = (impedance - refs) / (impedance + refs)
        b, _, _ = bundle(600.)
        b["ports"][0]["type"] = "waveguide"
        b["results"]["frequency"] = self.fixture["frequency"]
        b["results"]["ports"]["1"] = {"z_ref": 600., "z_ref_f": refs.tolist(),
            "s11_re": gamma.real.tolist(), "s11_im": gamma.imag.tolist()}
        return b

    def through(self, physical=(1, 2)):
        b = self.one_port()
        b["ports"] = [{"number": p, "type": "waveguide", "excite": True} for p in physical]
        refs = np.array(self.fixture["references"], float).T
        a, c = refs.T
        r, t = (c-a)/(a+c), 2*np.sqrt(a*c)/(a+c)
        s = np.array([[r, t], [t, -r]]).transpose(2, 0, 1)
        b["results"]["ports"] = {str(p): {"z_ref": 600., "z_ref_f": refs[:, i].tolist()} for i, p in enumerate(physical)}
        b["results"]["sparams"] = {"ports": [1, 2], "z_ref": [600., 600.], "excited": [1, 2],
            "s": {f"{i+1},{j+1}": {"re": s[:, i, j].tolist(), "im": [0.]*3} for i in range(2) for j in range(2)}}
        if physical != (1, 2):
            b["results"]["sparams"]["port_numbers"] = list(physical)
        return b

    def test_fixed_and_complex_loads_honor_requested_reference(self):
        for load in (600 + 0j, complex(*self.fixture["complexLoad"])):
            for target in (50., 600., 73.):
                f, s, z, _ = reflection(self.one_port(load), z_ref=target)
                self.assertEqual(z, target)
                np.testing.assert_allclose(s, (load-target)/(load+target), atol=2e-15)

    def test_open_short_and_native_keep_semantics(self):
        for value in (-1., 1.):
            b = self.one_port()
            b["results"]["ports"]["1"].update(s11_re=[value]*3, s11_im=[0.]*3)
            np.testing.assert_array_equal(reflection(b, z_ref=50.)[1], [value]*3)
        with self.assertRaisesRegex(ValueError, "frequency-dependent"):
            reflection(self.one_port(), z_ref=None)
        b = self.one_port()
        pr = b["results"]["ports"]["1"]
        pr.update(z_ref=600.123, z_ref_f=[600.123456]*3)
        self.assertEqual(reflection(b, z_ref=None)[2], 600.123456)

    def test_full_power_wave_transform_for_through_and_physical_mapping(self):
        for physical in ((1, 2), tuple(self.fixture["physicalPorts"])):
            for target in (50., 600.):
                with tempfile.TemporaryDirectory() as directory:
                    text = write_snp(self.through(physical), Path(directory)/"through.s2p", z_ref=target)
                t = parse_touchstone(text, "through.s2p")
                self.assertEqual(t.z0, target)
                np.testing.assert_allclose(t.s, np.tile([[0., 1.], [1., 0.]], (3, 1, 1)), atol=1e-14)

    def test_matrix_independent_complex_loads_and_constant_unequal_references(self):
        loads = [600+120j, 340-75j]
        b = self.through((7, 19))
        for i in range(2):
            refs = np.array(self.fixture["references"][i])
            gamma = (loads[i]-refs)/(loads[i]+refs)
            for j in range(2):
                values = gamma if i == j else np.zeros(3, complex)
                b["results"]["sparams"]["s"][f"{i+1},{j+1}"] = {"re": values.real.tolist(), "im": values.imag.tolist()}
        with tempfile.TemporaryDirectory() as directory:
            t = parse_touchstone(write_snp(b, Path(directory)/"loads.s2p", z_ref=600.), "loads.s2p")
        for i in range(2):
            np.testing.assert_allclose(t.s[:, i, i], (loads[i]-600)/(loads[i]+600), atol=2e-15)
            np.testing.assert_array_equal(t.s[:, i, 1-i], 0.)
        b = self.through()
        b["results"]["frequency"] = b["results"]["frequency"][:1]
        b["results"]["sparams"]["z_ref"] = [800., 500.]
        for p in b["ports"]:
            p["type"] = "lumped"
            del b["results"]["ports"][str(p["number"])]["z_ref_f"]
        for values in b["results"]["sparams"]["s"].values():
            values["re"], values["im"] = values["re"][:1], values["im"][:1]
        with tempfile.TemporaryDirectory() as directory:
            t = parse_touchstone(write_snp(b, Path(directory)/"through.s2p", z_ref=73.), "through.s2p")
        np.testing.assert_allclose(t.s, [[[0., 1.], [1., 0.]]], atol=1e-14)

    def test_invalid_reference_vectors_and_mapping_preserve_destination(self):
        validator = TouchstoneExportValidation()
        for invalid in (None, [], [800.], [0., 600., 500.], [-1., 600., 500.],
                        [float("nan"), 600., 500.], [float("inf"), 600., 500.],
                        [True, 600., 500.], [800+1j, 600., 500.]):
            for writer, b in ((write_s1p, self.one_port()), (write_snp, self.through())):
                b["results"]["ports"]["1"]["z_ref_f"] = invalid
                with self.subTest(invalid=invalid, writer=writer.__name__):
                    validator.assert_preserved(writer, b)
                    b["ports"][0]["type"] = "lumped"
                    validator.assert_preserved(writer, b)
        for mapping in (None, [7, 7], [7], [7, 19.5], [True, 19]):
            b = self.through((7, 19))
            if mapping is None:
                del b["results"]["sparams"]["port_numbers"]
            else:
                b["results"]["sparams"]["port_numbers"] = mapping
            validator.assert_preserved(write_snp, b)
        for writer, b in ((write_s1p, self.one_port()), (write_snp, self.through())):
            del b["results"]["ports"]["1"]["z_ref_f"]
            validator.assert_preserved(writer, b)
        validator.assert_preserved(write_snp, self.through(), z_ref=None)
        for invalid in (None, [600.], [True, 600.], ["600", 600.], [600+1j, 600.]):
            b = self.through()
            b["results"]["sparams"]["z_ref"] = invalid
            validator.assert_preserved(write_snp, b)
        for invalid in ([2, 1], [1, 1], [True, 2], [1., 2.]):
            b = self.through((7, 19))
            b["results"]["sparams"]["ports"] = invalid
            validator.assert_preserved(write_snp, b)

    def test_retained_horn_preserves_reconstructed_input_impedance(self):
        b = json.loads((ROOT / "public/projects/pyramidal-horn.json").read_text())
        pr = b["results"]["ports"]["1"]
        original = np.array(pr["s11_re"]) + 1j*np.array(pr["s11_im"])
        expected_z = np.array(pr["z_ref_f"])*(1+original)/(1-original)
        _, s, z, _ = reflection(b, z_ref=50.)
        np.testing.assert_allclose(z*(1+s)/(1-s), expected_z, rtol=1e-12, atol=1e-10)

    def test_matrix_stale_frequency_reference_summaries_rejected(self):
        b = self.through((7, 19))
        sp = b["results"]["sparams"]
        # An external editor re-references two independent 600-ohm loads to 50 ohm but
        # leaves the original native port records behind. Neither reference can be guessed.
        sp["z_ref"] = [50., 50.]
        for key, values in sp["s"].items():
            i, j = key.split(",")
            values["re"] = [(600.-50.)/(600.+50.) if i == j else 0.]*3
        TouchstoneExportValidation().assert_preserved(write_snp, b, z_ref=50.)
        for invalid in (None, float("nan"), True, -600., "600"):
            b = self.through()
            b["results"]["ports"]["1"]["z_ref"] = invalid
            TouchstoneExportValidation().assert_preserved(write_snp, b)

    def test_matrix_rounded_summaries_keep_actual_reference_vector(self):
        b = self.through((7, 19))
        b["results"]["sparams"]["z_ref"] = [600.123, 600.123]
        for pr in b["results"]["ports"].values():
            pr.update(z_ref=600.123, z_ref_f=[600.123456]*3)
        # A through at equal references remains a through for any common target.
        for key, values in b["results"]["sparams"]["s"].items():
            i, j = key.split(",")
            values["re"] = [0. if i == j else 1.]*3
        with tempfile.TemporaryDirectory() as directory:
            text = write_snp(b, Path(directory)/"rounded.s2p", z_ref=None)
        t = parse_touchstone(text, "rounded.s2p")
        self.assertEqual(t.z0, 600.123456)
        np.testing.assert_array_equal(t.s, np.tile([[0., 1.], [1., 0.]], (3, 1, 1)))


class TouchstoneExportValidation(unittest.TestCase):
    def assert_preserved(self, writer, b, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"existing.s2p"
            original = b"previous valid measurement\n"
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                writer(b, path, **kwargs)
            self.assertEqual(path.read_bytes(), original)
            fresh = Path(directory)/"new.s2p"
            with self.assertRaises(ValueError):
                writer(b, fresh, **kwargs)
            self.assertFalse(fresh.exists())

    def test_invalid_requested_and_native_references_preserve_destination(self):
        for bad in (float("nan"), float("inf"), float("-inf"), -50., 0., True, [50.], 10**400):
            for writer, b in ((write_s1p, bundle()[0]), (write_snp, matrix_bundle())):
                with self.subTest(writer=writer.__name__, reference=bad):
                    self.assert_preserved(writer, b, z_ref=bad)
        for bad in (float("nan"), float("inf"), float("-inf"), -50., 0.):
            one = bundle()[0]
            one["results"]["ports"]["1"]["z_ref"] = bad
            self.assert_preserved(write_s1p, one, z_ref=None)
            many = matrix_bundle()
            many["results"]["sparams"]["z_ref"][1] = bad
            self.assert_preserved(write_snp, many)
        many = matrix_bundle()
        many["results"]["sparams"]["z_ref"] = [50.]
        self.assert_preserved(write_snp, many)

    def test_malformed_frequencies_and_samples_cannot_truncate_or_broadcast(self):
        for f in ([], [2e9, 1e9], [1e9]*11, [-1.]+[float(k) for k in range(10)],
                  [float("nan")]*11, [float("inf")]*11, [[1e9]*11]):
            for writer, b in ((write_s1p, bundle()[0]), (write_snp, matrix_bundle())):
                b["results"]["frequency"] = f
                self.assert_preserved(writer, b)
        for samples in ([0.], [0.]*12, [float("nan")]*11, [float("inf")]*11, [[0.]*11]):
            for field in ("s11_re", "s11_im"):
                b = bundle()[0]
                b["results"]["ports"]["1"][field] = samples
                self.assert_preserved(write_s1p, b)
            for field in ("re", "im"):
                b = matrix_bundle()
                b["results"]["sparams"]["s"]["2,1"][field] = samples
                self.assert_preserved(write_snp, b)

    def test_low_level_formatter_validates_the_entire_matrix(self):
        f = np.array([1e9, 2e9])
        for s in (np.zeros((3, 2, 2)), np.zeros((2, 2, 3)), np.zeros((2, 0, 0)),
                  np.zeros((2, 2)), np.full((2, 2, 2), np.inf)):
            with self.assertRaises(ValueError):
                format_snp(f, s, 50., [])
        for bad in (0., -50., np.nan, np.inf):
            with self.assertRaises(ValueError):
                format_snp(f, np.zeros((2, 2, 2)), bad, [])
        parsed = parse_touchstone(format_snp([0., 1e9], np.zeros((2, 2, 2)), 50., []), "dc.s2p")
        np.testing.assert_array_equal(parsed.f, [0., 1e9])

    def test_magnitude_only_reference_is_not_exported_as_known_complex_data(self):
        for writer, b in ((write_s1p, bundle()[0]), (write_snp, matrix_bundle())):
            b["reference"] = {"phaseKnown": False}
            self.assert_preserved(writer, b)
        b = bundle()[0]
        b["reference"] = {"phaseKnown": False}
        with self.assertRaisesRegex(ValueError, "known phase"):
            reflection(b)
        b = matrix_bundle()
        b["reference"] = {"phaseKnown": False}
        with self.assertRaisesRegex(ValueError, "known phase"):
            full_matrix(b)

    def test_cli_invalid_refs_fail_without_overwrite_and_zero_keeps_native(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory)/"input.json", Path(directory)/"result.s1p"
            source.write_text(json.dumps(bundle(73.)[0]), encoding="utf-8")
            for ref in ("nan", "-50", "-inf"):
                destination.write_text("original", encoding="utf-8")
                result = subprocess.run([sys.executable, "-m", "fairbeam", "touchstone", str(source),
                    "--output", str(destination), f"--ref={ref}"], cwd=ROOT/"python", capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("finite and positive", result.stderr)
                self.assertEqual(destination.read_text(), "original")
            result = subprocess.run([sys.executable, "-m", "fairbeam", "touchstone", str(source),
                "--output", str(destination), "--ref=0"], cwd=ROOT/"python", capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read_touchstone(destination).z0, 73.)


class TouchstoneTest(unittest.TestCase):
    def test_open_short_and_load_renormalization(self):
        b, _, _ = bundle(75.0)
        b["results"]["frequency"] = [1e9, 2e9, 3e9]
        p = b["results"]["ports"]["1"]
        p["s11_re"], p["s11_im"] = [1.0, -1.0, 0.0], [0.0] * 3
        with np.errstate(all="raise"):
            _, s, _, _ = reflection(b, z_ref=50.0)
        np.testing.assert_allclose(s, [1.0, -1.0, 0.2], rtol=0, atol=1e-15)

    def test_narrow_sweep_and_small_signal_round_trip(self):
        f = np.array([1e9 + 0.125, 1e9 + 0.25])
        values = np.array([1.234567890123456e-12 - 3.456789012345678e-13j,
                           -2.345678901234567e-14 + 4.567890123456789e-15j])
        z = 50.1234567890123
        b, _, _ = bundle(z)
        b["results"]["frequency"] = f.tolist()
        b["results"]["ports"]["1"]["s11_re"] = values.real.tolist()
        b["results"]["ports"]["1"]["s11_im"] = values.imag.tolist()
        with tempfile.TemporaryDirectory() as d:
            text = write_s1p(b, Path(d) / "precision.s1p", z_ref=None)
        parsed = parse_touchstone(text, "precision.s1p")
        self.assertEqual(parsed.z0, z)
        np.testing.assert_allclose(parsed.f, f, rtol=2 * np.finfo(float).eps, atol=0)
        np.testing.assert_array_equal(parsed.s[:, 0, 0], values)
        for n in [1, 2, 3]:
            with self.subTest(ports=n):
                matrix = np.broadcast_to(values[:, None, None], (2, n, n))
                parsed = parse_touchstone(format_snp(f, matrix, z, []), f"precision.s{n}p")
                self.assertEqual(parsed.z0, z)
                np.testing.assert_allclose(parsed.f, f, rtol=2 * np.finfo(float).eps, atol=0)
                np.testing.assert_array_equal(parsed.s, matrix)

    def test_renormalise_to_50(self):
        b, f, zin = bundle(73.0)
        _, s, z, key = reflection(b, z_ref=50.0)
        self.assertEqual((z, key), (50.0, "1"))
        np.testing.assert_allclose(s, (zin - 50) / (zin + 50), atol=1e-12)

    def test_native_reference(self):
        b, f, zin = bundle(73.0)
        _, s, z, _ = reflection(b, z_ref=None)
        self.assertEqual(z, 73.0)
        np.testing.assert_allclose(s, (zin - 73) / (zin + 73), atol=1e-12)

    def test_round_trip(self):
        b, f, zin = bundle(50.0)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.s1p"
            text = write_s1p(b, path)
            self.assertIn("# GHz S RI R 50", text)
            f2, s2, z2 = read_s1p(path)
        self.assertEqual(z2, 50.0)
        np.testing.assert_allclose(f2, f, rtol=1e-9)
        np.testing.assert_allclose(s2, (zin - 50) / (zin + 50), atol=1e-8)

    def test_committed_patch_bundle(self):
        b = json.loads(PATCH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.s1p"
            write_s1p(b, path)
            f, s, _ = read_s1p(path)
        self.assertEqual(len(f), len(b["results"]["frequency"]))
        k = int(np.argmin(np.abs(s)))
        self.assertAlmostEqual(f[k], b["results"]["bands"][0]["f_center"], delta=5e6)

    def test_errors(self):
        with self.assertRaises(ValueError):
            reflection({"results": None})
        b, _, _ = bundle()
        with self.assertRaises(ValueError):
            reflection(b, port=2)


class TouchstoneReaderTest(unittest.TestCase):
    """The fixtures and their closed-form answers are shared with src/import/touchstone.ts."""

    def test_fixtures(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["files"]
        self.assertGreaterEqual(len(expected), 17)
        for name, e in expected.items():
            with self.subTest(name):
                if "error" in e:
                    with self.assertRaisesRegex(ValueError, re.escape(e["error"])):
                        read_touchstone(FIXTURES / name)
                    continue
                t = read_touchstone(FIXTURES / name)
                want = np.array(e["s"], float)
                self.assertEqual((t.ports, t.z0), (e["ports"], e["z0"]))
                np.testing.assert_allclose(t.f, e["f"], rtol=1e-12)
                np.testing.assert_allclose(t.s, want[..., 0] + 1j * want[..., 1], atol=e.get("tol", 1e-9))
                self.assertEqual(t.noise_frequencies, e.get("noise", 0))
                for w in e.get("warn", []):
                    self.assertTrue(any(w in x for x in t.warnings), f"no warning with {w!r} in {t.warnings}")
                _, s, _ = read_snp(FIXTURES / name)
                np.testing.assert_array_equal(s, t.s)

    def test_known_networks(self):
        # matched load, series and shunt 50 ohm resistors, spelled out
        _, s11, _ = read_s1p(FIXTURES / "z_matched_load.s1p")
        self.assertAlmostEqual(abs(s11[0]), 0.0, places=12)
        _, s, _ = read_snp(FIXTURES / "y_series_50ohm.s2p")
        np.testing.assert_allclose(s[0], [[1 / 3, 2 / 3], [2 / 3, 1 / 3]], atol=1e-12)
        _, s, _ = read_snp(FIXTURES / "z_shunt_resistor.s2p")
        np.testing.assert_allclose(s[0], [[-1 / 3, 2 / 3], [2 / 3, -1 / 3]], atol=1e-12)

    def test_read_s1p_refuses_multiport(self):
        with self.assertRaisesRegex(ValueError, "2-port data"):
            read_s1p(FIXTURES / "v2_order_12_21.ts")

    def test_errors(self):
        head = "[Version] 2.0\n# GHz S RI R 50\n[Number of Ports] 1\n"
        cases = {
            head + "[Number of Frequencies] 3\n1 0 0\n2 0 0\n": r"\[Number of Frequencies\] is 3, but the file has 2",
            head + "[Matrix Format] Diagonal\n1 0 0\n": r"Invalid \[Matrix Format\]",
            head + "[Mixed-Mode Order] D1,2 C1,2\n1 0 0\n": "Mixed-mode",
            "[Version] 2.0\n[Reference] 50\n[Number of Ports] 1\n1 0 0\n": "must come after",
            "[Version] 2.0\n[Number of Ports] 2\n[Reference] 50\n[Two-Port Data Order] 12_21\n1 0 0 0 0 0 0 0 0\n": "needs 2 values",
            "[Version] 3.0\n1 0 0\n": "not supported",
            "# GHz S RI R -5\n1 0 0\n": "Invalid reference impedance",
            "# GHz S RI R 50\n1 0 0 x\n": "Not a number",
            "# GHz S RI R 50\n1 0 0\n2 0\n": "does not fit 1 port",
            "# GHz S RI R 50\n! nothing\n": "No Touchstone network data",
            "# GHz S RI R 50\n1 0 0 0 0\n": "Cannot tell the port count",
        }
        for text, msg in cases.items():
            with self.subTest(msg), self.assertRaisesRegex(ValueError, msg):
                parse_touchstone(text, "x.ts")

    def test_misc_rules(self):
        # [Begin Information] blocks are skipped, unknown keywords warned about, only the first
        # option line counts, commas and Fortran exponents are accepted
        t = parse_touchstone("[Version] 2.1\n# MHz S RI R 50\n# GHz Z MA R 75\n[Number of Ports] 1\n"
                             "[Begin Information]\n[Anything] 1 2 3\n[End Information]\n[Frobnicate] x\n"
                             "[Network Data]\n100, 0.5D-00, 0.25\n[End]\n", "x.ts")
        self.assertEqual((t.f[0], t.s[0, 0, 0], t.z0, t.unit), (1e8, 0.5 + 0.25j, 50.0, "MHz"))
        self.assertTrue(any("Frobnicate" in w for w in t.warnings))
        # an explicit n must agree with [Number of Ports]
        with self.assertRaisesRegex(ValueError, "asked for"):
            parse_touchstone("[Number of Ports] 1\n1 0 0\n", "x.ts", n=2)
        # a v1 2-port frequency that drops back without 5-number lines is not a noise block
        with self.assertRaisesRegex(ValueError, "not strictly increasing"):
            parse_touchstone("# GHz S RI R 50\n2 0 0 0 0 0 0 0 0\n1 0 0 0 0 0 0 0 0\n", "x.s2p")
        # v1 Z-parameters are normalised to R: z = 1 at R 75 is a matched 75 ohm load
        t = parse_touchstone("# GHz Z RI R 75\n1 1 0\n", "x.s1p")
        self.assertEqual((t.z0, abs(t.s[0, 0, 0])), (75.0, 0.0))


if __name__ == "__main__":
    unittest.main()
