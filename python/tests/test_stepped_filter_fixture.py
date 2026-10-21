"""Opt-in stepped-filter acquisition; ordinary discovery starts no propagation.

From python/ with openEMS installed:
python -m unittest tests.test_stepped_filter_fixture -q
python -m tests.test_stepped_filter_fixture --preflight --out C:/study/step-plan-fresh
python -m tests.test_stepped_filter_fixture --fdtd --mesh 1 --kind through --out C:/study/step-through-fresh
python -m tests.test_stepped_filter_fixture --study --out C:/study/step-study
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import numpy as np
from fairbeam.procutil import popen_group, release_group, terminate_group
from fairbeam.network import network_s
from tests.test_coax_resonator import wait_owned
from tests import stepped_filter_fixture as f


def configurations(include_diagnostics=False):
    cases = [(f'n{n}_{kind}_{loss}', n, kind, loss, 'ordinary')
        for n in f.MESHES for kind in f.KINDS for loss in f.LOSSES]+[
        (f'n2_filter_{loss}_{control}', 2, 'filter', loss, control)
        for loss in f.LOSSES for control in f.STUDY_CONTROLS]
    if include_diagnostics:
        cases += [(f'n1_through_pec_{control}', 1, 'through', 'pec', control) for control in f.DIAGNOSTIC_CONTROLS]
    return cases


def forecast(meta, rate):
    if not np.isfinite(rate) or rate <= 0: raise ValueError('positive conservative rate required')
    return 1.2*meta['native_cells']*meta['max_timesteps']/(rate*1e6)+45.


def freeze(out, n, kind, loss, control, cap_s=None):
    planned = []
    for port in (1, 2):
        holder = f.build(n, port, kind, loss, control, cap_s); sim, meta = holder[0], holder[2]
        path = out/f'planned-p{port}.xml'; sim.fdtd.Write2XML(str(path))
        sim.fdtd.SetTimeStep(0.); sim.fdtd.SetNumberOfTimeSteps(1024)
        operator = out/f'planned-p{port}-operator.xml'; sim.fdtd.Write2XML(str(operator))
        planned.append(dict(**meta, input_sha256=f.sha(path), operator_input_sha256=f.sha(operator)))
    plan = dict(source=f.identity(), runtime=f.runtime_identity(), ports=planned)
    f.save(out/'plan.json', plan)
    return plan


def preflight(out, rate):
    out = Path(out); out.mkdir(parents=True, exist_ok=False); rows = []
    for name, n, kind, loss, control in configurations(include_diagnostics=True):
        folder = out/name; folder.mkdir()
        plan = freeze(folder, n, kind, loss, control)
        for meta in plan['ports']:
            seconds = forecast(meta, rate)
            rows.append(dict(name=name, **meta, forecast_seconds=seconds, deferred=seconds > 1800.))
    result = dict(rows=rows, source=f.identity(), runtime=f.runtime_identity(),
        rate_mcps=rate, limits=f.LIMITS, loss_estimates=f.loss_estimates(), native_propagations=0, qualified=False)
    f.save(out/'preflight.json', result)
    return result


def acquire(out, n=1, kind='filter', loss='pec', control='ordinary', rate=15., pilot_s=None, allow_cap=False):
    out = Path(out).resolve()
    if out.exists(): raise ValueError('fresh output directory required')
    if pilot_s is not None and (not np.isfinite(pilot_s) or not 0 < pilot_s <= .5e-9):
        raise ValueError('performance pilot must be in 0..0.5 ns')
    holder = f.build(n, 1, kind, loss, control, pilot_s); meta = holder[2]
    estimate = forecast(meta, rate)
    if estimate > 1800.: raise ValueError('forecast exceeds 30 minutes; defer this cohort')
    out.mkdir(parents=True, exist_ok=False); plan = freeze(out, n, kind, loss, control, pilot_s)
    state = dict(started_utc=datetime.now(timezone.utc).isoformat(), status='prepared',
        source=plan['source'], runtime=plan['runtime'], active_launcher_pid=None, qualified=False,
        pilot=pilot_s is not None, capped_diagnostic_requested=allow_cap,
        driver_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=f.ROOT, text=True).strip(),
        forecast_seconds_per_source=estimate, rate_mcps=rate)
    progress = out/'progress.json'; f.save(progress, state)
    try:
        for port in ((1,) if pilot_s is not None else (1, 2)):
            if f.identity() != state['source'] or f.runtime_identity() != state['runtime']:
                raise ValueError('frozen source/runtime changed')
            command = [sys.executable, '-m', 'tests.test_stepped_filter_fixture', '--worker',
                '--mesh', str(n), '--kind', kind, '--loss', loss, '--control', control,
                '--port', str(port), '--out', str(out/f'port{port}')]
            if pilot_s is not None: command.extend(('--pilot-seconds', str(pilot_s)))
            start, wall = time.monotonic(), time.time(); code = None; failure = None
            with (out/f'port{port}.log').open('x', encoding='utf-8') as log:
                proc = popen_group(command, cwd=f.ROOT/'python', stdout=log, stderr=subprocess.STDOUT)
                state.update(status='running', active_port=port, active_launcher_pid=proc.pid); f.save(progress, state)
                try: code = wait_owned(proc, seconds=1800.)
                except BaseException as error: failure = repr(error); raise
                finally:
                    if proc.poll() is None:
                        terminate_group(proc.pid, grace=0, job=proc.win_job); proc.wait(timeout=15)
                    release_group(proc)
                    f.save(out/f'port{port}.worker.json', dict(command=command, pid=proc.pid, exit_code=code,
                        failure=failure, wall_seconds=time.time()-wall, monotonic_seconds=time.monotonic()-start,
                        deadline_seconds=1800., forecast_seconds=estimate, driver_head=state['driver_head']))
            if code: raise RuntimeError('native worker failed; partial records retained')
            folder = out/f'port{port}'; report = json.loads((folder/'report.json').read_text())
            report.update(native_header=f.audit_header((out/f'port{port}.log').read_text(), report),
                native_log_sha256=f.sha(out/f'port{port}.log')); f.save(folder/'report.json', report)
            checks = f.quality(report)
            if pilot_s is not None:
                required = ('grid', 'clock', 'time', 'operator', 'geometry', 'probes', 'header', 'engine')
                if (not all(checks[k] for k in required) or report['native']['iteration_wall_s'] <= 0
                        or report['native']['timesteps'] != report['max_timesteps']):
                    raise ValueError('incomplete/invalid timing pilot')
                native = report['native']
                state.update(status='performance_only', rate_measured_mcps=native['cells']*native['timesteps']/native['iteration_wall_s']/1e6)
                return state
            f.read(folder, allow_cap)
        _, result = f.matrix(out, allow_cap)
        state.update(status='complete' if result['native_complete'] else 'complete_with_known_deviation', result=result)
        return result
    except BaseException as error:
        state.update(status='rejected', reason=repr(error)); raise
    finally:
        state.update(active_port=None, active_launcher_pid=None); f.save(progress, state)


def align_plane(actual, loss):
    """Move the independently known 9 mm feed planes inward to 6 mm."""
    from fairbeam.network import shift_reference_planes
    ee = f.microstrip_eps_eff(f.ER, f.H, f.dimensions()['feed_width_mm'])
    gamma = 2j*np.pi*f.FREQUENCIES*np.sqrt(ee)/f.C0
    if loss == 'dielectric':
        gamma += 2*np.pi*f.FREQUENCIES/f.C0*f.ER*(ee-1)/(2*np.sqrt(ee)*(f.ER-1))*f.TAN_D*f.FC/f.FREQUENCIES
    return shift_reference_planes(actual, gamma, [.003, .003])


def study(root):
    root = Path(root); missing = []; rejected = []; results = {}; rows = []
    for name, n, kind, loss, control in configurations():
        folder = root/name
        if not all((folder/f'port{p}/report.json').exists() for p in (1, 2)):
            missing.append(name); continue
        try: actual, row = f.matrix(folder)
        except (ValueError, AssertionError, FileNotFoundError) as error:
            rejected.append(dict(name=name, reason=repr(error))); continue
        results[name] = actual; rows.append(row)
    mesh = []; controls = []
    for kind in f.KINDS:
        for loss in f.LOSSES:
            for a, b in zip(f.MESHES[:-1], f.MESHES[1:]):
                first, second = f'n{a}_{kind}_{loss}', f'n{b}_{kind}_{loss}'
                if first in results and second in results:
                    change = f.difference(results[second], results[first])
                    mesh.append(dict(kind=kind, loss=loss, from_mesh=a, to_mesh=b, **change,
                        passes=change['s_abs'] <= f.LIMITS['mesh_s_abs'] and change['phase_deg'] <= f.LIMITS['mesh_phase_deg']))
    for loss in f.LOSSES:
        base = f'n2_filter_{loss}'
        for control in f.STUDY_CONTROLS:
            other = base+'_'+control
            if base not in results or other not in results: continue
            # Feed-plane changes have an independently specified analytical
            # phase shift; remove only that propagation, never fit a launch.
            actual = results[other]
            if control == 'plane':
                actual = align_plane(actual, loss)
            change = f.difference(actual, results[base])
            controls.append(dict(loss=loss, control=control, **change,
                passes=change['s_abs'] <= f.LIMITS['control_s_abs'] and change['phase_deg'] <= f.LIMITS['control_phase_deg']))
    qualified = (not missing and not rejected and len(mesh) == 8 and len(controls) == 8
        and all(r['passes'] for r in mesh+controls)
        and all(r['native_complete'] and r['measurement_passes'] and r['point_target_passes'] for r in rows))
    return dict(qualified=qualified, missing=missing, rejected=rejected, rows=rows, mesh_changes=mesh,
        controls=controls, native_copper_qualified=False,
        scope='PEC and constant-conductivity dielectric only; no copper or fabricated-filter certificate')


class SteppedFilterTests(unittest.TestCase):
    def test_six_section_nodal_reference_matches_independent_abcd(self):
        d = f.dimensions(); self.assertEqual(len(d['sections']), 6)
        self.assertEqual([s['kind'] for s in d['sections']], list('CLCLCL'))
        feed = dict(z=f.Z0, width=d['feed_width_mm'], length=6.,
            eps_eff=f.microstrip_eps_eff(f.ER, f.H, d['feed_width_mm']))
        expected = f.cascade_lines_s(f.FREQUENCIES, [feed, *d['sections'], feed], z0=f.Z0)
        np.testing.assert_allclose(f.reference(), expected, rtol=2e-12, atol=2e-12)

    def test_lumped_prototype_matches_independent_butterworth_magnitude(self):
        g = 2*np.sin((2*np.arange(1, 7)-1)*np.pi/12)
        nodes = 0; admittances = []; impedances = []
        for k, value in enumerate(g):
            if k % 2 == 0: admittances.append((nodes, -1, 2j*np.pi*f.FREQUENCIES*value/(2*np.pi*f.FC*f.Z0)))
            else:
                impedances.append((nodes, nodes+1, 2j*np.pi*f.FREQUENCIES*value*f.Z0/(2*np.pi*f.FC))); nodes += 1
        s = network_s(f.FREQUENCIES, [0, nodes], z_ref=f.Z0, admittances=admittances, impedances=impedances)
        np.testing.assert_allclose(abs(s[:, 1, 0]), 1/np.sqrt(1+(f.FREQUENCIES/f.FC)**12), atol=3e-14)

    def test_no_tuning_of_short_line_lengths_to_ideal_response(self):
        d = f.dimensions()
        for k, s in enumerate(d['sections']):
            g = 2*np.sin((2*(k+1)-1)*np.pi/12)
            beta_l = g*(f.Z_LOW/f.Z0 if k % 2 == 0 else f.Z0/f.Z_HIGH)
            self.assertAlmostEqual(2*np.pi*f.FC*np.sqrt(s['eps_eff'])/f.C0*s['length']*1e-3, beta_l)
        k = int(np.argmin(abs(f.FREQUENCIES-4e9)))
        exact = -20*np.log10(abs(f.reference()[k, 1, 0]))
        lumped = 10*np.log10(1+(4e9/f.FC)**12)
        self.assertGreater(abs(exact-lumped), 1.)

    def test_lossless_reference_is_reciprocal_and_unitary(self):
        s = f.reference()
        np.testing.assert_allclose(s, s.transpose(0, 2, 1), atol=1e-13)
        np.testing.assert_allclose(s.conj().transpose(0, 2, 1)@s,
            np.broadcast_to(np.eye(2), s.shape), atol=1e-13)

    def test_reference_plane_control_uses_known_inward_shift(self):
        for loss in f.LOSSES:
            np.testing.assert_allclose(align_plane(f.reference(plane_mm=9., loss=loss), loss),
                f.reference(plane_mm=6., loss=loss), atol=3e-13)

    def test_dielectric_reference_is_passive_and_distinguishes_loss_laws(self):
        s = f.reference(loss='dielectric')
        self.assertLess((np.linalg.svd(s, compute_uv=False)**2).max(), 1.)
        values = f.loss_estimates(np.array([f.FC/2, f.FC, 2*f.FC]))
        np.testing.assert_allclose(values['native_tan_d'], [.04, .02, .01])
        self.assertFalse(values['copper_qualified']); self.assertEqual(values['native_copper_runs'], 0)

    def test_fixed_physical_interfaces_and_bounded_neighbour_growth(self):
        for control in f.CONTROLS:
            interfaces = []
            for n in f.MESHES:
                x, y, z = f.mesh_lines(n, control)
                interfaces.append([x[8], x[-9], y[8], y[-9], z[0], z[-9]])
                for a in (x, y, z):
                    h = np.diff(a); self.assertTrue(np.all(h > 0))
                    self.assertLessEqual(max(np.max(h[1:]/h[:-1]), np.max(h[:-1]/h[1:])), 2+1e-10)
                for a in (x, y):
                    np.testing.assert_allclose(np.diff(a[:9]), f.H/n, atol=1e-12)
                np.testing.assert_allclose(np.diff(z[-9:]), f.H/n, atol=1e-12)
            np.testing.assert_allclose(interfaces, np.broadcast_to(interfaces[0], (3, 6)), atol=1e-12)

    def test_all_strip_edges_retain_requested_thirds(self):
        widths = [f.dimensions()['feed_width_mm'], *[s['width'] for s in f.dimensions()['sections']]]
        for n in f.MESHES:
            for control in ('ordinary', 'boundary'):
                y = f.mesh_lines(n, control)[1]
                for width in widths:
                    for sign in (-1, 1):
                        edge = sign*width/2; j = np.searchsorted(y, edge)
                        expected = 1/3 if sign == 1 else 2/3
                        self.assertAlmostEqual((edge-y[j-1])/(y[j]-y[j-1]), expected, places=11)

    def test_launch_pair_changes_only_the_active_source_position(self):
        for port in (1, 2):
            canonical = []
            for control, distance in (('launch-box', 12.), ('launch', 24.)):
                holder = f.build(1, port, kind='through', control=control)
                sim, meta = holder[0], holder[2]
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)/'input.xml'; sim.fdtd.Write2XML(str(path)); tree = ET.parse(path)
                    boxes = tree.findall('.//Properties/Excitation/Primitives/Box')
                    self.assertEqual(len(boxes), 1)
                    expected = -distance if port == 1 else f.dimensions()['junctions_mm'][-1]+distance
                    for corner in ('P1', 'P2'):
                        node = boxes[0].find(corner)
                        # CSXCAD serializes primitive coordinates to six digits.
                        np.testing.assert_allclose(float(node.attrib['X']), expected, rtol=1e-6, atol=1e-12)
                        node.set('X', 'SOURCE-POSITION')
                    canonical.append(ET.tostring(tree.getroot()))
                self.assertEqual(meta['source_distance_mm'], distance)
            self.assertEqual(canonical[0], canonical[1])
        self.assertEqual(len(configurations()), 20)
        self.assertEqual(len(configurations(include_diagnostics=True)), 23)

    def test_longitudinal_shoulders_use_the_exposed_union_boundary(self):
        d = f.dimensions()
        widths = [d['feed_width_mm'], *[s['width'] for s in d['sections']], d['feed_width_mm']]
        for n in f.MESHES:
            aligned = f.mesh_lines(n, 'launch')
            corrected = f.mesh_lines(n, 'launch-edges')
            np.testing.assert_array_equal(aligned[1], corrected[1])
            np.testing.assert_array_equal(aligned[2], corrected[2])
            np.testing.assert_array_equal(aligned[0][[8, -9]], corrected[0][[8, -9]])
            x = corrected[0]
            for edge, left, right in zip(d['junctions_mm'], widths[:-1], widths[1:]):
                self.assertFalse(np.isclose(x, edge, rtol=0, atol=1e-12).any())
                j = np.searchsorted(x, edge)
                fraction = (edge-x[j-1])/(x[j]-x[j-1])
                self.assertAlmostEqual(fraction, 2/3 if right > left else 1/3, places=11)

    def test_edge_control_preserves_physical_geometry_source_and_clock(self):
        for n in f.MESHES:
            for port in (1, 2):
                canonical = []; metadata = []
                for control in ('launch', 'launch-edges'):
                    holder = f.build(n, port, control=control); metadata.append(holder[2])
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp)/'input.xml'; holder[0].fdtd.Write2XML(str(path))
                        root = ET.parse(path).getroot()
                        grid = root.find('.//RectilinearGrid/XLines')
                        self.assertIsNotNone(grid)
                        grid.clear(); grid.text = 'LONGITUDINAL-MESH'
                        canonical.append(ET.tostring(root))
                self.assertEqual(canonical[0], canonical[1])
                np.testing.assert_allclose(metadata[0]['declared_dt_s'],
                    metadata[1]['declared_dt_s'], rtol=1e-14, atol=0)
                self.assertEqual(metadata[0]['source_distance_mm'], 24.)
                self.assertEqual(metadata[1]['source_distance_mm'], 24.)

    def test_geometry_and_loss_are_explicit_and_bundles_refused(self):
        for kind in f.KINDS:
            for loss in f.LOSSES:
                holder = f.build(1, 2, kind, loss); sim, meta = holder[0], holder[2]
                self.assertFalse(meta['native_copper']); self.assertEqual(meta['threads'], 4)
                self.assertEqual(len(meta['solids']), 3 if kind == 'through' else 8)
                self.assertEqual(len(meta['expected_current_indices']), 8)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)/'input.xml'; sim.fdtd.Write2XML(str(path))
                    tree = ET.parse(path); material = tree.find('.//Material[@Name="substrate"]')
                    self.assertIsNotNone(material); self.assertEqual(material.attrib['Isotropy'], '1')
                    # CSXCAD stores an isotropic scalar in component zero.
                    kappa = np.fromstring(material.find('Property').attrib['Kappa'], sep=',')[0]
                    np.testing.assert_allclose(kappa,
                        f.TAN_D*2*np.pi*f.FC*f.EPS0*f.ER if loss == 'dielectric' else 0., rtol=1e-6)
                with self.assertRaises(ValueError): sim.to_bundle()

    def test_invalid_models_refused(self):
        for n in (True, 0, 4, 2.):
            with self.assertRaises(ValueError): f.build(n, 1)
        for kwargs in (dict(kind='bad'), dict(loss='copper'), dict(control='bad'), dict(cap_s=0.)):
            with self.assertRaises(ValueError): f.build(1, 1, **kwargs)

    def test_full_wave_matrix_with_cross_incident_waves(self):
        target = f.reference(); a = np.broadcast_to(np.array([[1., .2j], [.1, .9]]), target.shape)
        b = target@a
        v = (np.sqrt(f.Z0)*(a+b)).transpose(2, 1, 0)
        i = ((a-b)/np.sqrt(f.Z0)).transpose(2, 1, 0)
        np.testing.assert_allclose(f.scattering(v, i), target, atol=3e-14)
        with self.assertRaises(ValueError): f.scattering(v[[0, 0]], i[[0, 0]])

    def test_two_contours_and_spatial_stagger_are_independent_checks(self):
        target = f.reference(); a = np.broadcast_to(np.eye(2), target.shape); b = target@a
        v = (np.sqrt(f.Z0)*(a+b)).transpose(2, 1, 0)
        i = ((a-b)/np.sqrt(f.Z0)).transpose(2, 1, 0); phase = .04
        vv = np.stack((v*np.exp(1j*phase), v, v*np.exp(-1j*phase)), axis=2)
        ii = np.broadcast_to(i[:, :, None, None, :]*np.cos(phase/2), (2, 2, 2, 2, len(f.FREQUENCIES))).copy()
        actual, qa = f.extract(vv, ii); np.testing.assert_allclose(actual, target, atol=3e-14)
        self.assertLess(qa['contour_s_abs'], 1e-13)
        ii[:, :, 1] *= 1.03
        self.assertGreater(f.extract(vv, ii)[1]['contour_s_abs'], f.LIMITS['contour_s_abs'])

    def test_cap_admission_is_explicit_and_never_repairs_other_failures(self):
        meta = dict(native=dict(timesteps=10, numerical_time_s=1.), max_timesteps=10, cap_s=1.)
        q = dict(source=True, clock=True, tail=True, stopping=False)
        self.assertFalse(f.admissible(meta, q)); self.assertTrue(f.admissible(meta, q, True))
        for key in ('source', 'clock', 'tail'):
            self.assertFalse(f.admissible(meta, dict(q, **{key: False}), True))
        bad = deepcopy(meta); bad['native']['timesteps'] = 9
        self.assertFalse(f.admissible(bad, q, True)); self.assertFalse(q['stopping'])

    def test_perfect_capped_matrix_still_cannot_be_qualified(self):
        target = f.reference(); a = np.broadcast_to(np.eye(2), target.shape)
        v = (np.sqrt(f.Z0)*(a+target)).transpose(2, 1, 0)
        current = ((a-target)/np.sqrt(f.Z0)).transpose(2, 1, 0); phase = .04
        vv = np.stack((v*np.exp(1j*phase), v, v*np.exp(-1j*phase)), axis=2)
        ii = np.broadcast_to(current[:, :, None, None, :]*np.cos(phase/2), (2, 2, 2, 2, len(f.FREQUENCIES)))
        def source(path, allow_cap):
            self.assertTrue(allow_cap); port = int(path.name[-1])
            meta = dict(n=1, kind='filter', loss='pec', control='ordinary', cap_s=8e-9,
                mesh_mm={}, plane_mm=6., excited_port=port)
            return meta, dict(v=vv[port-1], i=ii[port-1]), dict(source=True, stopping=False)
        with tempfile.TemporaryDirectory() as tmp, patch.object(f, 'read', side_effect=source):
            _, result = f.matrix(tmp, True)
            self.assertTrue(result['point_target_passes']); self.assertTrue(result['measurement_passes'])
            self.assertFalse(result['native_complete']); self.assertFalse(result['qualified'])
            self.assertFalse((Path(tmp)/'matrix.npz').exists())
            path = Path(tmp)/'cap-diagnostic-comparison.json'
            changed = json.loads(path.read_text()); changed['qualified'] = True; f.save(path, changed)
            with self.assertRaises(ValueError): f.matrix(tmp, True)

    def test_electric_and_half_step_magnetic_clocks(self):
        dt = 1e-13; stride = int(1/(2*f.FREQUENCIES[-1]*dt))//f.OVERSAMPLING
        meta = dict(native=dict(dt_s=dt, numerical_time_s=3*stride*dt))
        e = np.column_stack((np.arange(4)*stride*dt, np.ones(4)))
        h = e+np.array([dt/2, 0.]); f.trace_clock(e, meta); f.trace_clock(h, meta, True)
        with self.assertRaises(ValueError): f.trace_clock(h, meta)
        with self.assertRaises(ValueError): f.trace_clock(e[:-1], meta)

    def test_through_samples_do_not_use_the_filter_prototype(self):
        target = f.reference(kind='through'); a = np.broadcast_to(np.eye(2), target.shape)
        v = (np.sqrt(f.Z0)*(a+target)).transpose(2, 1, 0)
        i = ((a-target)/np.sqrt(f.Z0)).transpose(2, 1, 0); phase = .04
        vv = np.stack((v*np.exp(1j*phase), v, v*np.exp(-1j*phase)), axis=2)
        ii = np.broadcast_to(i[:, :, None, None, :]*np.cos(phase/2), (2, 2, 2, 2, len(f.FREQUENCIES)))
        def source(path, allow_cap):
            port = int(path.name[-1])
            meta = dict(n=1, kind='through', loss='pec', control='ordinary', cap_s=8e-9,
                mesh_mm={}, plane_mm=6., excited_port=port)
            return meta, dict(v=vv[port-1], i=ii[port-1]), dict(source=True, stopping=False)
        with tempfile.TemporaryDirectory() as tmp, patch.object(f, 'read', side_effect=source):
            _, result = f.matrix(tmp, True)
            self.assertTrue(all(row['prototype_loss_db'] == 0. for row in result['samples']))

    def test_long_forecast_refused_before_launch_or_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch(__name__+'.popen_group') as launch:
            out = Path(tmp)/'absent'
            with self.assertRaises(ValueError): acquire(out, rate=.01)
            self.assertFalse(out.exists()); launch.assert_not_called()
        for rate in (0., -1., np.nan):
            with self.assertRaises(ValueError): forecast(dict(native_cells=1, max_timesteps=1), rate)

    def test_pilot_is_bounded_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp, patch(__name__+'.popen_group') as launch:
            for cap in (0., 1e-9, np.nan):
                with self.assertRaises(ValueError): acquire(Path(tmp)/'absent', pilot_s=cap)
            launch.assert_not_called()

    def test_failed_worker_leaves_receipt_and_no_active_pid(self):
        proc = Mock(pid=123, win_job=None); proc.poll.return_value = 2
        with tempfile.TemporaryDirectory() as tmp, patch(__name__+'.popen_group', return_value=proc), \
                patch(__name__+'.wait_owned', return_value=2), patch(__name__+'.release_group'):
            out = Path(tmp)/'failed'
            with self.assertRaises(RuntimeError): acquire(out, rate=1000.)
            self.assertIsNone(json.loads((out/'progress.json').read_text())['active_launcher_pid'])
            self.assertEqual(json.loads((out/'port1.worker.json').read_text())['exit_code'], 2)

    def test_missing_mesh_and_controls_cannot_qualify(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = study(tmp); self.assertFalse(result['qualified'])
            self.assertEqual(len(result['missing']), 20)


def main():
    p = argparse.ArgumentParser(description=__doc__); mode = p.add_mutually_exclusive_group(required=True)
    for name in ('preflight', 'fdtd', 'compare', 'study', 'worker'): mode.add_argument('--'+name, action='store_true')
    p.add_argument('--out', type=Path, required=True); p.add_argument('--mesh', type=int, choices=f.MESHES, default=1)
    p.add_argument('--kind', choices=f.KINDS, default='filter'); p.add_argument('--loss', choices=f.LOSSES, default='pec')
    p.add_argument('--control', choices=f.CONTROLS, default='ordinary'); p.add_argument('--port', type=int, choices=(1, 2), default=1)
    p.add_argument('--rate-mcps', type=float, default=15.); p.add_argument('--pilot-seconds', type=float)
    p.add_argument('--allow-capped-diagnostic', action='store_true')
    args = p.parse_args()
    if args.allow_capped_diagnostic and not (args.fdtd or args.compare): p.error('capped diagnostics are separate from strict study')
    if args.worker:
        f.capture(args.out, args.mesh, args.port, args.kind, args.loss, args.control, args.pilot_seconds); return
    if args.preflight: result = preflight(args.out, args.rate_mcps)
    elif args.fdtd: result = acquire(args.out, args.mesh, args.kind, args.loss, args.control, args.rate_mcps, args.pilot_seconds, args.allow_capped_diagnostic)
    elif args.compare: result = f.matrix(args.out, args.allow_capped_diagnostic)[1]
    else: result = study(args.out)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    if any('--'+s in sys.argv for s in ('preflight', 'fdtd', 'compare', 'study', 'worker')): main()
    else: unittest.main()
