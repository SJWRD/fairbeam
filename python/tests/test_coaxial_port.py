import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from fairbeam.coax import resolved_coax, validate_coax
from fairbeam.design import build, evaluate, to_python
from fairbeam.design_checks import lint
from fairbeam.example_design import ExampleConversionError, convert_example
from fairbeam.model import load_model
from fairbeam.simulation import Simulation, _time_signals


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

    def test_time_signals_read_the_coax_time_axis_from_u_data(self):
        # openEMS' CoaxialPort has no u_time; evaluate() must still write the time signals
        t = np.linspace(0, 1e-9, 11)

        class UI:
            ui_time = [t]

        class FakeCoaxPort:
            u_data, Z_ref = UI(), 50.0
            ut_inc = ut_ref = ut_tot = it_tot = np.zeros_like(t)

        sig = _time_signals(FakeCoaxPort())
        self.assertEqual(sig["samples"], 11)
        self.assertAlmostEqual(sig["dt_s"], 1e-10)
        self.assertEqual(_time_signals(type("NoData", (), {})()), {})

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


# A Python model with a native coaxial port feeding a dielectric load. Its transverse lines have no
# line on the centerline (x = y = 0): the native port does not need one.
COAX_MODEL = """
import numpy as np
from fairbeam import Param, Simulation

MODEL = {"id": "coax-control", "name": "Coax control"}
PARAMS = [Param("r_out", 3.0, "Shield inner radius", "mm", minimum=2.5, maximum=3.4),
          Param("L", 30.0, "Line length", "mm", minimum=24, maximum=36)]


def build(p):
    sim = Simulation(1e9, 2e9, boundaries=("PEC",) * 4 + ("PML_8",) * 2)
    sim.dielectric("load", 4.0, tan_d=0.01, tan_d_freq=1.5e9).AddBox([-4, -4, 0], [4, 4, 10], priority=1)
    sim.coaxial_port(1, [0, 0, -p["L"]], [0, 0, 0], "z", 1, p["r_out"], 3.5, dielectric="PTFE", eps_r=2.1,
                     feed_shift=10, measurement_shift=20)
    for axis in "xy":
        sim.mesh.AddLine(axis, np.arange(-4.125, 4.2, .25))
    sim.mesh.AddLine("z", np.arange(-p["L"], 31, 1.0))
    return sim
"""


class CoaxialConversionTests(unittest.TestCase):
    """A Python model with a native coaxial port becomes an editable design and back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "coax_control.py"

    def convert(self, source=COAX_MODEL):
        self.path.write_text(source, encoding="utf-8")
        return convert_example(self.path, "coax_control", "Coax control")

    def native(self, values=None):
        module = load_model(self.path)
        sim = module.build({**{p.key: p.default for p in module.PARAMS}, **(values or {})})
        sim.finalize_coaxial_ports()
        return sim

    def test_port_materials_and_parameters_are_carried(self):
        d = self.convert()
        port, = d["ports"]
        self.assertEqual({k: port[k] for k in ("type", "mode", "direction", "r_i", "r_o", "r_os", "feed_shift",
                                               "measurement_shift", "conductor", "dielectric", "termination", "excite")},
                         dict(type="coaxial", mode="TEM", direction="z", r_i=1.0, r_o="r_out", r_os=3.5, feed_shift=10.0,
                              measurement_shift=20.0, conductor="PEC", dielectric="PTFE", termination="open", excite=True))
        self.assertEqual(port["start"], [0, 0, "-L"])
        self.assertNotIn("R", port)
        materials = {m["name"]: m for m in d["materials"]}
        self.assertEqual(materials["PEC"], {"name": "PEC", "kind": "metal"})
        self.assertEqual(materials["PTFE"], {"name": "PTFE", "kind": "dielectric", "eps_r": "2.1", "tan_d": "0"})
        # the port's own conductor and filling are not copied as solids: the port creates them
        self.assertEqual([part["name"] for part in d["parts"]], ["load"])
        self.assertEqual(d["mesh"]["mode"], "manual")
        self.assertNotIn("automatic", d["mesh"])
        self.assertIn("coaxMesh", [n["code"] for n in d["model"]["conversion"]["notes"]])
        self.assertFalse([c for c in lint(d) if c["severity"] == "error"])

    def test_design_and_exported_python_rebuild_the_native_port(self):
        d = json.loads(json.dumps(self.convert()))
        for values in ({}, {"r_out": 2.7, "L": 29.0}):
            with self.subTest(values=values):
                original = self.native(values)
                rebuilt = build(d, values)
                self.assertEqual(type(rebuilt._port_objs[0]).__name__, "CoaxialPort")
                if values:
                    # the design's lines stretch with the moved geometry, so the probe planes may
                    # snap elsewhere than on the model's own grid; the port itself is the same
                    for ports in (rebuilt.ports, original.ports):
                        for key in ("reference_plane", "actual_measurement_shift"):
                            ports[0].pop(key)
                self.assertEqual(rebuilt.ports, original.ports)
                for name in ("Coaxial port 1 conductor", "Coaxial port 1 dielectric"):
                    self.assertEqual(rebuilt.csx.GetPropertiesByName(name)[0].GetQtyPrimitives(),
                                     original.csx.GetPropertiesByName(name)[0].GetQtyPrimitives())
        self.path.write_text(to_python(d), encoding="utf-8")
        self.assertEqual(self.native().ports, build(d, {}).ports)

    def test_a_different_material_of_the_same_name_is_kept_apart(self):
        source = COAX_MODEL.replace('sim.dielectric("load", 4.0', 'sim.dielectric("PTFE", 4.0')
        d = self.convert(source)
        materials = {m["name"]: m for m in d["materials"]}
        self.assertEqual(materials["PTFE"]["eps_r"], "4.0")
        self.assertEqual(d["ports"][0]["dielectric"], "PTFE 2")
        self.assertEqual(materials["PTFE 2"]["eps_r"], "2.1")
        self.assertEqual(build(d, {}).ports[0]["eps_r"], 2.1)

    def test_changed_generated_geometry_is_refused(self):
        source = COAX_MODEL.replace("    for axis in \"xy\":", "    sim.csx.GetPropertiesByName(\"Coaxial port 1 dielectric\")[0]"
                                    ".AddBox([-1, -1, 1], [1, 1, 2], priority=4)\n    for axis in \"xy\":")
        with self.assertRaisesRegex(ExampleConversionError, "generated geometry"):
            self.convert(source)
        source = COAX_MODEL.replace("    return sim", "    sim.finalize_coaxial_ports()\n    return sim")
        with self.assertRaisesRegex(ExampleConversionError, "finalized"):
            self.convert(source)

    def test_manual_lines_need_only_the_axial_ends(self):
        d = design()
        d["mesh"]["lines"].update(x=np.arange(-4.125, 4.2, .25).tolist(), y=np.arange(-4.125, 4.2, .25).tolist())
        self.assertEqual(build(d, {}).ports[0]["reference_plane"], -10)
        d["mesh"]["lines"]["z"].remove(0)
        with self.assertRaisesRegex(ValueError, "mesh line"):
            build(d, {})


if __name__ == "__main__":
    unittest.main()
