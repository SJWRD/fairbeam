"""Own circuit and mesh checks for the opt-in Example 8.10 fixture."""
import tempfile
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys
import time
from unittest.mock import Mock, patch
from fairbeam.procutil import popen_group, release_group, terminate_group
from tests.test_coax_resonator import wait_owned
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import numpy as np
from tests import shunt_resonator_filter_fixture as f


def configurations():
    return ([(f'n{n}_{kind}',n,kind,'ordinary') for n in f.MESHES for kind in f.KINDS]
        +[(f'n1_filter_{control}',1,'filter',control) for control in f.CONTROLS if control!='ordinary'])


def forecast(meta, rate):
    if not np.isfinite(rate) or rate <= 0: raise ValueError('positive conservative rate required')
    return 1.2*meta['native_cells']*meta['max_timesteps']/(rate*1e6)+45.


def freeze(out, n, kind, control, cap_s=None):
    planned = []
    for port in (1, 2):
        holder = f.build(n, port, kind, control, cap_s); sim, meta = holder[0], holder[1]
        path = out/f'planned-p{port}.xml'; sim.fdtd.Write2XML(str(path))
        sim.fdtd.SetTimeStep(0.); sim.fdtd.SetNumberOfTimeSteps(1024)
        operator = out/f'planned-p{port}-operator.xml'; sim.fdtd.Write2XML(str(operator))
        planned.append(dict(**meta, input_sha256=f.sha(path), operator_input_sha256=f.sha(operator)))
    plan = dict(source=f.identity(), runtime=f.runtime_identity(), ports=planned)
    f.save(out/'plan.json', plan)
    return plan


def preflight(out,rate):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);rows=[]
    for name,n,kind,control in configurations():
        folder=out/name;folder.mkdir();plan=freeze(folder,n,kind,control)
        for meta in plan['ports']:
            seconds=forecast(meta,rate)
            rows.append(dict(name=name,**meta,forecast_seconds=seconds,deferred=seconds>1800.))
    result=dict(rows=rows,source=f.identity(),runtime=f.runtime_identity(),rate_mcps=rate,
        limits=f.LIMITS,dimensions=f.synthesis(),native_propagations=0,qualified=False)
    f.save(out/'preflight.json',result);return result


def wait_finite(proc, log_path, seconds=1800.):
    """Preserve the owned deadline and reject non-finite propagation energy.

    A zero energy with -inf dB is valid; only the energy value is checked.
    The caller's existing finally block terminates and releases its worker.
    """
    class Watched:
        def __getattr__(self, name):
            return getattr(proc, name)

        def wait(self, timeout=None):
            text=Path(log_path).read_text(encoding='utf-8', errors='replace')
            if f.MARKER in text and re.search(r'Energy:\s*~\s*[+-]?(?:nan|inf)\b', text.split(f.MARKER)[-1], re.I):
                raise ValueError('non-finite native propagation energy; source rejected')
            return proc.wait(timeout=timeout)

    return wait_owned(Watched(), seconds=seconds)


def acquire(out,n=0,kind='filter',control='ordinary',rate=60.,pilot_s=None,allow_cap=False,window_s=None):
    out = Path(out).resolve()
    if out.exists(): raise ValueError('fresh output directory required')
    if pilot_s is not None and (not np.isfinite(pilot_s) or not 0 < pilot_s <= .5e-9):
        raise ValueError('performance pilot must be in 0..0.5 ns')
    if window_s is not None and (pilot_s is not None or not allow_cap
            or not np.isfinite(window_s) or not f.excitation.dgauss_duration_s(f.FREQUENCIES[-1]) <= window_s <= f.CAP_S):
        raise ValueError('explicit capped diagnostic window must contain the source and not exceed the ordinary window')
    cap = pilot_s if pilot_s is not None else window_s
    holder = f.build(n, 1, kind, control, cap); meta = holder[1]
    estimate = forecast(meta, rate)
    if estimate > 1800.: raise ValueError('forecast exceeds 30 minutes; defer this cohort')
    out.mkdir(parents=True, exist_ok=False); plan = freeze(out, n, kind, control, cap)
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
            command = [sys.executable, '-m', 'tests.test_shunt_resonator_filter_fixture', '--worker',
                '--mesh', str(n), '--kind', kind, '--control', control,
                '--port', str(port), '--out', str(out/f'port{port}')]
            if pilot_s is not None: command.extend(('--pilot-seconds', str(pilot_s)))
            if window_s is not None: command.extend(('--window-seconds', str(window_s)))
            start, wall = time.monotonic(), time.time(); code = None; failure = None
            with (out/f'port{port}.log').open('x', encoding='utf-8') as log:
                proc = popen_group(command, cwd=f.ROOT/'python', stdout=log, stderr=subprocess.STDOUT)
                state.update(status='running', active_port=port, active_launcher_pid=proc.pid); f.save(progress, state)
                try: code = wait_finite(proc, out/f'port{port}.log', seconds=1800.)
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


def study(root):
    from fairbeam.network import shift_reference_planes
    root=Path(root);missing=[];rejected=[];results={};rows=[];mesh=[];controls=[]
    for name,n,kind,control in configurations():
        folder=root/name
        if not all((folder/f'port{p}/report.json').exists() for p in (1,2)):
            state=json.loads((folder/'progress.json').read_text()) if (folder/'progress.json').exists() else {}
            if state.get('status')=='rejected':rejected.append(dict(name=name,reason=state.get('reason')))
            else:missing.append(name)
            continue
        try:
            actual,row=f.matrix(folder)
            expected = dict(n=n, kind=kind, control=control)
            if any(row.get(key) != value for key, value in expected.items()):
                raise ValueError(f'cohort identity mismatch for {name}: expected {expected}')
        except (ValueError,AssertionError,FileNotFoundError) as error:
            rejected.append(dict(name=name,reason=repr(error)));continue
        results[name]=actual;rows.append(row)
    for kind in f.KINDS:
        for a,b in zip(f.MESHES[:-1],f.MESHES[1:]):
            first,second=f'n{a}_{kind}',f'n{b}_{kind}'
            if first not in results or second not in results:continue
            change=f.difference(results[second],results[first])
            mesh.append(dict(kind=kind,from_mesh=a,to_mesh=b,**change,
                passes=change['s_abs']<=f.LIMITS['mesh_s_abs'] and change['phase_deg']<=f.LIMITS['mesh_phase_deg']))
    for control in f.CONTROLS[1:]:
        other='n1_filter_'+control
        if 'n1_filter' not in results or other not in results:continue
        value=results[other]
        if control=='plane':value=shift_reference_planes(value,2j*np.pi*f.FREQUENCIES/f.C0,[-.004,-.004])
        change=f.difference(value,results['n1_filter'])
        controls.append(dict(control=control,**change,
            passes=change['s_abs']<=f.LIMITS['control_s_abs'] and change['phase_deg']<=f.LIMITS['control_phase_deg']))
    qualified=bool(not missing and not rejected and len(rows)==17 and len(mesh)==8 and len(controls)==5
        and all(v['passes'] for v in mesh+controls)
        and all(v['native_complete'] and v['measurement_passes'] and v['point_target_passes'] for v in rows))
    return dict(own_finite_geometry_qualified=qualified,qualified=qualified,missing=missing,rejected=rejected,
        mesh_changes=mesh,controls=controls,rows=rows,
        scope='Own finite coaxial geometry only. The original zero-connection circuit is a distinct analytical reference; no book-layout validation follows from this qualification.')


def abcd_reference(freq=f.FREQUENCIES,finite=True,plane_mm=28.):
    freq=np.asarray(freq,float);w=2*np.pi*freq;d=f.synthesis()
    def identity():return np.broadcast_to(np.eye(2,dtype=complex),(len(freq),2,2)).copy()
    def series(cap):
        a=identity();a[:,0,1]=1/(1j*w*cap);return a
    def shunt(delay):
        a=identity();a[:,1,0]=-1j/(f.Z0*np.tan(w*delay));return a
    def line(length_mm):
        theta=w*length_mm*1e-3/f.C0;a=identity()
        a[:,0,0]=a[:,1,1]=np.cos(theta);a[:,0,1]=1j*f.Z0*np.sin(theta);a[:,1,0]=1j*np.sin(theta)/f.Z0
        return a
    total=identity()
    for k,c in enumerate(d['capacitance_f']):
        if finite:total=total@line(plane_mm-12.-f.GAP_MM/2 if k==0 else 4.-f.GAP_MM/2)
        total=total@series(c)
        if k<3:
            if finite:total=total@line(4.-f.GAP_MM/2)
            total=total@shunt(d['stub_delay_s'][k])
    if finite:total=total@line(plane_mm-12.-f.GAP_MM/2)
    a,b,c,d=(total[:,j,k] for j,k in ((0,0),(0,1),(1,0),(1,1)))
    denominator=a+b/f.Z0+c*f.Z0+d;s=np.empty_like(total)
    s[:,0,0]=(a+b/f.Z0-c*f.Z0-d)/denominator;s[:,1,1]=(-a+b/f.Z0-c*f.Z0+d)/denominator
    s[:,1,0]=2/denominator;s[:,0,1]=2*(a*d-b*c)/denominator
    return s



class CohortIdentityTests(unittest.TestCase):
    """Study admission must bind replayed data to the requested cohort."""

    def test_replayed_identity_and_duplicate_mesh_admission(self):
        from unittest.mock import patch
        from contextlib import ExitStack
        fields = ['n', 'kind', 'control']
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

class ShuntResonatorTests(unittest.TestCase):
    def test_original_and_finite_circuits_match_independent_cascade(self):
        for finite in (False,True):
            value=f.reference(finite=finite)
            np.testing.assert_allclose(value,abcd_reference(finite=finite),atol=3e-11,rtol=0)
            np.testing.assert_allclose(value@value.conj().transpose(0,2,1),np.broadcast_to(np.eye(2),value.shape),atol=4e-11,rtol=0)
            np.testing.assert_allclose(value,value.transpose(0,2,1),atol=4e-11,rtol=0)

    def test_references_keep_prototype_approximation_and_connections_separate(self):
        k=int(np.argmin(abs(f.FREQUENCIES-3e9)))
        self.assertAlmostEqual(-20*np.log10(abs(f.reference(finite=False)[k,1,0])),31.371724311615125,places=8)
        self.assertAlmostEqual(-20*np.log10(abs(f.reference()[k,1,0])),51.5593353955368,places=8)
        self.assertAlmostEqual(f.prototype_loss()[k],36.26418425071953,places=8)
        self.assertGreater(abs(f.reference()-f.reference(finite=False)).max(),.5)

    def test_synthesis_is_positive_mirrored_and_keeps_linear_stub_correction(self):
        d=f.synthesis();c=np.array(d['capacitance_f']);theta=np.array(d['stub_theta_rad'])
        self.assertTrue(np.all(c>0));np.testing.assert_allclose(c,c[::-1],rtol=1e-14,atol=0)
        np.testing.assert_allclose(theta,np.pi/2-2*np.pi*f.F0*f.Z0*(c[:-1]+c[1:]),atol=2e-15,rtol=0)
        self.assertTrue(np.all((theta>0)&(theta<np.pi/2)))
        self.assertAlmostEqual(f.ETA0/(2*np.pi)*np.log(f.RO/f.RI),f.Z0,places=11)

    def test_through_and_single_element_controls_have_independent_references(self):
        target=f.reference(kind='through')
        np.testing.assert_allclose(target[:,1,0],np.exp(-2j*np.pi*f.FREQUENCIES*.056/f.C0),atol=2e-14,rtol=0)
        np.testing.assert_allclose(target[:,0,0],0.,atol=2e-14)
        for kind in ('capacitor','stub'):
            value=f.reference(kind=kind)
            np.testing.assert_allclose(value@value.conj().transpose(0,2,1),np.broadcast_to(np.eye(2),value.shape),atol=3e-12,rtol=0)
            self.assertGreater(abs(value-target).max(),.1)

    def test_probe_mesh_encloses_inner_current_and_excludes_return_current(self):
        for n in f.MESHES:
            sim,m=f.build(n,1,kind='through')
            self.assertEqual(m['grid'],[len(sim.mesh.GetLines(a)) for a in 'xyz'])
            self.assertEqual(m['native_cells'],int(np.prod(m['grid'])))
            for port in m['native_probe_geometry']:
                self.assertEqual(len(port['native_u_names']),3);self.assertEqual(len(port['native_i_names']),2)
                for bounds in port['current_loop_bounds_mm'].values():
                    for low,high in bounds:self.assertLess(low,-f.RI);self.assertGreater(high,f.RI)
                    self.assertLess(max(np.hypot(a,b) for a in bounds[0] for b in bounds[1]),f.RO)
            with self.assertRaises(ValueError):sim.to_bundle()

    def test_native_inputs_have_only_declared_capacitors_and_two_tem_ports(self):
        for kind,count,stubs in (('through',0,0),('capacitor',1,0),('stub',0,1),('filter',4,3)):
            sim,m=f.build(0,1,kind=kind)
            self.assertEqual(len(m['elements']),count)
            self.assertEqual([p['excite'] for p in m['ports']],[True,False])
            self.assertEqual([p['type'] for p in m['ports']],['coaxial','coaxial'])
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'input.xml';sim.fdtd.Write2XML(str(path));root=ET.parse(path).getroot()
            native=root.findall('.//LumpedElement');self.assertEqual(len(native),count)
            self.assertTrue(all(v.get('L') is None and v.get('R') is None and float(v.get('C'))>0 for v in native))
            for actual,element in zip(native,m['elements']):
                self.assertLess(abs(float(actual.get('C'))/element['value_f']-1),5e-7)
                self.assertGreater(element['value_f']/element['minimum_native_c_f'],1.8)
            shorts=sim.csx.GetPropertiesByName('branch_shorts')
            self.assertEqual(len(shorts[0].GetAllPrimitives()) if shorts else 0,stubs)

    def test_model_geometry_and_reference_distance_controls_are_explicit(self):
        _,base=f.build(0,1,kind='filter')
        for control in ('source','plane'):
            _,other=f.build(0,1,kind='filter',control=control)
            for key in ('dimensions','mesh_mm','elements','declared_dt_s','cap_s'):self.assertEqual(base[key],other[key])
        from fairbeam.network import shift_reference_planes
        # A 24 mm plane lies 4 mm closer on both sides than the 28 mm plane.
        shifted=shift_reference_planes(f.reference(plane_mm=24.),2j*np.pi*f.FREQUENCIES/f.C0,[-.004,-.004])
        np.testing.assert_allclose(shifted,f.reference(),atol=4e-12,rtol=0)

    def test_invalid_values_do_not_create_a_model(self):
        for n in (-1,True,3,1.5):
            with self.assertRaises(ValueError):f.build(n,1)
        for p in (0,True,3):
            with self.assertRaises(ValueError):f.build(0,p)
        for freq in ([np.nan],[0.],np.array([2e9+1j])):
            with self.assertRaises(ValueError):f.reference(freq)

    def test_full_wave_inversion_retains_cross_incident_waves(self):
        target=f.reference(kind='through');a=np.broadcast_to(np.array([[1.,.2j],[.1,.9]]),target.shape);b=target@a
        v=(np.sqrt(f.Z0)*(a+b)).transpose(2,1,0);i=((a-b)/np.sqrt(f.Z0)).transpose(2,1,0)
        np.testing.assert_allclose(f.scattering(v,i),target,atol=3e-13,rtol=0)
        with self.assertRaises(ValueError):f.scattering(v[[0,0]],i[[0,0]])

    def test_frozen_native_plan_survives_json_without_type_changes(self):
        for kind in f.KINDS:
            for port in (1,2):
                _,meta=f.build(0,port,kind=kind,cap_s=.5e-9)
                self.assertEqual(meta,json.loads(json.dumps(meta,allow_nan=False)))

    def test_current_probe_prediction_uses_native_dual_nodes(self):
        # The rc3 finest pilot puts the lower contour at dual indices 9/87.
        # Nearest-primal-minus-one incorrectly predicts 8/86 on this mesh.
        _,meta=f.build(2,1,kind='through',cap_s=.5e-9)
        row=meta['native_probe_geometry'][0]
        self.assertEqual(row['expected_indices']['port_it_1A'],[[77,9,87],[77,18,96]])

    def test_fast_controls_do_not_stop_before_the_local_tail_window_is_quiet(self):
        for kind in f.KINDS:
            expected=-100. if kind=='filter' else -115.
            self.assertEqual(f.build(0,1,kind=kind)[1]['end_db'],expected)
            self.assertEqual(f.build(0,1,kind=kind,control='time')[1]['end_db'],expected-10)
        self.assertEqual(f.LIMITS['terminal_tail_db'],-70.)

    def test_inclusive_dual_area_rejects_the_old_small_capacitor_box(self):
        mesh=dict(x=np.array([-2.,-1.,-.5,0.,.5,1.,2.]),
                  y=np.array([-2.,-1.,0.,1.,2.]),z=np.array([-2.,-1.,0.,1.,2.]))
        # Inclusive transverse nodes cover a 3 mm by 3 mm dual area.
        small=f.minimum_box_capacitance(mesh,[-.5,-1.,-1.],[.5,1.,1.])
        wide=f.minimum_box_capacitance(mesh,[-1.,-1.,-1.],[1.,1.,1.])
        self.assertAlmostEqual(small,9e-3*f.EPS0,delta=1e-26)
        self.assertAlmostEqual(wide,small/2,delta=1e-26)
        c=f.synthesis()['capacitance_f'][1]
        self.assertLess(c,small);self.assertGreater(c,wide)

    def test_used_primitive_cannot_hide_a_native_capacitance_warning_before_fields(self):
        warning='Operator::Calc_LumpedElements(): Warning: Lumped Element capacity is too small for its size! skipping. @ Property: C'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'port1';body=b'<input/>'
            source={'fixture':'frozen'};runtime={'engine':'frozen'}
            meta=dict(excited_port=1,required_properties=['C'],declared_dt_s=.1,grid=[4,4,4])
            sample=root/'input-sample';sample.write_bytes(body);digest=f.sha(sample)
            f.save(root/'plan.json',dict(source=source,runtime=runtime,
                ports=[dict(**meta,input_sha256=digest,operator_input_sha256=digest)]))
            main,setup=Mock(),Mock()
            main.fdtd.Write2XML.side_effect=lambda path:Path(path).write_bytes(body)
            setup.fdtd.Write2XML.side_effect=lambda path:Path(path).write_bytes(body)
            def operator_run(*args,**kwargs):
                (root/'port1.log').write_text(warning+'\n')
                return dict(timestep_s=1.,grid=[4,4,4])
            setup.run.side_effect=operator_run
            with patch.object(f,'build',side_effect=[(main,dict(meta)),(setup,dict(meta))]),\
                    patch.object(f,'identity',return_value=source),patch.object(f,'runtime_identity',return_value=runtime),\
                    patch.object(f,'usage',return_value={'C':[True]}):
                with self.assertRaisesRegex(ValueError,'native lumped model warning'):f.capture(out,0,1)
            main.run.assert_not_called()
            self.assertFalse((out/'raw').exists())
            self.assertNotIn(f.MARKER,(root/'port1.log').read_text())

    def test_stored_native_header_cannot_admit_skipped_lumped_elements(self):
        text='Operator::Calc_LumpedElements(): Warning: Lumped Element capacity is too small for its size! skipping.\n'+f.MARKER
        with self.assertRaisesRegex(ValueError,'native lumped model warning'):f.audit_header(text,{})

    def test_actual_electric_and_half_step_magnetic_clocks_are_required(self):
        dt=1e-13;stride=int(1/(2*f.FREQUENCIES[-1]*dt))//f.OVERSAMPLING
        m=dict(native=dict(dt_s=dt,numerical_time_s=3*stride*dt))
        e=np.column_stack((np.arange(4)*stride*dt,np.ones(4)));h=e+np.array([dt/2,0.])
        f.trace_clock(e,m);f.trace_clock(h,m,True)
        for bad in (e[:-1],h,np.column_stack((e[:,0],[1.,np.nan,1.,1.]))):
            with self.assertRaises(ValueError):f.trace_clock(bad,m)

    def test_cap_exception_cannot_admit_an_incomplete_source_or_tail(self):
        m=dict(native=dict(timesteps=10,numerical_time_s=1.),max_timesteps=10,cap_s=1.)
        q=dict(source=True,clock=True,tail=True,stopping=False)
        self.assertFalse(f.admissible(m,q));self.assertTrue(f.admissible(m,q,True))
        for key in ('source','clock','tail'):
            with self.subTest(key=key):self.assertFalse(f.admissible(m,dict(q,**{key:False}),True))

    def test_forecast_refuses_an_overlong_run_before_output_or_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group') as start:
            out=Path(tmp)/'absent'
            with self.assertRaises(ValueError):acquire(out,rate=.00001)
            self.assertFalse(out.exists());start.assert_not_called()

    def test_pilot_and_short_diagnostic_windows_cannot_bypass_source_admission(self):
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group') as start:
            for value in (0.,np.nan,1e-9):
                with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',pilot_s=value)
            for value in (0.,np.nan,.5e-9,121e-9):
                with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',window_s=value,allow_cap=True)
            with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',window_s=12e-9)
            start.assert_not_called()

    def test_failed_worker_preserves_its_receipt_and_withholds_source_two(self):
        proc=Mock(pid=123,win_job=None);proc.poll.return_value=2
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group',return_value=proc),\
                patch(__name__+'.wait_owned',return_value=2),patch(__name__+'.release_group'):
            out=Path(tmp)/'failed'
            with self.assertRaises(RuntimeError):acquire(out,kind='through',rate=1000.)
            self.assertFalse((out/'port2.worker.json').exists())
            row=json.loads((out/'port1.worker.json').read_text());self.assertEqual(row['exit_code'],2)
            self.assertIsNone(json.loads((out/'progress.json').read_text())['active_launcher_pid'])

    def test_nonfinite_energy_stops_only_the_owned_worker(self):
        proc=Mock(pid=123,win_job='owned');proc.poll.return_value=None;proc.wait.return_value=9
        def launch(command,**kwargs):
            kwargs['stdout'].write(f'{f.MARKER}\nEnergy: ~-nan(ind) (- 0.00dB)\n');kwargs['stdout'].flush();return proc
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group',side_effect=launch),\
                patch(__name__+'.terminate_group') as stop,patch(__name__+'.release_group'):
            out=Path(tmp)/'failed'
            with self.assertRaisesRegex(ValueError,'non-finite native'):acquire(out,kind='through',rate=1000.)
            stop.assert_called_once_with(123,grace=0,job='owned')
            self.assertFalse((out/'port2.worker.json').exists())

    def test_missing_and_rejected_studies_cannot_qualify_the_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=study(tmp);self.assertEqual(len(result['missing']),17);self.assertFalse(result['qualified'])
            folder=Path(tmp)/'n0_through';folder.mkdir();f.save(folder/'progress.json',dict(status='rejected',reason='tail'))
            result=study(tmp);self.assertEqual(len(result['missing']),16)
            self.assertEqual(result['rejected'],[dict(name='n0_through',reason='tail')]);self.assertFalse(result['qualified'])


def main():
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True)
    for name in ('preflight','fdtd','compare','study','worker'):mode.add_argument('--'+name,action='store_true')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--mesh',type=int,choices=f.MESHES,default=0)
    p.add_argument('--kind',choices=f.KINDS,default='filter');p.add_argument('--control',choices=f.CONTROLS,default='ordinary')
    p.add_argument('--port',type=int,choices=(1,2),default=1);p.add_argument('--rate-mcps',type=float,default=60.)
    p.add_argument('--pilot-seconds',type=float);p.add_argument('--window-seconds',type=float)
    p.add_argument('--allow-capped-diagnostic',action='store_true');args=p.parse_args()
    if args.allow_capped_diagnostic and not (args.fdtd or args.compare):p.error('capped diagnostics are separate from strict study')
    if args.window_seconds is not None and (args.pilot_seconds is not None or not (args.fdtd or args.worker)
        or not np.isfinite(args.window_seconds)
        or not f.excitation.dgauss_duration_s(f.FREQUENCIES[-1])<=args.window_seconds<=f.CAP_S):
        p.error('diagnostic window must contain source, fit ordinary cap, and be separate from pilot')
    if args.worker:
        cap=args.pilot_seconds if args.pilot_seconds is not None else args.window_seconds
        f.capture(args.out,args.mesh,args.port,args.kind,args.control,cap);return
    if args.preflight:result=preflight(args.out,args.rate_mcps)
    elif args.fdtd:result=acquire(args.out,args.mesh,args.kind,args.control,args.rate_mcps,
        args.pilot_seconds,args.allow_capped_diagnostic,args.window_seconds)
    elif args.compare:result=f.matrix(args.out,args.allow_capped_diagnostic)[1]
    else:result=study(args.out)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__':
    if any('--'+name in sys.argv for name in ('preflight','fdtd','compare','study','worker')):main()
    else:unittest.main()
