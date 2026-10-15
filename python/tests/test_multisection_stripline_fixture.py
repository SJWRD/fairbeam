"""Opt-in three-section field comparison; default discovery starts no FDTD."""
import argparse
from decimal import Decimal
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from fairbeam.network import network_s
from fairbeam.procutil import popen_group, release_group, terminate_group
from tests import multisection_stripline_fixture as f
from tests.stripline_fixture import impedance
from tests.test_coax_resonator import wait_owned


def forecast(meta, rate):
    if isinstance(rate, bool) or not np.isfinite(rate) or rate <= 0:
        raise ValueError('positive finite conservative throughput required')
    return 20+1.15*meta['native_cells']*meta['max_timesteps']/(rate*1e6)


def audit_header(text, meta):
    text = text.rsplit(f.PROPAGATION_MARKER, 1)[-1]
    if re.search(r'forced timestep:.*larger than calculated timestep', text):
        raise ValueError('forced step exceeds native stability bound')
    grid = re.search(r'FDTD simulation size: (\d+)x(\d+)x(\d+) --> (\d+(?:\.\d+)?(?:[eE][+-]?\d+)?) FDTD cells', text)
    interval = re.search(r'Exact-endcriteria: evaluating the end criteria every (\d+) timestep', text)
    pulse = re.search(r'Excitation signal length is: (\d+) timesteps', text)
    if ('openEMS 64bit -- version v0.37.0-rc3' not in text or not grid or not interval or not pulse
            or f'fixed number of threads: {f.THREADS}' not in text):
        raise ValueError('complete native runtime/grid/clock/source header required')
    dimensions = list(map(int, grid.groups()[:3]))
    cells = math.prod(dimensions)
    # rc3's console uses default six-significant-digit floating output;
    # its statistics file retains the exact count. Check that and the grid
    # product exactly; accept only the exact or correctly rounded console count.
    printed = Decimal(grid[4])
    if (dimensions != meta['native_lines'] or cells != meta['native_cells']
            or meta['native']['cells'] != cells
            or printed not in (Decimal(cells), Decimal(format(cells, '.6g')))):
        raise ValueError('native grid differs from preflight')
    if int(interval[1]) != int(1/(2*f.FREQUENCIES[-1]*meta['native']['dt_s'])) or int(pulse[1]) != meta['max_timesteps']:
        raise ValueError('native probe/source clock differs from preflight')
    return dict(version='v0.37.0-rc3', threads=f.THREADS, nyquist_interval=int(interval[1]),
                excitation_timesteps=int(pulse[1]))


def acquire_serial(out, n, rate=40., control='ordinary'):
    out = Path(out).resolve()
    if out.exists():
        raise ValueError('fresh output directory required')
    _, meta = f.build(n, 1, control=control)
    estimate = forecast(meta, rate)
    if estimate > 1800:
        raise ValueError(f'forecast {estimate/60:.2f} min per port exceeds 30; defer before launch')
    out.mkdir(parents=True)
    source, runtime = f.identity(), f.runtime_identity()
    progress = dict(status='prepared', n=n, control=control, source_sha256=source, runtime=runtime,
                    limits=f.LIMITS, estimated_seconds_per_port=estimate, rate_assumption_mcps=rate, ports=[])
    f.save(out/'progress.json', progress)
    for port in range(1, 5):
        if source != f.identity() or runtime != f.runtime_identity():
            progress.update(status='rejected', reason='source/runtime changed', qualified=False, active_launcher_pid=None)
            f.save(out/'progress.json', progress)
            raise RuntimeError('source/runtime changed between excitations')
        destination = out/f'port{port}'
        command = [sys.executable, '-m', 'tests.test_multisection_stripline_fixture', '--fdtd', '--worker-case',
                   '--mesh', str(n), '--port', str(port), '--control', control, '--out', str(destination)]
        start, wall = time.monotonic(), time.time()
        code, failure = None, None
        with (out/f'port{port}.log').open('w', encoding='utf-8') as log:
            proc = popen_group(command, cwd=f.ROOT/'python', stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, 'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '1'})
            progress.update(status='running', active_port=port, active_launcher_pid=proc.pid)
            f.save(out/'progress.json', progress)
            try:
                code = wait_owned(proc, seconds=1800.)
            except BaseException as error:
                failure = repr(error)
                raise
            finally:
                if proc.poll() is None:
                    terminate_group(proc.pid, grace=0, job=proc.win_job)
                    proc.wait(timeout=15)
                release_group(proc)
                f.save(out/f'port{port}.worker.json', dict(command=command, pid=proc.pid, exit_code=code,
                    failure=failure, wall_seconds=time.time()-wall, monotonic_seconds=time.monotonic()-start,
                    deadline_seconds=1800., estimated_seconds=estimate))
                if failure or code:
                    progress.update(status='failed', reason=failure or f'worker exit {code}',
                                    qualified=False, active_launcher_pid=None)
                    f.save(out/'progress.json', progress)
        if code:
            raise RuntimeError('native worker failed; retain its log and raw input')
        try:
            report = json.loads((destination/'report.json').read_text(encoding='utf-8'))
            report['native_header'] = audit_header((out/f'port{port}.log').read_text(encoding='utf-8'), report)
            f.save(destination/'report.json', report)
            *_, quality = f.read(destination)
            if not all(quality.values()):
                raise ValueError('native grid/clock/source/energy audit failed; no next source')
        except BaseException as error:
            progress.update(status='rejected', reason=repr(error), qualified=False, active_launcher_pid=None)
            f.save(out/'progress.json', progress)
            raise
        progress['ports'].append(dict(port=port, quality=quality))
        f.save(out/'progress.json', progress)
    try:
        _, result = f.matrix(out)
    except BaseException as error:
        progress.update(status='rejected', reason=repr(error), qualified=False, active_launcher_pid=None)
        f.save(out/'progress.json', progress)
        raise
    progress.update(status='complete', active_port=None, active_launcher_pid=None,
                    completed_utc=datetime.now(timezone.utc).isoformat())
    f.save(out/'progress.json', progress)
    return result


def synthetic_waves(s, dx=.0004):
    """Independent local traveling waves around a nonuniform circuit DUT."""
    v = np.empty((4, 4, 3, len(f.FREQUENCIES)), complex)
    current = np.empty((4, 4, 2, 2, len(f.FREQUENCIES)), complex)
    q = np.array([[1., 1.], [1., -1.]])/np.sqrt(2)
    voltage = (np.sqrt(f.Z0)*(np.eye(4)+s)).transpose(2, 1, 0)
    inward = ((np.eye(4)-s)/np.sqrt(f.Z0)).transpose(2, 1, 0)
    for side, (ids, sign) in enumerate((((0, 3), 1), ((1, 2), -1))):
        vm = np.einsum('mc,ecf->emf', q, np.take(voltage, ids, axis=1))
        im = sign*np.einsum('mc,ecf->emf', q, np.take(inward, ids, axis=1))
        # Different propagation in each side/mode deliberately prevents a
        # single uniform-DUT correction from recovering these local samples.
        gamma = np.array([.4+.1*side+1j*2*np.pi*f.FREQUENCIES/f.C0*np.sqrt(f.ER)*(1+.02*mode+.01*side)
                          for mode in (0, 1)])
        z = np.array([53., 47.])[:, None]
        for j, distance in enumerate((-dx, 0., dx)):
            local = vm*np.cosh(gamma*distance)-z*im*np.sinh(gamma*distance)
            v[:, ids, j, :] = np.einsum('cm,emf->ecf', q.T, local)
        for j, distance in enumerate((-dx/2, dx/2)):
            local = im*np.cosh(gamma*distance)-vm/z*np.sinh(gamma*distance)
            for loop in (0, 1):
                current[:, ids, j, loop, :] = np.einsum('cm,emf->ecf', q.T, local)
    return v, current


class MultisectionStriplineTests(unittest.TestCase):
    def test_inverse_map_and_isolated_strip_limit(self):
        for part in f.geometry()['sections']:
            a = np.tanh(np.pi*part['width_mm']/(2*f.B))
            b = np.tanh(np.pi*(part['width_mm']+part['gap_mm'])/(2*f.B))
            np.testing.assert_allclose(f.mode_impedance(np.array([a*b, a/b])),
                                       part['modal_impedances_ohm'], rtol=0, atol=4e-14)
        w = 2.6
        independent = impedance(w/f.B, f.ER)
        self.assertAlmostEqual(float(f.mode_impedance(np.tanh(np.pi*w/(2*f.B)))), independent, places=12)

    def test_reference_against_independent_nodal_network(self):
        q = np.array([[1., 1.], [1., -1.]])/np.sqrt(2)
        p = np.zeros((4, 4))
        p[0, :2], p[3, :2], p[1, 2:], p[2, 2:] = q[0], q[1], q[0], q[1]
        modal = np.zeros((201, 4, 4), complex)
        for mode, ids in enumerate(((0, 2), (1, 3))):
            edges = [(i, i+1, x['modal_impedances_ohm'][mode], 1/(4*f.F0))
                     for i, x in enumerate(f.geometry()['sections'])]
            expected = network_s(f.FREQUENCIES, [0, 3], edges, z_ref=f.Z0)
            for j, a in enumerate(ids):
                for k, b in enumerate(ids):
                    modal[:, a, b] = expected[:, j, k]
        np.testing.assert_allclose(f.reference(), p@modal@p.T, rtol=0, atol=3e-15)

    def test_exact_coupling_is_not_fitted_to_first_order_target(self):
        s = f.reference()
        self.assertAlmostEqual(abs(s[100, 3, 0]), .10031734837799729, places=14)
        self.assertGreater(abs(s[100, 3, 0])-.1, .0003)
        np.testing.assert_allclose(s@s.conj().transpose(0, 2, 1), np.eye(4)[None]+np.zeros_like(s), atol=2e-15, rtol=0)
        np.testing.assert_allclose(s, s.transpose(0, 2, 1), atol=2e-15, rtol=0)

    def test_sections_physically_overlap_at_the_joins(self):
        a, b = f.geometry()['sections'][:2]
        # Positive strip intervals are [gap/2, gap/2+width]. No ideal wire
        # or diagonal shortcut connects the geometric steps.
        overlap = min(a['gap_mm']/2+a['width_mm'], b['gap_mm']/2+b['width_mm'])-max(a['gap_mm']/2, b['gap_mm']/2)
        self.assertGreater(overlap, 1.)
        sim, meta = f.build(4, 1)
        self.assertEqual(meta['geometry']['joins_mm'], [-f.LENGTH/2, f.LENGTH/2])
        self.assertEqual(len(sim.ports), 4)

    def test_local_correction_recovers_a_nonuniform_dut(self):
        target = f.reference()
        v, i = synthetic_waves(target)
        s, d = f.extract(v, i)
        np.testing.assert_allclose(s, target, atol=2e-15, rtol=0)
        self.assertGreater(abs(d['raw_s']-target).max(), 1e-5)
        self.assertLess(d['recurrence_residual'], 1e-14)
        self.assertGreater(abs(d['local_current_half_factor'][0]-d['local_current_half_factor'][1]).max(), 1e-6)

    def test_current_scale_and_contour_errors_are_not_normalized_away(self):
        target = f.reference()
        v, i = synthetic_waves(target)
        s, _ = f.extract(v, i*1.03)
        self.assertGreater(abs(s-target).max(), f.LIMITS['s_target_abs'])
        i[:, :, :, 1] *= 1.03
        _, d = f.extract(v, i)
        self.assertGreater(d['contour_s_abs'], f.LIMITS['contour_s_abs'])

    def test_missing_fields_and_excitation_are_rejected(self):
        v, i = synthetic_waves(f.reference())
        for a, b in ((v[:3], i), (v*np.nan, i), (v*0, i), (v, i*np.nan)):
            with self.assertRaises(ValueError):
                f.extract(a, b)
        with self.assertRaises(ValueError):
            f.assemble(v[:, :, 1]*0, i[:, :, 0, 0]*0)

    def test_native_inputs_have_exact_planes_and_control_changes(self):
        _, nominal = f.build(4, 1)
        x = np.array(nominal['mesh_mm']['x'])
        for plane in nominal['voltage_planes_mm']+nominal['geometry']['joins_mm']:
            self.assertIn(plane, x)
        for name in f.CONTROLS[1:]:
            _, controlled = f.build(4, 1, control=name)
            self.assertEqual(controlled['geometry'], nominal['geometry'])
            self.assertEqual(controlled['voltage_planes_mm'], nominal['voltage_planes_mm'])
        _, time_case = f.build(4, 1, control='time')
        self.assertEqual(time_case['end_db'], nominal['end_db']-10)
        self.assertEqual(time_case['cap_s'], 1.5*nominal['cap_s'])

    def test_bad_inputs_and_over_budget_are_refused(self):
        for n, port, control in ((True, 1, 'ordinary'), (5, 1, 'ordinary'), (4, True, 'ordinary'), (4, 1, 'bad')):
            with self.assertRaises(ValueError):
                f.build(n, port, control=control)
        with tempfile.TemporaryDirectory() as temp, patch(__name__+'.popen_group') as start:
            out = Path(temp)/'not-created'
            with self.assertRaisesRegex(ValueError, 'exceeds 30'):
                acquire_serial(out, 6, .01)
            self.assertFalse(out.exists())
            start.assert_not_called()
        for rate in (True, 0, -1, np.nan):
            with self.assertRaises(ValueError):
                forecast({}, rate)

    def test_native_operator_admission_precedes_any_propagation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(f.Simulation, 'run', return_value={}) as run:
            out = Path(temp)/'case'
            with self.assertRaisesRegex(ValueError, 'native stability'):
                f.acquire(out, 4, 1)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.kwargs['setup_only'])
            self.assertFalse((out/'raw').exists())
            self.assertFalse(json.loads((out/'operator.json').read_text())['passes'])

    def test_capped_or_unfinished_excitation_is_never_native_complete(self):
        _, meta = f.build(4, 1)
        dt = meta['declared_dt_s']
        steps = int(2e-9/dt)
        meta['native'] = dict(cells=meta['native_cells'], dt_s=dt, timesteps=steps, numerical_time_s=steps*dt)
        meta['run'] = dict(grid=meta['native_lines'], converged=True, exact_endcriteria=True, threads=4)
        meta['native_header'] = dict(version='v0.37.0-rc3', threads=4,
            nyquist_interval=int(1/(2*f.FREQUENCIES[-1]*dt)), excitation_timesteps=meta['max_timesteps'])
        meta['operator_preflight'] = dict(setup_only=True, ratio_limit=f.NATIVE_STEP_RATIO_MAX, planned_dt_s=dt,
            run=dict(timestep_s=2*dt, grid=meta['native_lines'], threads=4, engine='cpu',
                     exact_endcriteria=True, converged=False, energy_trace=[]))
        self.assertTrue(all(f.quality(meta).values()))
        meta['run']['hit_timestep_limit'] = True
        self.assertFalse(f.quality(meta)['energy'])
        meta['native']['numerical_time_s'] = meta['source_duration_s']/2
        self.assertFalse(f.quality(meta)['source'])
        meta['operator_preflight']['run']['timestep_s'] = dt
        self.assertFalse(f.quality(meta)['operator'])

    def test_native_header_and_probe_clocks_must_match_preflight(self):
        _, meta = f.build(4, 1)
        dt = meta['declared_dt_s']
        meta['native'] = dict(dt_s=dt, numerical_time_s=2e-9, cells=meta['native_cells'])
        interval = int(1/(2*f.FREQUENCIES[-1]*dt))
        shape = 'x'.join(str(v) for v in meta['native_lines'])
        text = (f'openEMS 64bit -- version v0.37.0-rc3\nfixed number of threads: 4\n'
            f'FDTD simulation size: {shape} --> {meta["native_cells"]} FDTD cells\n'
            f'Exact-endcriteria: evaluating the end criteria every {interval} timestep\n'
            f'Excitation signal length is: {meta["max_timesteps"]} timesteps\n')
        self.assertEqual(audit_header(text, meta)['nyquist_interval'], interval)
        with self.assertRaisesRegex(ValueError, 'clock'):
            audit_header(text.replace(f'{interval} timestep', '1 timestep'), meta)
        with self.assertRaisesRegex(ValueError, 'stability'):
            audit_header('forced timestep: 1 ps larger than calculated timestep', {})
        stride = int(1/(2*f.FREQUENCIES[-1]*dt*4))
        data = np.column_stack((np.arange(12)*stride*dt, np.ones(12)))
        self.assertEqual(f.trace_clock(data, meta)['stride_steps'], stride)
        for changed in (data*[2, 1], data*np.nan, np.vstack((data[:5], data[4:]))):
            with self.assertRaises(ValueError):
                f.trace_clock(changed, meta)

    def test_large_grid_header_accepts_exact_and_rounded_scientific_counts(self):
        _, meta = f.build(8, 1)
        dt = meta['declared_dt_s']
        meta['native'] = dict(dt_s=dt, cells=meta['native_cells'])
        self.assertEqual(meta['native_cells'], 1280125)
        for count in ('1280125', '1.28012e+06'):
            text = (f'openEMS 64bit -- version v0.37.0-rc3\nfixed number of threads: 4\n'
                f'FDTD simulation size: 385x133x25 --> {count} FDTD cells\n'
                f'Exact-endcriteria: evaluating the end criteria every {int(1/(2*f.FREQUENCIES[-1]*dt))} timestep\n'
                f'Excitation signal length is: {meta["max_timesteps"]} timesteps\n')
            self.assertEqual(audit_header(text, meta)['threads'], 4)

    def test_large_header_keeps_exact_grid_and_stats_guards(self):
        _, meta = f.build(8, 1)
        dt = meta['declared_dt_s']
        meta['native'] = dict(dt_s=dt, cells=meta['native_cells'])
        template = (f'openEMS 64bit -- version v0.37.0-rc3\nfixed number of threads: 4\n'
            'FDTD simulation size: 385x133x25 --> COUNT FDTD cells\n'
            f'Exact-endcriteria: evaluating the end criteria every {int(1/(2*f.FREQUENCIES[-1]*dt))} timestep\n'
            f'Excitation signal length is: {meta["max_timesteps"]} timesteps\n')
        for count in ('1.28013e+06', '1.28e+06', '1280124', 'NaN', 'inf'):
            with self.subTest(count=count), self.assertRaises(ValueError):
                audit_header(template.replace('COUNT', count), meta)
        valid = template.replace('COUNT', '1.28012e+06')
        with self.assertRaises(ValueError):
            audit_header(valid, {**meta, 'native': {**meta['native'], 'cells': 1280124}})
        with self.assertRaises(ValueError):
            audit_header(valid.replace('385x133x25', '385x133x24'), meta)

    def test_completed_bad_worker_retains_rejection_instead_of_running_pid(self):
        class Worker:
            pid, win_job = 777, None
            def wait(self, timeout=None): return 0
            def poll(self): return 0
        for failure in ('json', 'header', 'trace'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/'cohort'
                def launch(command, **options):
                    dest = Path(command[command.index('--out')+1])
                    dest.mkdir()
                    (dest/'report.json').write_text('{' if failure == 'json' else '{}')
                    return Worker()
                header = {'side_effect': ValueError('header')} if failure == 'header' else {'return_value': {}}
                with patch(__name__+'.popen_group', side_effect=launch) as start, \
                        patch(__name__+'.release_group'), patch(__name__+'.audit_header', **header), \
                        patch.object(f, 'read', side_effect=ValueError('changed trace')):
                    with self.assertRaises(ValueError):
                        acquire_serial(out, 4, 1000.)
                    self.assertEqual(start.call_count, 1)
                row = json.loads((out/'progress.json').read_text())
                self.assertEqual(row['status'], 'rejected')
                self.assertIsNone(row['active_launcher_pid'])
                self.assertTrue((out/'port1.worker.json').exists())

    def test_missing_mesh_or_failed_control_cannot_qualify(self):
        target = f.reference()
        def fake(path):
            name = path.name
            controlled = '_' in name
            s = target.copy()
            if name == 'n6':
                s[:, 0, 0] += .01
            return s, dict(target_passes=not controlled)
        with tempfile.TemporaryDirectory() as temp, patch.object(f, 'matrix', side_effect=fake):
            result = f.study(temp)
        self.assertFalse(result['qualified'])
        self.assertFalse(result['mesh_changes'][0]['passes'])
        self.assertFalse(result['controls']['dt']['passes'])
        self.assertFalse(result['directivity_qualified'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('preflight', 'fdtd', 'analyse', 'study'):
        modes.add_argument('--'+name, action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mesh', type=int, choices=f.MESHES+f.PILOT_MESHES, default=f.MESHES[0])
    parser.add_argument('--control', choices=f.CONTROLS, default='ordinary')
    parser.add_argument('--rate-mcps', type=float, default=40.)
    parser.add_argument('--worker-case', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, choices=(1, 2, 3, 4), default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.preflight:
        args.out.mkdir(parents=True, exist_ok=False)
        rows = []
        for n in f.PILOT_MESHES+f.MESHES:
            for control in f.CONTROLS if n == f.CONTROL_MESH else ('ordinary',):
                sim, meta = f.build(n, 1, control=control)
                path = args.out/f'n{n}_{control}.xml'
                sim.fdtd.Write2XML(str(path))
                seconds = forecast(meta, args.rate_mcps)
                rows.append(dict(meta=meta, input_sha256=f.sha(path), forecast_seconds=seconds, deferred=seconds > 1800))
        f.save(args.out/'plan.json', dict(protocol=f.PROTOCOL, rows=rows, source=f.identity(), runtime=f.runtime_identity(), limits=f.LIMITS))
        print(json.dumps([dict(n=r['meta']['n'], control=r['meta']['control'], cells=r['meta']['native_cells'],
                              seconds=r['forecast_seconds'], deferred=r['deferred']) for r in rows], indent=2))
    elif args.analyse:
        print(json.dumps(f.matrix(args.out)[1], indent=2))
    elif args.study:
        print(json.dumps(f.study(args.out), indent=2))
    elif args.worker_case:
        meta = f.acquire(args.out, args.mesh, args.port, args.control)
        print(json.dumps(dict(n=args.mesh, port=args.port, native=meta['native']), indent=2))
    else:
        print(json.dumps(acquire_serial(args.out, args.mesh, args.rate_mcps, args.control), indent=2))


if __name__ == '__main__':
    if len(sys.argv) == 1:
        unittest.main()
    else:
        main()
