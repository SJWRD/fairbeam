import unittest

import numpy as np
from fairbeam.coax import resolved_coax, validate_coax
from fairbeam.design import build, evaluate, to_python
from fairbeam.design_checks import lint
from fairbeam.simulation import Simulation


def design():
    return dict(schema="fairbeam.design/1", model=dict(id="coax_test", name="Coax test"), params=[],
                materials=[dict(name="PEC", kind="metal")], parts=[],
                ports=[dict(number=1, type="coaxial", mode="TEM", direction="z", start=[0, 0, -30], stop=[0, 0, 0],
                            r_i=1, r_o=3, r_os=3.5, conductor="PEC", dielectric=None,
                            feed_shift=10, measurement_shift=20, termination="open", excite=True)],
                simulation=dict(f_min=1, f_max=2, boundaries=["PEC"] * 4 + ["PML_8"] * 2),
                mesh=dict(mode="manual", lines={**{a: np.arange(-4, 4.01, .25).tolist() for a in "xy"}, "z": list(range(-30, 31))}),
                far_field=dict(enabled=False))


class CoaxialPortTests(unittest.TestCase):
    def test_native_build_after_mesh_and_bundle(self):
        d = design()
        sim = build(d, {})
        self.assertEqual(type(sim._port_objs[0]).__name__, "CoaxialPort")
        self.assertEqual(sim.ports[0]["reference_plane"], -10)
        self.assertEqual(sim.ports[0]["conductor"], "PEC")
        bundle = sim.to_bundle(d["model"], [])
        self.assertEqual(bundle["ports"][0]["type"], "coaxial")
        self.assertEqual(len(bundle["parts"]), 1)
        self.assertEqual(len(bundle["parts"][0]["primitives"]), 2)
        sim.finalize_coaxial_ports()
        self.assertEqual(len(sim._port_objs), 1)

    def test_invalid_input_never_becomes_lumped(self):
        for changes in [dict(direction=None), dict(direction=1), dict(direction=""), dict(r_i=0), dict(r_os=3), dict(stop=[1, 0, 0]), dict(measurement_shift=30),
                        dict(feed_shift=25), dict(R=50), dict(Feed_R=50), dict(termination="matched"),
                        dict(mode="TE10"), dict(group={}), dict(reference_impedance=dict(real=50, imag=0)), dict(r_i=2.5)]:
            port = {**design()["ports"][0], **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_coax(port)

    def test_materials_and_parameter_resolution(self):
        d = design()
        d["materials"].append(dict(name="PTFE", kind="dielectric", eps_r=2.1))
        d["ports"][0].update(dielectric="PTFE", r_i="radius")
        resolved = resolved_coax(d["ports"][0], d["materials"], lambda x, path: evaluate(x, {"radius": 1}))
        self.assertEqual(resolved["eps_r"], 2.1)
        d["materials"][0]["conductivity"] = 5.8e7
        with self.assertRaisesRegex(ValueError, "PEC"):
            resolved_coax(d["ports"][0], d["materials"], lambda x, path: evaluate(x, {"radius": 1}))

    def test_mesh_and_pml_checks(self):
        for edit in (lambda d: d["mesh"]["lines"].update(x=list(range(-4, 5))),
                     lambda d: d["ports"][0].update(feed_shift=2),
                     lambda d: d["simulation"].update(boundaries=["PML_8"] * 6),
                     lambda d: d["ports"][0].update(measurement_shift=29)):
            d = design()
            edit(d)
            with self.assertRaises(ValueError):
                build(d, {})

    def test_generated_code_keeps_native_type(self):
        code = to_python(design())
        self.assertIn("sim.coaxial_port(", code)
        self.assertNotIn("sim.lumped_port(", code)
        compile(code, "coax.py", "exec")
        namespace = {}
        exec(code, namespace)
        generated = namespace["build"]({})
        generated.finalize_coaxial_ports()
        self.assertEqual(generated.ports, build(design(), {}).ports)
        unsupported = design()
        unsupported["ports"][0]["Feed_R"] = 50
        with self.assertRaisesRegex(ValueError, "finite feed resistance"):
            to_python(unsupported)

    def test_nonuniform_probe_stencil_is_rejected(self):
        d = design()
        d["mesh"]["lines"]["z"].remove(-9)
        with self.assertRaisesRegex(ValueError, "equally spaced"):
            build(d, {})

    def test_narrow_annulus_is_rejected_before_creating_geometry(self):
        sim = Simulation(1e9, 2e9)
        with self.assertRaisesRegex(ValueError, "square current probe"):
            sim.coaxial_port(1, [0, 0, -20], [0, 0, 20], "z", 2.5, 3, 3.5, measurement_shift=15)
        self.assertEqual(sim.ports, [])
        self.assertEqual(sim.csx.GetQtyPrimitives(), 0)

    def test_short_inside_pml_is_rejected(self):
        d = design()
        d["ports"][0]["termination"] = "short"
        with self.assertRaisesRegex(ValueError, "short termination.*PML"):
            build(d, {})

    def test_lint_does_not_require_separate_conductor_solid(self):
        checks = lint(design())
        self.assertTrue(any(c["code"] == "coax-mesh" and c["severity"] == "warning" for c in checks))
        self.assertFalse(any(c["code"] in ("no-metal", "port-floating", "port-in-metal", "coax-port") for c in checks), checks)

    def test_all_cartesian_axes_and_reversed_lines(self):
        for axis, direction in enumerate("xyz"):
            for sign in (-1, 1):
                with self.subTest(axis=direction, sign=sign):
                    sim = Simulation(1e9, 2e9, boundaries=["PEC"] * 6)
                    a, b = [0, 0, 0], [0, 0, 0]
                    a[axis], b[axis] = -20 * sign, 20 * sign
                    sim.coaxial_port(1, a, b, direction, 1, 3, 3.5, feed_shift=5, measurement_shift=15, termination="short")
                    for k in range(3):
                        sim.mesh.AddLine(k, np.arange(-25, 26) if k == axis else np.arange(-4, 4.01, .25))
                    sim.finalize_coaxial_ports()
                    self.assertEqual(sim.ports[0]["reference_plane"], -5 * sign)
                    self.assertEqual(sim.csx.GetPropertiesByName("Coaxial port 1 conductor")[0].GetQtyPrimitives(), 4)

    def test_unsupported_runtime_and_coordinates_fail_before_geometry(self):
        from types import SimpleNamespace
        sim = Simulation(1e9, 2e9)
        sim.fdtd = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, "AddCoaxialPort"):
            sim.coaxial_port(1, [0, 0, -10], [0, 0, 10], "z", 1, 3, 3.5, measurement_shift=10)
        self.assertEqual(sim.ports, [])
        sim = Simulation(1e9, 2e9)
        sim.mesh.SetMeshType(1)
        with self.assertRaisesRegex(ValueError, "Cartesian"):
            sim.coaxial_port(1, [0, 0, -10], [0, 0, 10], "z", 1, 3, 3.5, measurement_shift=10)


    def test_gpu_run_refuses_unqualified_coax_before_execution(self):
        sim = Simulation(1e9, 2e9)
        sim.coaxial_port(1, [0, 0, -10], [0, 0, 10], "z", 1, 3, 3.5, measurement_shift=10)
        with self.assertRaisesRegex(ValueError, "CPU runs only"):
            sim.run("must-not-be-created", engine="gpu")

    def test_filling_preserves_native_material_and_priority(self):
        sim = Simulation(1e9, 2e9, boundaries=["PEC"] * 6)
        args = (1, [0, 0, -20], [0, 0, 20], "z", 1, 3, 3.5)
        with self.assertRaisesRegex(ValueError, "name a dielectric"):
            sim.coaxial_port(*args, eps_r=2.1, measurement_shift=15)
        sim.coaxial_port(*args, dielectric="PTFE", eps_r=2.1, measurement_shift=15, feed_shift=5)
        staged = sim.csx.GetPropertiesByName("Coaxial port 1 dielectric")[0]
        self.assertEqual(staged.GetMaterialProperty("epsilon"), 2.1)
        self.assertEqual(staged.GetPrimitive(0).GetPriority(), 4)
        for k in range(3):
            sim.mesh.AddLine(k, np.arange(-25, 26) if k == 2 else np.arange(-4, 4.01, .25))
        sim.finalize_coaxial_ports()
        final = sim.csx.GetPropertiesByName("Coaxial port 1 dielectric")[0]
        self.assertEqual(final.GetMaterialProperty("epsilon"), 2.1)
        self.assertEqual(final.GetPrimitive(0).GetPriority(), 4)
        self.assertEqual(final.GetQtyPrimitives(), 1)


if __name__ == "__main__":
    unittest.main()
