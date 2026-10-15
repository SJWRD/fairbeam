"""Pure ring-hybrid controls and explicit opt-in serial native runs."""
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
import xml.etree.ElementTree as ET

import numpy as np
from fairbeam.network import network_s
from fairbeam.procutil import popen_group, release_group, terminate_group
from tests import ring_hybrid_fixture as f
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
            or f'fixed number of threads: {f.THREADS}' not in text
            or 'Create cylindrical FDTD operator' not in text
            or 'r=0 included...' in text
            or re.search(r'Unused primitive.*property: ring_and_feeds', text)):
        raise ValueError('complete annular native runtime/grid/clock/source header required')
    reported = list(map(int, grid.groups()[:3]))
    # rc3 displays the active angular count but allocates two periodic lines.
    allocation = [reported[0], reported[1]+2, reported[2]]
    cells = math.prod(allocation)
    if (reported != meta['reported_grid_lines'] or allocation != meta['native_lines']
            or cells != meta['native_cells'] or meta['native']['cells'] != cells
            or Decimal(grid[4]) not in (Decimal(cells), Decimal(format(cells, '.6g')))):
        raise ValueError('native periodic allocation differs from preflight')
    if int(interval[1]) != int(1/(2*f.FREQUENCIES[-1]*meta['native']['dt_s'])) or int(pulse[1]) != meta['max_timesteps']:
        raise ValueError('native energy/source clock differs from preflight')
    return dict(version='v0.37.0-rc3', threads=f.THREADS, nyquist_interval=int(interval[1]),
                excitation_timesteps=int(pulse[1]))


def acquire_serial(out, n, rate=40., control='ordinary'):
    out = Path(out).resolve()
    if out.exists():
        raise ValueError('fresh output directory required')
    _, meta = f.build(n, 1, control=control);estimate = forecast(meta, rate)
    if estimate > 1800:
        raise ValueError(f'forecast {estimate/60:.2f} min per source exceeds 30; defer before launch')
    out.mkdir(parents=True);source, runtime = f.identity(), f.runtime_identity()
    progress = dict(status='prepared', n=n, control=control, source_sha256=source, runtime=runtime,
        limits=f.LIMITS, estimated_seconds_per_source=estimate, rate_assumption_mcps=rate, ports=[])
    f.save(out/'progress.json', progress)
    for port in range(1, 5):
        if source != f.identity() or runtime != f.runtime_identity():
            progress.update(status='rejected', reason='source/runtime changed', qualified=False, active_launcher_pid=None)
            f.save(out/'progress.json', progress);raise RuntimeError('source/runtime changed between sources')
        command = [sys.executable, '-m', 'tests.test_ring_hybrid_fixture', '--fdtd', '--worker-case',
            '--mesh', str(n), '--port', str(port), '--control', control, '--out', str(out/f'port{port}')]
        start, wall = time.monotonic(), time.time();code, failure = None, None
        with (out/f'port{port}.log').open('w', encoding='utf-8') as log:
            proc = popen_group(command, cwd=f.ROOT/'python', stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, 'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '1'})
            progress.update(status='running', active_port=port, active_launcher_pid=proc.pid);f.save(out/'progress.json', progress)
            try:
                code = wait_owned(proc, seconds=1800.)
            except BaseException as error:
                failure = repr(error);raise
            finally:
                if proc.poll() is None:
                    terminate_group(proc.pid, grace=0, job=proc.win_job);proc.wait(timeout=15)
                release_group(proc)
                f.save(out/f'port{port}.worker.json', dict(command=command, pid=proc.pid, exit_code=code, failure=failure,
                    wall_seconds=time.time()-wall, monotonic_seconds=time.monotonic()-start,
                    deadline_seconds=1800., estimated_seconds=estimate))
                if failure or code:
                    progress.update(status='failed', reason=failure or f'worker exit {code}', qualified=False, active_launcher_pid=None)
                    f.save(out/'progress.json', progress)
        if code:
            raise RuntimeError('native source failed; preserve its records')
        try:
            root = out/f'port{port}';report = json.loads((root/'report.json').read_text())
            report['native_header'] = audit_header((out/f'port{port}.log').read_text(), report)
            f.save(root/'report.json', report)
            *_, quality = f.read(root)
            if not all(quality.values()):
                raise ValueError('native grid/clock/source/energy audit failed; no next source')
        except BaseException as error:
            progress.update(status='rejected', reason=repr(error), qualified=False, active_launcher_pid=None)
            f.save(out/'progress.json', progress);raise
        progress['ports'].append(dict(port=port, quality=quality));f.save(out/'progress.json', progress)
    try:
        _, result = f.matrix(out)
    except BaseException as error:
        progress.update(status='rejected', reason=repr(error), qualified=False, active_launcher_pid=None)
        f.save(out/'progress.json', progress);raise
    progress.update(status='complete', active_port=None, active_launcher_pid=None, completed_utc=datetime.now(timezone.utc).isoformat())
    f.save(out/'progress.json', progress)
    return result


def synthetic_waves(s, n=4):
    """Independent traveling states with different actual Z/gamma in each feed."""
    v = np.empty((4, 4, 4, 3, len(f.FREQUENCIES)), complex)
    i = np.empty((4, 4, 4, 2, 2, len(f.FREQUENCIES)), complex)
    voltage = (np.sqrt(f.Z0)*(np.eye(4)+s)).transpose(2, 1, 0)
    outward = -((np.eye(4)-s)/np.sqrt(f.Z0)).transpose(2, 1, 0)
    dr = f.B/n*1e-3
    for p in range(4):
        gamma = .4+.1*p+1j*2*np.pi*f.FREQUENCIES/f.C0*np.sqrt(f.ER)*(1+.02*p)
        z = 48.+p
        for k, offset in enumerate(f.PLANE_OFFSETS):
            distance = offset*f.B*1e-3
            for j, shift in enumerate((-dr, 0., dr)):
                d = distance+shift
                v[:, p, k, j] = voltage[:, p]*np.cosh(gamma*d)-z*outward[:, p]*np.sinh(gamma*d)
            for j, shift in enumerate((-dr/2, dr/2)):
                d = distance+shift
                current = outward[:, p]*np.cosh(gamma*d)-voltage[:, p]/z*np.sinh(gamma*d)
                for loop in (0, 1):
                    i[:, p, k, j, loop] = current
    return v, i


class RingHybridTests(unittest.TestCase):
    def test_wave_reference_against_independent_line_current_network(self):
        frequencies = np.r_[f.FREQUENCIES, 2e9, 4e9]
        lines = [(j, (j+1) % 4, np.sqrt(2)*f.Z0, m/(4*f.F0)) for j, m in enumerate((1, 1, 1, 3))]
        expected = network_s(frequencies, range(4), lines, z_ref=f.Z0)
        actual = f.reference(frequencies)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=4e-15)
        np.testing.assert_allclose(actual@actual.conj().transpose(0, 2, 1), np.eye(4)[None]+np.zeros_like(actual), rtol=0, atol=4e-15)
        np.testing.assert_allclose(actual, actual.transpose(0, 2, 1), rtol=0, atol=3e-15)

    def test_center_split_isolation_phase_and_geometric_length(self):
        s = f.reference()[100];g = f.geometry()
        np.testing.assert_allclose(abs(s[:, 0]), [0., 1/np.sqrt(2), 0., 1/np.sqrt(2)], rtol=0, atol=8e-16)
        self.assertAlmostEqual(abs(np.angle(s[3, 0]*s[1, 0].conjugate(), deg=True)), 180., places=12)
        self.assertAlmostEqual(2*np.pi*g['ring_radius_mm']*1e-3, 1.5*f.C0/(f.F0*np.sqrt(f.ER)), places=15)
        for z, key in ((f.Z0, 'feed_width_mm'), (np.sqrt(2)*f.Z0, 'ring_width_mm')):
            self.assertAlmostEqual(f.impedance(g[key]/f.B, f.ER), z, places=12)

    def test_two_measured_plane_pairs_recover_unknown_feeds(self):
        target = f.reference();v, i = synthetic_waves(target)
        actual, diagnostics = f.extract(v, i)
        np.testing.assert_allclose(actual, target, atol=2e-14, rtol=0)
        self.assertLess(diagnostics['plane_s_abs'], 2e-14)
        self.assertLess(diagnostics['transfer_det_abs'], 2e-14)
        self.assertLess(diagnostics['transfer_symmetry_abs'], 2e-14)
        self.assertLess(diagnostics['transfer_fit_rel'], 2e-14)
        self.assertGreater(abs(diagnostics['local_current_half_factor']-1).max(), 1e-5)

    def test_wrong_current_and_contour_scaling_remain_wrong(self):
        target = f.reference();v, i = synthetic_waves(target)
        actual, _ = f.extract(v, i*1.03)
        self.assertGreater(abs(actual-target).max(), f.LIMITS['s_target_abs'])
        i[:, :, :, :, 1] *= 1.03
        _, diagnostics = f.extract(v, i)
        self.assertGreater(diagnostics['contour_s_abs'], f.LIMITS['contour_s_abs'])

    def test_transfer_errors_are_not_normalized_away(self):
        v, i = synthetic_waves(f.reference())
        v[:, :, 2] *= 1.03
        _, diagnostic = f.extract(v, i)
        self.assertGreater(diagnostic['transfer_det_abs'], f.LIMITS['transfer_det_abs'])
        self.assertGreater(diagnostic['transfer_symmetry_abs'], f.LIMITS['transfer_symmetry_abs'])

    def test_missing_or_unexcited_fields_are_rejected(self):
        v, i = synthetic_waves(f.reference())
        for a, b in ((v[:3], i), (v*np.nan, i), (v*0, i), (v, i*np.nan)):
            with self.assertRaises(ValueError):
                f.extract(a, b)

    def test_cylindrical_ring_and_cartesian_feeds_are_explicit(self):
        sim, meta = f.build(4, 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'input.xml';sim.fdtd.Write2XML(str(path));tree = ET.parse(path)
        self.assertEqual(tree.find('ContinuousStructure').get('CoordSystem'), '1')
        polygons = tree.findall('.//Polygon')
        self.assertEqual(len(polygons), 4)
        self.assertTrue(all(p.get('CoordSystem') == '0' for p in polygons))
        self.assertEqual(meta['native_lines'][1], meta['input_lines'][1]+1)
        self.assertEqual(meta['native_lines'][1], meta['reported_grid_lines'][1]+2)
        self.assertGreater(meta['mesh_mm_rad']['r'][0], 0)
        with self.assertRaisesRegex(ValueError, 'Cartesian bundle'):
            sim.to_bundle()

    def test_new_cartesian_feeds_receive_cylindrical_coordinate_input(self):
        sim, _ = f.build(2, 1);g = f.geometry()
        self.assertEqual(sim.csx.GetCoordInputType(), 1)
        for angle in g['port_angles_rad']:
            for offset in (3., 6., 12.):
                prop = sim.csx.GetPropertyByCoordPriority([g['ring_radius_mm']+offset*f.B, angle, 0.])
                self.assertEqual(prop.GetName(), 'ring_and_feeds')
            prop = sim.csx.GetPropertyByCoordPriority([g['ring_radius_mm']+6*f.B, angle+.1, 0.])
            self.assertEqual(prop.GetName(), 'homogeneous')
        self.assertEqual(f.conductor_usage(sim), [False]*5)

    def test_missing_native_conductor_usage_rejects_admission(self):
        _, meta = f.build(2, 1)
        run = dict(timestep_s=meta['declared_dt_s']*2, grid=meta['reported_grid_lines'],
                   threads=4, engine='cpu', exact_endcriteria=True, converged=False, energy_trace=[])
        record = dict(setup_only=True, ratio_limit=f.NATIVE_STEP_RATIO_MAX,
                      planned_dt_s=meta['declared_dt_s'], run=run, required_conductors_used=[True]*5)
        self.assertTrue(f.native_bound_ok({**meta, 'operator_preflight': record}))
        for usage in (None, [], [True]*4, [True, False, True, True, True]):
            self.assertFalse(f.native_bound_ok({**meta, 'operator_preflight':
                {**record, 'required_conductors_used': usage}}))

    def test_spatial_controls_keep_the_same_prospective_clock(self):
        _, nominal = f.build(4, 1)
        for control in f.SPATIAL_CONTROLS:
            _, meta = f.build(4, 1, control=control)
            self.assertEqual(meta['declared_dt_s'], nominal['declared_dt_s'])
            self.assertEqual(meta['plane_radii_mm'], nominal['plane_radii_mm'])
            self.assertEqual(meta['geometry'], nominal['geometry'])
        _, time_case = f.build(4, 1, control='time')
        self.assertEqual(time_case['cap_s'], 2*nominal['cap_s'])
        self.assertEqual(time_case['end_db'], nominal['end_db']-10)
        _, dt_case = f.build(4, 1, control='dt')
        self.assertAlmostEqual(dt_case['declared_dt_s']/nominal['declared_dt_s'], .7)

    def test_invalid_geometry_and_over_budget_do_not_launch(self):
        for n, p, c in ((True, 1, 'ordinary'), (3, 1, 'ordinary'), (4, True, 'ordinary'), (4, 1, 'bad')):
            with self.assertRaises(ValueError):
                f.build(n, p, control=c)
        with tempfile.TemporaryDirectory() as temp, patch(__name__+'.popen_group') as launch:
            out = Path(temp)/'not-created'
            with self.assertRaisesRegex(ValueError, 'exceeds 30'):
                acquire_serial(out, 4, .01)
            self.assertFalse(out.exists());launch.assert_not_called()
        for rate in (True, 0, -1, np.nan):
            with self.assertRaises(ValueError):
                forecast({}, rate)
        for freq in ([], [0.], [np.nan], [[f.F0]], np.array([f.F0+1j])):
            with self.assertRaises(ValueError):
                f.reference(freq)

    def test_unforced_operator_admission_precedes_propagation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(f.Simulation, 'run', return_value={}) as run:
            out = Path(temp)/'case'
            with self.assertRaisesRegex(ValueError, 'native stability'):
                f.acquire(out, 4, 1)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.kwargs['setup_only'])
            self.assertFalse((out/'raw').exists())

    def test_annular_header_checks_periodic_allocation_and_exact_stats(self):
        meta = dict(reported_grid_lines=[101, 999, 21], native_lines=[101, 1001, 21],
                    native_cells=2123121, native=dict(cells=2123121, dt_s=1e-14), max_timesteps=100000)
        def header(count):
            return ('openEMS 64bit -- version v0.37.0-rc3\nfixed number of threads: 4\n'
                'Create cylindrical FDTD operator\nAlpha is a full 2*PI => closed Cylinder\n'
                f'FDTD simulation size: 101x999x21 --> {count} FDTD cells\n'
                f'Exact-endcriteria: evaluating the end criteria every {int(1/(2*f.FREQUENCIES[-1]*1e-14))} timestep\n'
                'Excitation signal length is: 100000 timesteps\n')
        for count in ('2123121', '2.12312e+06'):
            self.assertEqual(audit_header(header(count), meta)['threads'], 4)
            quiet = header(count).replace('Alpha is a full 2*PI => closed Cylinder\n', '')
            self.assertEqual(audit_header(quiet, meta)['threads'], 4)
        for text in (header('2.12313e+06'), header('NaN'), header('2123121').replace('101x999x21', '101x1001x21'),
                     header('2123121')+'r=0 included...', header('2123121')+
                     'Warning: Unused primitive (type: Polygon) detected in property: ring_and_feeds!'):
            with self.assertRaises(ValueError):
                audit_header(text, meta)
        with self.assertRaises(ValueError):
            audit_header(header('2123121'), {**meta, 'native': {**meta['native'], 'cells': 2123120}})

    def test_bad_completed_worker_retains_rejection_and_no_next_source(self):
        class Worker:
            pid, win_job = 777, None
            def wait(self, timeout=None): return 0
            def poll(self): return 0
        for failure in ('json', 'header', 'trace'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/'cohort'
                def launch(command, **options):
                    dest = Path(command[command.index('--out')+1]);dest.mkdir()
                    (dest/'report.json').write_text('{' if failure == 'json' else '{}');return Worker()
                header = {'side_effect': ValueError('header')} if failure == 'header' else {'return_value': {}}
                with patch(__name__+'.popen_group', side_effect=launch) as start, patch(__name__+'.release_group'), \
                        patch(__name__+'.audit_header', **header), patch.object(f, 'read', side_effect=ValueError('changed trace')):
                    with self.assertRaises(ValueError):
                        acquire_serial(out, 4, 1e6)
                    self.assertEqual(start.call_count, 1)
                record = json.loads((out/'progress.json').read_text())
                self.assertEqual(record['status'], 'rejected');self.assertIsNone(record['active_launcher_pid'])
                self.assertTrue((out/'port1.worker.json').exists())

    def test_missing_or_mislabeled_cohorts_cannot_qualify(self):
        target = f.reference()
        with tempfile.TemporaryDirectory() as temp, patch.object(f, 'matrix', side_effect=FileNotFoundError('missing')):
            result = f.study(temp)
        self.assertFalse(result['qualified'])
        self.assertEqual(len(result['missing_or_rejected']), len(f.MESHES)+len(f.CONTROLS)-1)
        with tempfile.TemporaryDirectory() as temp, patch.object(f, 'matrix', return_value=(target,dict(n=4,control='ordinary',target_passes=True))):
            result = f.study(temp)
        self.assertFalse(result['qualified']);self.assertTrue(result['missing_or_rejected'])
        with tempfile.TemporaryDirectory() as temp, patch.object(f, 'matrix', side_effect=AssertionError('raw replay differs')):
            result = f.study(temp)
        self.assertFalse(result['qualified']);self.assertTrue(result['missing_or_rejected'])

    def test_failed_target_is_not_hidden_by_zero_control_difference(self):
        def matrix(path):
            match = re.fullmatch(r'n(\d+)(?:_(\w+))?', path.name)
            n, control = int(match[1]), match[2] or 'ordinary'
            return f.reference(), dict(n=n, control=control, target_passes=control != 'dt')
        with tempfile.TemporaryDirectory() as temp, patch.object(f, 'matrix', side_effect=matrix):
            result = f.study(temp)
        self.assertFalse(result['qualified']);self.assertFalse(result['controls']['dt']['passes'])
        self.assertEqual(result['controls']['dt']['s_abs'], 0.)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('preflight', 'fdtd', 'analyse', 'study'):
        modes.add_argument('--'+mode, action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mesh', type=int, choices=f.MESHES+f.PILOT_MESHES, default=f.MESHES[0])
    parser.add_argument('--control', choices=f.CONTROLS, default='ordinary')
    parser.add_argument('--rate-mcps', type=float, default=40.)
    parser.add_argument('--worker-case', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, choices=(1, 2, 3, 4), default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.preflight:
        args.out.mkdir(parents=True, exist_ok=False);rows = []
        for n in f.PILOT_MESHES+f.MESHES:
            for control in f.CONTROLS if n == f.CONTROL_MESH else ('ordinary',):
                sim, meta = f.build(n, 1, control=control);path = args.out/f'n{n}_{control}.xml'
                sim.fdtd.Write2XML(str(path));seconds = forecast(meta, args.rate_mcps)
                rows.append(dict(meta=meta, input_sha256=f.sha(path), forecast_seconds=seconds, deferred=seconds > 1800))
        f.save(args.out/'plan.json', dict(protocol=f.PROTOCOL, rows=rows, source=f.identity(), runtime=f.runtime_identity(), limits=f.LIMITS))
        print(json.dumps([dict(n=r['meta']['n'], control=r['meta']['control'], cells=r['meta']['native_cells'],
                              dt_s=r['meta']['declared_dt_s'], seconds=r['forecast_seconds'], deferred=r['deferred']) for r in rows], indent=2))
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
