"""Opt-in own air twin-wire bandstop comparison associated with Example 8.8.

From python/ in the bundled runtime:
python -m unittest tests.test_open_stub_bandstop_fixture -q
python -m tests.test_open_stub_bandstop_fixture --preflight --out C:/study/bandstop-plan
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
from tests import open_stub_bandstop_fixture as f

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
        rate_mcps=rate, limits=f.LIMITS, dimensions=f.dimensions(), native_propagations=0, qualified=False)
    f.save(out/'preflight.json', result)
    return result


def acquire(out, n=1, kind='filter', loss='pec', control='ordinary', rate=15., pilot_s=None, allow_cap=False, window_s=None):
    out = Path(out).resolve()
    if out.exists(): raise ValueError('fresh output directory required')
    if pilot_s is not None and (not np.isfinite(pilot_s) or not 0 < pilot_s <= .5e-9):
        raise ValueError('performance pilot must be in 0..0.5 ns')
    if window_s is not None and (pilot_s is not None or not allow_cap
            or not np.isfinite(window_s) or not f.excitation.dgauss_duration_s(f.FREQUENCIES[-1]) <= window_s <= f.CAP_S):
        raise ValueError('explicit capped diagnostic window must contain the source and be at most 32 ns')
    cap = pilot_s if pilot_s is not None else window_s
    holder = f.build(n, 1, kind, loss, control, cap); meta = holder[2]
    estimate = forecast(meta, rate)
    if estimate > 1800.: raise ValueError('forecast exceeds 30 minutes; defer this cohort')
    out.mkdir(parents=True, exist_ok=False); plan = freeze(out, n, kind, loss, control, cap)
    state = dict(started_utc=datetime.now(timezone.utc).isoformat(), status='prepared',
        source=plan['source'], runtime=plan['runtime'], active_launcher_pid=None, qualified=False,
        pilot=pilot_s is not None, diagnostic_window_s=window_s, capped_diagnostic_requested=allow_cap,
        driver_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=f.ROOT, text=True).strip(),
        forecast_seconds_per_source=estimate, rate_mcps=rate)
    progress = out/'progress.json'; f.save(progress, state)
    try:
        for port in ((1,) if pilot_s is not None else (1, 2)):
            if f.identity() != state['source'] or f.runtime_identity() != state['runtime']:
                raise ValueError('frozen source/runtime changed')
            command = [sys.executable, '-m', 'tests.test_open_stub_bandstop_fixture', '--worker',
                '--mesh', str(n), '--kind', kind, '--loss', loss, '--control', control,
                '--port', str(port), '--out', str(out/f'port{port}')]
            if pilot_s is not None: command.extend(('--pilot-seconds', str(pilot_s)))
            if window_s is not None: command.extend(('--window-seconds', str(window_s)))
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
    from fairbeam.network import shift_reference_planes
    gamma = 2j*np.pi*f.FREQUENCIES*np.sqrt(f.ER)/f.C0
    return shift_reference_planes(actual, gamma, [.006, .006])


def study(root):
    root = Path(root); missing = []; rejected = []; results = {}; rows = []
    for name, n, kind, loss, control in configurations():
        folder = root/name
        if not all((folder/f'port{p}/report.json').exists() for p in (1, 2)):
            missing.append(name); continue
        try:
            actual, row = f.matrix(folder)
            expected = dict(n=n, kind=kind, loss=loss, control=control)
            if any(row.get(key) != value for key, value in expected.items()):
                raise ValueError(f'cohort identity mismatch for {name}: expected {expected}')
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
    qualified = (not missing and not rejected and len(mesh) == 6 and len(controls) == 5
        and all(r['passes'] for r in mesh+controls)
        and all(r['native_complete'] and r['measurement_passes'] and r['point_target_passes'] for r in rows))
    return dict(qualified=qualified, missing=missing, rejected=rejected, rows=rows, mesh_changes=mesh,
        controls=controls, native_copper_qualified=False,
        scope='Own finite air wire pairs; radiation/open ends/T junctions differ from ideal TEM circuit; no dynamic-range certificate')


def abcd_reference(freq, plane_mm=12.):
    theta = np.pi*freq/(2*f.F0)
    line = np.empty((len(freq), 2, 2), complex)
    line[:, 0, 0] = line[:, 1, 1] = np.cos(theta)
    line[:, 0, 1], line[:, 1, 0] = 1j*f.Z0*np.sin(theta), 1j*np.sin(theta)/f.Z0
    total = np.broadcast_to(np.eye(2, dtype=complex), line.shape).copy()
    for k, impedance in enumerate(f.dimensions()['stub_impedances_ohm']):
        shunt = np.broadcast_to(np.eye(2, dtype=complex), line.shape).copy()
        shunt[:, 1, 0] = 1j*np.tan(theta)/impedance
        total = total@shunt
        if k < 2: total = total@line
    a,b,c,d = (total[:, j, k] for j,k in ((0,0),(0,1),(1,0),(1,1)))
    den = a+b/f.Z0+c*f.Z0+d; result = np.empty_like(total)
    result[:,0,0],result[:,1,1] = (a+b/f.Z0-c*f.Z0-d)/den,(-a+b/f.Z0-c*f.Z0+d)/den
    result[:,1,0],result[:,0,1] = 2/den,2*(a*d-b*c)/den
    return result*np.exp(-4j*np.pi*freq*plane_mm*1e-3/f.C0)[:,None,None]



class CohortIdentityTests(unittest.TestCase):
    """Study admission must bind replayed data to the requested cohort."""

    def test_replayed_identity_and_duplicate_mesh_admission(self):
        from unittest.mock import patch
        from contextlib import ExitStack
        fields = ['n', 'kind', 'loss', 'control']
        chosen = [c for c in configurations() if c[2] == 'filter'
                  and c[fields.index('control') + 1] == 'ordinary'
                  and ('loss' not in fields or c[fields.index('loss') + 1] == 'pec')][:2]
        self.assertEqual(len(chosen), 2)
        self.assertNotEqual(chosen[0][1], chosen[1][1])
        mutations = [('valid', None), ('duplicate mesh', 'n')]
        mutations += [('wrong ' + key, key) for key in fields if key != 'n']
        mutations += [('missing ' + key, key) for key in fields]
        for label, key in mutations:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                metadata = {}
                for config in chosen:
                    folder = root / config[0]
                    folder.mkdir()
                    metadata[config[0]] = dict(zip(fields, config[1:]))
                    for port in (1, 2):
                        (folder / f'port{port}').mkdir()
                        (folder / f'port{port}' / 'report.json').write_text('{}')
                second = metadata[chosen[1][0]]
                if label == 'duplicate mesh':
                    second['n'] = chosen[0][1]
                elif label.startswith('missing '):
                    del second[key]
                elif key is not None:
                    second[key] = 'wrong-cohort'
                spectrum = np.zeros((len(f.FREQUENCIES), 2, 2), complex)
                spectrum[:, 0, 1] = spectrum[:, 1, 0] = 1

                def row_for(folder):
                    return dict(metadata[Path(folder).name], native_complete=True,
                                measurement_passes=True, point_target_passes=True,
                                point_passes=True, requested_spec_passes=True,
                                reference_spec_passes=True)

                with ExitStack() as stack:
                    stack.enter_context(patch(__name__ + '.configurations', return_value=chosen))
                    stack.enter_context(patch.object(f, 'matrix',
                        side_effect=lambda folder, *args: (spectrum, row_for(folder))))
                    report = study(root)
                changes = report.get('mesh_changes', report.get('changes',
                    report.get('by_order', {}).get('3', {}).get('mesh_changes', [])))
                if label == 'valid':
                    self.assertEqual(report['rejected'], [])
                    self.assertEqual(len(changes), 1)
                    self.assertTrue(changes[0]['passes'])
                else:
                    self.assertEqual([r['name'] for r in report['rejected']], [chosen[1][0]])
                    self.assertIn('cohort identity mismatch', report['rejected'][0]['reason'])
                    self.assertEqual(changes, [])
                    self.assertFalse(report['qualified'])

class OpenStubTests(unittest.TestCase):
    def test_native_header_integer_and_rounded_scientific_cell_counts(self):
        for n in (0,1,2):
            meta=f.build(n,1)[2]
            cells=meta['native_cells'];grid='x'.join(map(str,meta['grid']))
            text=(f'{f.MARKER}\nopenEMS 64bit -- version v0.37.0-rc3\n'
                f'fixed number of threads: 4\nFDTD simulation size: {grid} --> {cells:.6g} FDTD cells')
            self.assertEqual(f.audit_header(text,meta),dict(version='v0.37.0-rc3',threads=4))
            for bad in (text.replace(grid,grid+'0'),text.replace('threads: 4','threads: 8'),text.replace('v0.37.0-rc3','v0.36.0'),
                    text.replace(f'{cells:.6g} FDTD',f'{cells+100} FDTD'),text+f.MARKER):
                with self.assertRaises(ValueError):f.audit_header(bad,meta)

    def test_through_reference_is_the_matched_air_line(self):
        target = f.reference(kind='through')
        length = f.dimensions()['junctions_mm'][-1]*1e-3+.024
        delay = np.exp(-2j*np.pi*f.FREQUENCIES*length/f.C0)
        np.testing.assert_allclose(target[:,0,1],delay,atol=2e-14,rtol=0)
        np.testing.assert_allclose(target[:,1,0],delay,atol=2e-14,rtol=0)
        np.testing.assert_allclose(target[:,(0,1),(0,1)],0.,atol=2e-14,rtol=0)

    def test_independent_nodal_and_abcd_predictions_off_the_notch(self):
        freq = f.FREQUENCIES[abs(f.FREQUENCIES-f.F0)>1e7]
        np.testing.assert_allclose(f.reference(freq),abcd_reference(freq),atol=2e-11,rtol=0)

    def test_exact_notch_is_a_finite_scattering_limit(self):
        s = f.reference(np.array([f.F0]))
        self.assertTrue(np.isfinite(s).all())
        self.assertEqual(s[0,1,0],0)
        self.assertTrue(np.isinf(f.prototype_loss(np.array([f.F0]))[0]))
        np.testing.assert_allclose(abs(s[0].diagonal()),1.,atol=2e-15)

    def test_ideal_circuit_is_reciprocal_and_lossless(self):
        s = f.reference(); np.testing.assert_allclose(s,s.transpose(0,2,1),atol=2e-12)
        np.testing.assert_allclose(s.conj().transpose(0,2,1)@s,np.broadcast_to(np.eye(2),s.shape),atol=2e-11)

    def test_wire_dimensions_recover_all_declared_impedances(self):
        d = f.dimensions(); self.assertAlmostEqual(f.wire_impedance(d['main_radius_mm'],d['axis_spacing_mm']),50.)
        for radius,target in zip(d['stub_radii_mm'],d['stub_impedances_ohm']):
            self.assertAlmostEqual(f.wire_impedance(radius,d['axis_spacing_mm']),target,places=11)
        self.assertEqual(d['stub_radii_mm'][0],d['stub_radii_mm'][2])

    def test_physical_lengths_are_fixed_quarters_without_end_tuning(self):
        d = f.dimensions();self.assertAlmostEqual(d['stub_length_mm']*1e-3/f.C0,1/(4*f.F0))
        np.testing.assert_allclose(np.diff(d['junctions_mm']),d['stub_length_mm'],atol=1e-12)

    def test_mesh_symmetry_growth_and_fixed_physical_pml_interfaces(self):
        bounds=[]
        for n in f.MESHES:
            x,y,z=f.mesh_lines(n);np.testing.assert_allclose(z,-z[::-1],atol=2e-11,rtol=0)
            bounds.append([a[k] for a in (x,y,z) for k in (8,-9)])
            for a in (x,y,z):
                h=np.diff(a);self.assertTrue(np.all(h>0));self.assertLessEqual(max(np.max(h[1:]/h[:-1]),np.max(h[:-1]/h[1:])),2+1e-10)
        np.testing.assert_allclose(bounds,np.broadcast_to(bounds[0],(len(f.MESHES),6)),atol=2e-11,rtol=0)

    def test_invalid_inputs_are_rejected(self):
        for n in (True,-1,4,1.):
            with self.assertRaises(ValueError):f.mesh_lines(n)
            with self.assertRaises(ValueError):f.build(n,1)
        for freq in ([],[0],[4e9],[np.nan],[2e9+1j]):
            with self.assertRaises(ValueError):f.reference(np.array(freq))
        for r,d in ((0,1),(1,2),(np.nan,3)):
            with self.assertRaises(ValueError):f.wire_impedance(r,d)

    def test_single_balanced_gap_sources_and_loads_are_preserved(self):
        for source in (1,2):
            sim,ports,meta=f.build(0,source)
            self.assertEqual(len(ports),2)
            for feed in meta['feeds']:
                self.assertEqual(feed['R'],50.)
                self.assertTrue(feed['start'][2]<0<feed['stop'][2])
                self.assertTrue(np.any(np.asarray(meta['mesh_mm']['x'])==feed['start'][0]))
                self.assertEqual(feed['start'][0],feed['stop'][0])
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'input.xml';sim.fdtd.Write2XML(str(path));root=ET.parse(path).getroot()
            self.assertEqual([float(v.get('R')) for v in root.findall('.//LumpedElement')],[50.,50.])
            excitations=[v for v in root.findall('.//Excitation') if v.get('Name')]
            self.assertEqual(len(excitations),1)
            self.assertTrue(excitations[0].get('Name').endswith(str(source)))
            self.assertEqual(sum('port_excite_' in v for v in meta['required_properties']),1)
            self.assertEqual(sum('port_resist_' in v for v in meta['required_properties']),2)
            with self.assertRaises(ValueError):sim.to_bundle()

    def test_voltage_planes_are_tied_to_actual_mesh_nodes(self):
        for n in f.MESHES:
            _,_,meta=f.build(n,1)
            self.assertEqual(len(meta['expected_voltage_indices']),6)
            for name,(a,b) in meta['expected_voltage_indices'].items():
                self.assertEqual(a[:2],b[:2])
                self.assertEqual(meta['mesh_mm']['y'][a[1]],0.)
                self.assertEqual([meta['mesh_mm']['z'][q[2]] for q in (a,b)],meta['actual_wire_centres_z_mm'])
                port,plane=map(int,name[1:].split('_'))
                centre=-meta['plane_mm'] if port==0 else meta['dimensions']['junctions_mm'][-1]+meta['plane_mm']
                self.assertAlmostEqual(meta['mesh_mm']['x'][a[0]],centre+(plane-1)/(1+.25*n),places=10)

    def test_current_contours_exclude_the_return_wire(self):
        for n in f.MESHES:
            _,_,meta=f.build(n,1);z=np.asarray(meta['mesh_mm']['z']);y=np.asarray(meta['mesh_mm']['y'])
            mz,my=(z[:-1]+z[1:])/2,(y[:-1]+y[1:])/2
            gap=meta['dimensions']['main_gap_mm'];upper=meta['actual_wire_centres_z_mm'][1]
            for name,(a,b) in meta['expected_current_indices'].items():
                self.assertTrue(-gap/2-1e-11<mz[a[2]]<gap/2+1e-11)
                self.assertGreater(mz[b[2]],upper+f.MAIN_RADIUS_MM)
                self.assertLess(my[a[1]],-f.MAIN_RADIUS_MM)
                self.assertGreater(my[b[1]],f.MAIN_RADIUS_MM)
            self.assertNotEqual(meta['expected_current_indices']['i0_0_0'],meta['expected_current_indices']['i0_1_0'])

    def test_source_and_plane_controls_leave_geometry_and_clock_fixed(self):
        a=f.build(0,1)[2]
        for control in ('source','plane'):
            b=f.build(0,1,control=control)[2]
            for key in ('dimensions','mesh_mm','declared_dt_s','cap_s','actual_wire_centres_z_mm'):
                self.assertEqual(a[key],b[key])
        self.assertEqual(f.build(0,1,control='source')[2]['source_distance_mm'],40.)
        self.assertEqual(f.build(0,1,control='plane')[2]['plane_mm'],18.)
        np.testing.assert_allclose(align_plane(f.reference(plane_mm=18.),'pec'),f.reference(),atol=1e-13,rtol=0)

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
                mesh_mm={}, plane_mm=12., excited_port=port)
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
            self.assertEqual(len(result['missing']), 13)


    def test_diagnostic_window_is_explicit_and_bounded_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp, patch(__name__+'.popen_group') as launch:
            for window in (0., -1., np.nan, .5e-9, 33e-9):
                with self.assertRaises(ValueError):
                    acquire(Path(tmp)/'absent', window_s=window, allow_cap=True)
            with self.assertRaises(ValueError):
                acquire(Path(tmp)/'absent', window_s=8e-9)
            with self.assertRaises(ValueError):
                acquire(Path(tmp)/'absent', window_s=8e-9, pilot_s=.5e-9, allow_cap=True)
            launch.assert_not_called()
            self.assertFalse((Path(tmp)/'absent').exists())

    def test_diagnostic_window_is_frozen_and_reaches_the_worker(self):
        proc = Mock(pid=123, win_job=None); proc.poll.return_value = 2
        with tempfile.TemporaryDirectory() as tmp, patch(__name__+'.popen_group', return_value=proc), \
                patch(__name__+'.wait_owned', return_value=2), patch(__name__+'.release_group'):
            out = Path(tmp)/'failed'
            with self.assertRaises(RuntimeError):
                acquire(out, n=0, kind='through', rate=1000., window_s=8e-9, allow_cap=True)
            plan = json.loads((out/'plan.json').read_text())
            self.assertTrue(all(p['cap_s']==8e-9 for p in plan['ports']))
            command = json.loads((out/'port1.worker.json').read_text())['command']
            self.assertEqual(float(command[command.index('--window-seconds')+1]),8e-9)
            self.assertEqual(f.build(0,1,kind='through')[2]['cap_s'],32e-9)
            self.assertFalse((out/'cap-diagnostic-matrix.npz').exists())

    def test_unused_source_is_refused_before_propagation(self):
        _,_,meta = f.build(0,1,kind='through')
        used = {name:[True] for name in meta['required_properties']}
        source = next(name for name in used if 'port_excite_' in name)
        used[source] = [False]
        operator = dict(timestep_s=meta['declared_dt_s']/.72,grid=meta['grid'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); freeze(root,0,'through','pec','ordinary')
            with patch.object(f.OpenStubSimulation,'run',return_value=operator) as run, \
                    patch.object(f,'usage',return_value=used):
                with self.assertRaisesRegex(ValueError,'operator CFL/material-usage'):
                    f.capture(root/'port1',0,1,kind='through')
                self.assertEqual(run.call_count,1)
                self.assertTrue(run.call_args.kwargs['setup_only'])
                self.assertFalse((root/'port1/raw').exists())


def main():
    p = argparse.ArgumentParser(description=__doc__); mode = p.add_mutually_exclusive_group(required=True)
    for name in ('preflight', 'fdtd', 'compare', 'study', 'worker'): mode.add_argument('--'+name, action='store_true')
    p.add_argument('--out', type=Path, required=True); p.add_argument('--mesh', type=int, choices=f.MESHES, default=1)
    p.add_argument('--kind', choices=f.KINDS, default='filter'); p.add_argument('--loss', choices=f.LOSSES, default='pec')
    p.add_argument('--control', choices=f.CONTROLS, default='ordinary'); p.add_argument('--port', type=int, choices=(1, 2), default=1)
    p.add_argument('--rate-mcps', type=float, default=15.); p.add_argument('--pilot-seconds', type=float)
    p.add_argument('--allow-capped-diagnostic', action='store_true')
    p.add_argument('--window-seconds', type=float, help='bounded diagnostic window; full quality gates still apply')
    args = p.parse_args()
    if args.allow_capped_diagnostic and not (args.fdtd or args.compare): p.error('capped diagnostics are separate from strict study')
    if args.window_seconds is not None:
        if (args.pilot_seconds is not None or not (args.fdtd or args.worker)
                or not np.isfinite(args.window_seconds)
                or not f.excitation.dgauss_duration_s(f.FREQUENCIES[-1]) <= args.window_seconds <= f.CAP_S):
            p.error('diagnostic window must contain the source, be at most 32 ns, and be separate from a pilot')
    if args.worker:
        cap = args.pilot_seconds if args.pilot_seconds is not None else args.window_seconds
        f.capture(args.out, args.mesh, args.port, args.kind, args.loss, args.control, cap); return
    if args.preflight: result = preflight(args.out, args.rate_mcps)
    elif args.fdtd: result = acquire(args.out, args.mesh, args.kind, args.loss, args.control, args.rate_mcps, args.pilot_seconds, args.allow_capped_diagnostic, args.window_seconds)
    elif args.compare: result = f.matrix(args.out, args.allow_capped_diagnostic)[1]
    else: result = study(args.out)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    if any('--'+s in sys.argv for s in ('preflight', 'fdtd', 'compare', 'study', 'worker')): main()
    else: unittest.main()
