"""Native planar-port construction and metadata checks, without solving."""
import copy
import json
import unittest
from types import SimpleNamespace
import numpy as np
from fairbeam.design import build, to_python
from fairbeam.design_checks import lint
from fairbeam.planar import validate_planar
from fairbeam.simulation import Simulation


def design(kind="microstrip", reverse=False):
    def box(name,a,b,material):
        return dict(name=name,material=material,primitives=[dict(kind="box",start=a,stop=b,priority=10 if material=="metal" else 0)])
    port=dict(type=kind,number=1,conductor="metal",start=[30 if reverse else -30,-1,.8],
              stop=[5 if reverse else -5,1,0 if kind=="microstrip" else .8],direction="x",
              excitation_direction="z" if kind=="microstrip" else "y",gap=.6,
              feed_shift=6,measurement_shift=18,reference_ohm=50,termination="pml",excite=True)
    parts=[box("substrate",[-30,-6,0],[30,6,.8],"substrate")]
    if kind=="microstrip":parts.append(box("ground",[-30,-6,0],[30,6,0],"metal"))
    else:
        parts += [box("left",[-30,-6,.8],[30,-1.6,.8],"metal"),
                  box("right",[-30,1.6,.8],[30,6,.8],"metal")]
    return dict(schema="fairbeam.design/1",model=dict(id="planar_fixture",name="Planar fixture"),params=[],
                materials=[dict(name="metal",kind="metal"),dict(name="substrate",kind="dielectric",eps_r=3.55)],
                parts=parts,ports=[port],simulation=dict(f_min=1,f_max=2,boundaries=["PML_8"]*6),
                mesh=dict(mode="manual",lines=dict(x=np.arange(-30,30.01,.5).tolist(),
                    y=np.arange(-12,12.01,.2).tolist(),z=np.arange(-6,6.01,.2).tolist())),far_field=dict(enabled=False))


class PlanarPortTests(unittest.TestCase):
    def test_native_probe_clock_must_be_finite_and_increasing(self):
        from unittest.mock import patch
        from fairbeam.planar import NativePlanarPort
        native = SimpleNamespace(CalcPort=lambda *args, **kwargs: None,
            Z_ref=np.ones(3)*50, U_filenames=["u"], I_filenames=["i"],
            ut_tot=np.ones(3), it_tot=np.ones(3))
        port = NativePlanarPort(native, 50)
        for clock in ([0, 1, float("nan")], [0, 1, float("inf")], [0, 1, 1]):
            with self.subTest(clock=clock), patch("numpy.loadtxt", return_value=np.array(clock)):
                with self.assertRaisesRegex(ValueError, "time axes"):
                    port.CalcPort("unused", [1, 2, 3])
        with patch("numpy.loadtxt", return_value=np.array([0., 1., 2.])):
            port.CalcPort("unused", [1, 2, 3])
            np.testing.assert_array_equal(port.u_time, [0, 1, 2])
            np.testing.assert_array_equal(port.i_time, [0, 1, 2])

    def test_native_kind_reference_plane_and_reversed_line(self):
        for kind in ("microstrip","cpw"):
            for reverse in (False,True):
                with self.subTest(kind=kind,reverse=reverse):
                    d=design(kind,reverse);sim=build(d,{})
                    rec=sim.ports[0]
                    self.assertEqual(type(sim._port_objs[0].native).__name__,"MSLPort" if kind=="microstrip" else "CPWPort")
                    self.assertAlmostEqual(rec["reference_plane"],12 if reverse else -12)
                    self.assertAlmostEqual(rec["actual_source_plane"],24 if reverse else -24)
                    self.assertEqual(rec["R"],50)
                    before=sim.csx.GetQtyPrimitives();sim.finalize_planar_ports()
                    self.assertEqual(sim.csx.GetQtyPrimitives(),before)
                    self.assertEqual(json.loads(json.dumps(sim.to_bundle(d["model"],[])))["ports"][0]["type"],kind)

    def test_design_checks_and_python_export_preserve_native_kind(self):
        for kind in ("microstrip","cpw"):
            d=design(kind)
            self.assertEqual([x for x in lint(d) if x["severity"]=="error"],[])
            ns={};exec(to_python(d),ns)
            sim=ns["build"]({});sim.finalize_planar_ports()
            self.assertEqual(sim.ports[0]["type"],kind)
            self.assertEqual(sim.ports[0]["reference_ohm"],50)

    def test_python_to_design_refuses_instead_of_dropping_native_feeds(self):
        from fairbeam.example_design import ExampleConversionError, _check_reconstructible_ports
        sim=Simulation(1e9,2e9)
        sim.microstrip_port(1,[-30,-1,.8],[-5,1,0],"x","z",feed_shift=6,measurement_shift=18)
        with self.assertRaisesRegex(ExampleConversionError,"port type 'microstrip' is not representable"):
            _check_reconstructible_ports(sim)

    def test_invalid_inputs_never_become_discrete_ports(self):
        p=design()["ports"][0]
        for changes in (dict(excitation_direction="x"),dict(reference_ohm=0),dict(feed_shift=20),
                        dict(measurement_shift=25),dict(start=[0,0,0]),dict(group={}),
                        dict(reference_impedance=dict(real=50,imag=0)),dict(termination="matched")):
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_planar({**p,**changes})
        with self.assertRaises(ValueError):validate_planar({**design("cpw")["ports"][0],"gap":0})

    def test_missing_native_api_is_explicit(self):
        sim=Simulation(1e9,2e9);sim.fdtd=SimpleNamespace()
        with self.assertRaisesRegex(ValueError,"no native AddMSLPort"):
            sim.microstrip_port(1,[-1,0,1],[1,10,0],"y","z",measurement_shift=5)
        self.assertEqual(sim.ports,[])

    def test_pml_termination_is_not_an_unrecorded_open_stub(self):
        d=design();d["ports"][0]["start"][0]=-20
        d["ports"][0]["measurement_shift"]=10
        with self.assertRaisesRegex(ValueError,"physical strip start inside"):
            build(d,{})

    def test_lossy_sheet_and_real_output_reference_are_recorded(self):
        d=design();d["materials"][0].update(conductivity=5.8e7,thickness=.035)
        d["ports"][0]["reference_ohm"]=75
        sim=build(d,{});rec=sim.ports[0]
        self.assertEqual(rec["R"],75);self.assertEqual(rec["conductivity"],5.8e7)
        self.assertEqual(rec["termination"],"pml")
        self.assertTrue(any(p["type"]=="ConductingSheet" for p in sim.to_bundle(d["model"],[])["parts"]))

    def test_missing_ground_and_shorted_gap_are_rejected(self):
        for kind in ("microstrip","cpw"):
            d=design(kind);d["parts"]=d["parts"][:1]
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,"matching ground"):
                build(d,{})
        d=design("cpw")
        d["parts"].append(dict(name="short",material="metal",primitives=[dict(kind="box",start=[-30,-2,.8],stop=[30,2,.8],priority=20)]))
        with self.assertRaisesRegex(ValueError,"shorted by metal"):build(d,{})

    def test_automatic_meshing_retains_symmetric_measurement_planes(self):
        from fairbeam.planar import align_automatic_probes
        sim=Simulation(1e9,2e9)
        sim.microstrip_port(1,[-30,-1,.8],[-5,1,0],"x","z",feed_shift=6,measurement_shift=18)
        sim.mesh.SetLines("x",[-30,-20,-12.4,-12,-11.8,-5,10])
        align_automatic_probes(sim)
        lines=np.asarray(sim.mesh.GetLines("x"));i=np.argmin(abs(lines+12))
        self.assertTrue(np.allclose(lines[i-1:i+2],[-12.2,-12,-11.8]))
        self.assertEqual(len(lines),7)
        self.assertNotIn(-12.4,lines)
        before=lines.copy();align_automatic_probes(sim)
        self.assertTrue(np.array_equal(before,sim.mesh.GetLines("x")))

    def test_automatic_probe_alignment_does_not_insert_a_sliver(self):
        from fairbeam.planar import align_automatic_probes
        for left,right in ((.07,.072),(.1,2.),(2.,.1)):
            with self.subTest(left=left,right=right):
                sim=Simulation(1e9,2e9)
                sim.cpw_port(1,[-1,-30,.8],[1,-5,.8],"y","x",.6,
                             feed_shift=6,measurement_shift=18)
                original=np.array([-30,-20,-12-left,-12,-12+right,-5,10])
                sim.mesh.SetLines("y",original)
                align_automatic_probes(sim,protected=[[],[-30,-20,-12,-5,10],[]])
                result=np.asarray(sim.mesh.GetLines("y"))
                self.assertEqual(len(result),len(original))
                self.assertGreaterEqual(min(np.diff(result)),min(np.diff(original))-1e-12)
                self.assertTrue(np.allclose(result[2:5],[-12-min(left,right),-12,-12+min(left,right)]))

    def test_probe_alignment_preserves_protected_planes_and_is_atomic(self):
        from fairbeam.planar import align_automatic_probes
        sim=Simulation(1e9,2e9)
        sim.microstrip_port(1,[-30,-1,.8],[-5,1,0],"x","z",feed_shift=6,measurement_shift=18)
        original=np.array([-30,-20,-12.4,-12,-11.8,-5,10])
        sim.mesh.SetLines("x",original)
        with self.assertRaisesRegex(ValueError,"protected mesh plane"):
            align_automatic_probes(sim,protected=[[-12.4],[],[]])
        self.assertTrue(np.array_equal(original,sim.mesh.GetLines("x")))

    def test_alignment_does_not_move_another_ports_measurement_center(self):
        from fairbeam.planar import align_automatic_probes
        sim=Simulation(1e9,2e9)
        sim.microstrip_port(1,[-30,-1,.8],[-5,1,0],"x","z",feed_shift=6,measurement_shift=18)
        sim.microstrip_port(2,[-30,-1,.8],[-5,1,0],"x","z",feed_shift=6,measurement_shift=17.6)
        original=np.array([-30,-20,-12.6,-12.4,-12,-11.8,-5,10])
        sim.mesh.SetLines("x",original)
        with self.assertRaisesRegex(ValueError,"protected mesh plane"):
            align_automatic_probes(sim)
        self.assertTrue(np.array_equal(original,sim.mesh.GetLines("x")))

    def test_magnitude_agreement_cannot_hide_phase_error(self):
        from fairbeam.planar import compare_responses
        a=dict(frequency=[1,2,3],s11_re=[.5,.3,.1],s11_im=[0,0,0])
        b=dict(a,s11_re=[-.5,-.3,-.1])
        result=compare_responses(a,b)
        self.assertEqual(result["floor_db_rmse"],0)
        self.assertFalse(result["passed"])
        self.assertTrue(compare_responses(a,a)["passed"])
        with self.assertRaisesRegex(ValueError,"same frequency grid"):
            compare_responses(a,dict(b,frequency=[1,2,4]))

    def test_study_requires_three_planar_levels_and_full_complex_agreement(self):
        from fairbeam import convergence as cv
        curve=dict(frequency=[1,2,3],s11_re=[.5,.3,.1],s11_im=[0,0,0])
        metric=dict(f_res=2,s11_db=-20,dmax_dbi=None,solver_converged=True,
                    fine_features_resolved=True,planar_response=curve)
        members=[dict(density=x,status="done",metrics=copy.deepcopy(metric)) for x in [20,30,45]]
        self.assertFalse(cv.evaluate(members[:2],[20,30,45])["converged"])
        self.assertTrue(cv.evaluate(members,[20,30,45])["converged"])
        members[2]["metrics"]["planar_response"]["s11_re"]=[-.5,-.3,-.1]
        self.assertFalse(cv.evaluate(members,[20,30,45])["converged"])

if __name__=="__main__":unittest.main()
