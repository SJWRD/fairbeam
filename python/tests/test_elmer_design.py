"""Focused Design-to-Elmer contract checks; native smoke is recorded separately."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairbeam.design import blank_design
from fairbeam import elmer_design, research


def design():
    d = blank_design("fem-run", "FEM run")
    d["ports"] = []
    d["materials"] = [{"name": "air", "kind": "dielectric", "eps_r": 1}]
    d["parts"] = [{"name": "volume", "material": "air", "primitives": [
        {"kind": "box", "start": [0, 0, 0], "stop": [100, 50, 200]}]}]
    return d


SETTINGS = dict(domain_mm=[0, 0, 0, 100, 50, 200], max_cell_mm=25, target_hz=1.8e9, closed_pec=True)


class ElmerDesignTests(unittest.TestCase):
    def test_mesh_units_and_contiguous_sif_bodies(self):
        mesh = elmer_design.prepare(design(), SETTINGS)
        self.assertEqual(mesh.cell_counts, (4, 2, 8))
        self.assertEqual(max(p[2] for p in mesh.nodes), .2)
        text = elmer_design.sif(mesh, SETTINGS["target_hz"])
        self.assertIn("Body 1\n Target Bodies(1) = 2", text)
        self.assertIn("Material 1\n Relative Permittivity = 1", text)
        self.assertNotIn("Body 2\n", text)

    def test_material_and_geometry_are_not_fixed_fixture(self):
        d = design()
        d["materials"][0]["eps_r"] = 4
        d["parts"][0]["primitives"][0]["stop"] = [80, 40, 160]
        settings = {**SETTINGS, "domain_mm": [0, 0, 0, 80, 40, 160], "max_cell_mm": 20}
        mesh = elmer_design.prepare(d, settings)
        self.assertEqual(max(p[2] for p in mesh.nodes), .16)
        self.assertIn("Relative Permittivity = 4", elmer_design.sif(mesh, settings["target_hz"]))

    def test_unsupported_port_loss_and_unacknowledged_boundary_rejected(self):
        d = design()
        for mutation in (lambda x: x.update(ports=blank_design("x", "x")["ports"]),
                         lambda x: x["materials"][0].update(tan_d=.01)):
            candidate = copy.deepcopy(d); mutation(candidate)
            with self.assertRaises(ValueError): elmer_design.prepare(candidate, SETTINGS)
        with self.assertRaises(ValueError): elmer_design.prepare(d, {**SETTINGS, "closed_pec": False})

    def test_resource_and_target_limits(self):
        for settings in ({**SETTINGS, "max_cell_mm": 5}, {**SETTINGS, "target_hz": float("nan")},
                         {**SETTINGS, "target_hz": True}, {**SETTINGS, "max_cell_mm": 100}):
            with self.assertRaises(ValueError): elmer_design.prepare(design(), settings)

    def test_exported_modes_never_claim_physical_validation(self):
        r = dict(schema=elmer_design.SCHEMA, backend="elmer", research_schema="fairbeam-research-result-1",
                 status="results_exported", frequency_hz=[1e9, 1e9], s_parameters=None,
                 qualifies_driven_antenna=False, precision_converged=False,
                 checks=dict(solver_completed=True, finite_nonzero_field=True))
        self.assertTrue(research.completed_result(research.validate_result(r, "elmer")))
        with self.assertRaises(ValueError): research.validate_result({**r, "status": "results_validated"}, "elmer")
        with self.assertRaises(ValueError): research.validate_result({**r, "frequency_hz": [float("nan"), 1e9]}, "elmer")
        self.assertFalse(research.completed_result({"backend": "periodic", "status": "results_exported"}))


if __name__ == "__main__": unittest.main()
