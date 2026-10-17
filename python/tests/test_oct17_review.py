"""Independent numerical and native-geometry regressions for the October review."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from fairbeam import Simulation
from fairbeam.automesh import _dispersive_index_bound, extract
from fairbeam.design import IDENTITY, _rotate, _rotate_any, _scale, flip, map_primitive, blank_design, to_python
from fairbeam.dispersion import Dispersion, Lorentz
from fairbeam.multiport import qa_metrics, element_pattern_section, decode_pattern_fields, ENC_F32
from fairbeam.optimize import Driver, Vary, Stop, parse_goal, goal_cost, bundle_metrics, run_optimization, optimize


class ReviewNumerics(unittest.TestCase):
    def test_composed_right_angle_preserves_linpoly_geometry(self):
        generated = {}
        d = blank_design('rotation', 'Rotation')
        d['parts'][2]['primitives'] = [{'kind': 'box', 'start': [0,0,0], 'stop': [1,2,3]}]
        d['parts'][2]['transforms'] = [{'type': 'rotate', 'axis': 'x', 'angle': 30, 'center': [0, 0, 0]}]
        exec(to_python(d), generated)
        for axis in range(3):
            exact = _rotate(*IDENTITY, axis, [0, 0, 0], 1)
            composed = _rotate_any(*_rotate_any(*IDENTITY, axis, [0, 0, 0], 30), axis, [0, 0, 0], 60)
            for modifier in (lambda m: m, lambda m: flip(*_scale(*m, 2, [1,2,3]), axis, 4),
                             lambda m: _rotate_any(*m, axis, [0,0,0], -90)):
                exact_map, composed_map = modifier(exact), modifier(composed)
                for normal in range(3):
                    primitive = {'kind': 'linpoly', 'normal': normal, 'elevation': 2, 'length': 3,
                                 'points': [[0, 0], [4, 0], [4, 5], [0, 5]]}
                    expected, actual = map_primitive(primitive, *exact_map), map_primitive(primitive, *composed_map)
                    self.assertEqual(actual['normal'], expected['normal'])
                    for key in ('points', 'elevation', 'length'):
                        np.testing.assert_allclose(actual[key], expected[key], atol=1e-12)
                    self.assertEqual(generated['_mapped_axis'](composed_map[0], normal)[0], expected['normal'])
                    # Native CSXCAD must receive the same extrusion axis and nonzero volume.
                    bounds = []
                    for p in (expected, actual):
                        sim = Simulation(1e9, 2e9)
                        poly = sim.metal('p').AddLinPoly(priority=1, points=np.asarray(p['points']).T,
                                                       norm_dir=p['normal'], elevation=p['elevation'], length=p['length'])
                        bounds.append(poly.GetBoundBox())
                    np.testing.assert_allclose(bounds[0], bounds[1], atol=1e-12)
                    self.assertTrue(np.all(np.ptp(bounds[1], axis=0) > 0))

    def test_passivity_requires_all_coherent_excitations(self):
        qa = qa_metrics(np.full((2, 2, 2), .7, complex), [1, 2])
        self.assertEqual(qa['column_power_max'], {'1': .98, '2': .98})
        self.assertFalse(qa['passive'])
        self.assertAlmostEqual(qa['passivity_max'], 1.96)
        for matrix in (np.eye(2), np.array([[0, 1j], [1j, 0]]), np.eye(2) * .3):
            self.assertTrue(qa_metrics(matrix[None], [1, 2])['passive'])
        invalid = np.ones((1, 2, 2), complex); invalid[0, 0, 0] = np.nan
        for matrix, ports in ((invalid, [1, 2]), (np.eye(2)[None], [1])):
            qa = qa_metrics(matrix, ports)
            self.assertIsNone(qa['passive'])
            json.dumps(qa, allow_nan=False)

    def test_element_patterns_use_full_complex_incident_basis(self):
        f = np.array([1e9, 2e9]); ids = [3, 7]
        a = np.array([[1, .1j], [.1, 1]], complex)
        expected = np.array([2+1j, 4-2j]); observed = expected @ a
        sims = []
        for j, pn in enumerate(ids):
            res = SimpleNamespace(E_theta=[np.full((2, 2), observed[j])], E_phi=[np.full((2, 2), 1j*observed[j])], r=1.)
            sim = SimpleNamespace(_nf2ff={'theta': [0, 180], 'phi': [0, 180], 'freqs': [1.5e9], 'res': res},
                _port_objs=[SimpleNamespace(uf_inc=np.full(2, a[i, j]*np.sqrt(50)), Z_ref=50.) for i in range(2)],
                ports=[{'number': n, 'start': [0,0,0], 'stop': [0,0,1]} for n in ids], boundaries=['MUR']*6, nf2ff_center=[0,0,0])
            sims.append((pn, sim))
        sec = element_pattern_section(sims[0][1], sims[::-1], f, encoding=ENC_F32)
        for port in sec['ports']:
            et, ep = decode_pattern_fields(sec, port['fields'][0])
            np.testing.assert_allclose(et, expected[ids.index(port['port'])], atol=1e-6)
            np.testing.assert_allclose(ep, 1j*et, atol=1e-6)
        with self.assertRaisesRegex(ValueError, 'every port'):
            element_pattern_section(sims[0][1], sims[:1], f)
        sims[1][1].nf2ff_center = [1,0,0]
        with self.assertRaisesRegex(ValueError, 'phase center'):
            element_pattern_section(sims[0][1], sims, f)

    def test_optimizer_and_study_use_canonical_sii_without_mutating_raw_run(self):
        from fairbeam.study import _excited_s11
        bundle = {'ports': [{'number': 7, 'excite': True}], 'results': {
            'frequency': [1e9, 2e9], 'ports': {'7': {'s11_re': [.26,.26], 's11_im': [0,0],
                'zin_re': [85,85], 'zin_im': [0,0], 'z_ref': 50}},
            'sparams': {'ports': [1,2], 'port_numbers': [3,7],
                's': {'1,1': {'re': [.2,.2], 'im': [0,0]}, '2,2': {'re': [.2,.2], 'im': [0,0]}}}}}
        goals = [parse_goal('s11_max=-13@1.5'), parse_goal('match_all=-13@1.5')]
        metrics = bundle_metrics(bundle, goals)
        self.assertEqual(metrics['s11_at']['1.5'], metrics['match_all_at']['1.5']['max'])
        np.testing.assert_allclose(_excited_s11(bundle)[1], .2)
        self.assertEqual(bundle['results']['ports']['7']['s11_re'], [.26,.26])

    def test_native_optimizer_gates_energy_and_each_port_and_continues(self):
        from fairbeam.model import Param
        model = SimpleNamespace(MODEL={'id': 'quality', 'name': 'Quality'},
            PARAMS=[Param('x', .5, 'x', minimum=0, maximum=1)],
            build=lambda _: SimpleNamespace(ports=[{'number': 1}], f_min=1e9, f_max=2e9))
        for run in ({}, {'converged': False}, {'converged': True, 'port_runs': [{'converged': False}]},
                    {'converged': True, 'port_runs': [{'converged': True}]}):
            with self.subTest(run=run), tempfile.TemporaryDirectory() as directory:
                sim = SimpleNamespace(to_bundle=lambda *args, **kwargs: {'run': run})
                output = io.StringIO()
                with patch('fairbeam.model.load_model', return_value=model), \
                        patch('fairbeam.multiport.run_model', return_value=sim), \
                        patch('fairbeam.optimize.bundle_metrics', return_value={'s11_at': {'1.5': -20}}), \
                        contextlib.redirect_stdout(output):
                    doc = run_optimization('fake.py', [Vary('x',0,1)], [parse_goal('s11_max=-10@1.5')],
                        out=Path(directory)/'out', sim_root=Path(directory)/'sim', max_evals=1, keep_sim=True, log=lambda _: None)
                valid = run.get('converged') is True and all(r.get('converged') is True for r in run.get('port_runs', []))
                self.assertEqual(doc['best'] is not None, valid)
                self.assertEqual(doc['evaluations'][0]['met'], valid)
                json.dumps(doc, allow_nan=False)
                for line in output.getvalue().splitlines():
                    if line.startswith('fairbeam: optimize '):
                        json.loads(line.split(' ', 3)[3], parse_constant=lambda c: self.fail(c))

    def test_all_goal_kinds_reject_nonfinite_metrics(self):
        cases = [('f0=2', lambda v: {'f0_ghz': v}), ('bw_min=100', lambda v: {'bw_mhz': v}),
                 ('s11_max=-10@2', lambda v: {'s11_at': {'2': v}}),
                 ('dmax_min=3@2', lambda v: {'dmax_at': {'2': v}}),
                 ('sij_max=-10@2:2,1', lambda v: {'sij_at': {'2,1@2': v}}),
                 ('sij_min=-3@2:2,1', lambda v: {'sij_at': {'2,1@2': v}}),
                 ('match_all=-10@2', lambda v: {'match_all_at': {'2': {'max': v}}})]
        for spec, metrics in cases:
            for v in (np.nan, np.inf, -np.inf):
                with self.subTest(spec=spec, value=v):
                    c, met, val = goal_cost(parse_goal(spec), metrics(v))
                    self.assertGreaterEqual(c, 1e4); self.assertFalse(met); self.assertIsNone(val)
        d = Driver([Vary('x', 0, 1)], [parse_goal('s11_max=-10@2')],
                   lambda p: {'metrics': {'s11_at': {'2': np.nan if p['x'] == 0 else -20}}})
        d([0]); self.assertIsNone(d.best)
        with self.assertRaises(Stop): d([1])
        self.assertEqual(d.best['params'], {'x': 1})
        json.dumps(d.history, allow_nan=False)
        out = optimize([Vary('x', 0, 1)], [parse_goal('s11_max=-10@2')],
                       lambda p: {'metrics': {'s11_at': {'2': np.nan}}}, method='nelder-mead', max_evals=5)
        self.assertIsNone(out['best']); self.assertNotEqual(out['reason'], 'goals met')


class NativeDispersiveMesh(unittest.TestCase):
    def test_zero_strength_lorentz_matches_static_epsilon_16_mesh(self):
        meshes = []
        for dispersive in (False, True):
            sim = Simulation(1e9, 3e9, boundaries=['MUR']*6)
            mat = (Dispersion(eps_inf=16, eps_poles=(Lorentz(0, 2e9, 1e-9),)).add_to(sim.csx, 'sub')
                   if dispersive else sim.dielectric('sub', 16))
            mat.AddBox(priority=0, start=[-10,-10,0], stop=[10,10,1])
            _, dielectrics, other = extract(sim.csx, (sim.f_min, sim.f_max))
            self.assertEqual(len(dielectrics), 1); self.assertFalse(other)
            self.assertEqual(dielectrics[0].eps_r, 16)
            sim.auto_mesh()
            meshes.append([sim.mesh.GetLines(a) for a in 'xyz'])
        for a, b in zip(*meshes): np.testing.assert_allclose(a, b, atol=1e-12)

    def test_pole_bound_covers_peak_between_samples(self):
        sim = Simulation(1e9, 3e9)
        model = Dispersion(eps_inf=2, eps_poles=(Lorentz(1e9, 2.012345e9, 1e-7),))
        mat = model.add_to(sim.csx, 'pole')
        bound = _dispersive_index_bound(mat, (1e9, 3e9))
        f = np.r_[np.linspace(1e9, 3e9, 10001), 2.012345e9]
        w = 2*np.pi*f; wl = 2*np.pi*2.012345e9
        eps = 2*(1-(2*np.pi*1e9)**2/(w*w-wl*wl-1j*w/1e-7))
        self.assertGreaterEqual(bound, np.max(np.abs(eps)))
        self.assertGreater(bound, 100)


if __name__ == '__main__':
    unittest.main()
