"""Opt-in effective-TEM capacitor-coupled bandpass comparison, Example 8.9.

From python/ in the bundled runtime:
python -m unittest tests.test_cap_coupled_bandpass_fixture -q
python -m tests.test_cap_coupled_bandpass_fixture --preflight --out C:/study/bandpass-plan
"""
import argparse
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
from tests.test_coax_resonator import wait_owned
from tests import cap_coupled_bandpass_fixture as f

def configurations(include_diagnostics=False):
    cases=[(f'o{o}_n{n}_{kind}_pec',n,kind,'pec','ordinary',o)
        for o in f.ORDERS for n in f.MESHES for kind in f.KINDS]
    cases += [(f'o{o}_n2_filter_pec_{control}',2,'filter','pec',control,o)
        for o in f.ORDERS for control in f.STUDY_CONTROLS]
    if include_diagnostics:
        cases += [(f'o{o}_n0_element_pec_unmapped',0,'element','pec','unmapped',o) for o in f.ORDERS]
    return cases


def forecast(meta, rate):
    if not np.isfinite(rate) or rate <= 0: raise ValueError('positive conservative rate required')
    return 1.2*meta['native_cells']*meta['max_timesteps']/(rate*1e6)+45.


def freeze(out, n, kind, loss, control, cap_s=None, order=3):
    planned = []
    for port in (1, 2):
        holder = f.build(n, port, kind, loss, control, cap_s, order=order); sim, meta = holder[0], holder[2]
        path = out/f'planned-p{port}.xml'; sim.fdtd.Write2XML(str(path))
        sim.fdtd.SetTimeStep(0.); sim.fdtd.SetNumberOfTimeSteps(1024)
        operator = out/f'planned-p{port}-operator.xml'; sim.fdtd.Write2XML(str(operator))
        planned.append(dict(**meta, input_sha256=f.sha(path), operator_input_sha256=f.sha(operator)))
    plan = dict(source=f.identity(), runtime=f.runtime_identity(), ports=planned)
    f.save(out/'plan.json', plan)
    return plan


def preflight(out, rate):
    out = Path(out); out.mkdir(parents=True, exist_ok=False); rows = []
    for name, n, kind, loss, control, order in configurations(include_diagnostics=True):
        folder = out/name; folder.mkdir()
        plan = freeze(folder, n, kind, loss, control, order=order)
        for meta in plan['ports']:
            seconds = forecast(meta, rate)
            rows.append(dict(name=name, **meta, forecast_seconds=seconds, deferred=seconds > 1800.))
    result = dict(rows=rows, source=f.identity(), runtime=f.runtime_identity(),
        rate_mcps=rate, limits=f.LIMITS, dimensions={o:f.synthesis(o) for o in f.ORDERS}, native_propagations=0, qualified=False)
    f.save(out/'preflight.json', result)
    return result


def acquire(out, n=1, kind='filter', loss='pec', control='ordinary', rate=15., pilot_s=None, allow_cap=False, window_s=None, order=3):
    out = Path(out).resolve()
    if out.exists(): raise ValueError('fresh output directory required')
    if pilot_s is not None and (not np.isfinite(pilot_s) or not 0 < pilot_s <= .5e-9):
        raise ValueError('performance pilot must be in 0..0.5 ns')
    if window_s is not None and (pilot_s is not None or not allow_cap
            or not np.isfinite(window_s) or not f.excitation.dgauss_duration_s(f.FREQUENCIES[-1]) <= window_s <= f.CAP_S):
        raise ValueError('explicit capped diagnostic window must contain the source and not exceed the ordinary window')
    cap = pilot_s if pilot_s is not None else window_s
    holder = f.build(n, 1, kind, loss, control, cap, order=order); meta = holder[2]
    estimate = forecast(meta, rate)
    if estimate > 1800.: raise ValueError('forecast exceeds 30 minutes; defer this cohort')
    out.mkdir(parents=True, exist_ok=False); plan = freeze(out, n, kind, loss, control, cap, order=order)
    state = dict(started_utc=datetime.now(timezone.utc).isoformat(), status='prepared',
        source=plan['source'], runtime=plan['runtime'], active_launcher_pid=None, qualified=False,
        order=order, pilot=pilot_s is not None, diagnostic_window_s=window_s, capped_diagnostic_requested=allow_cap,
        driver_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=f.ROOT, text=True).strip(),
        forecast_seconds_per_source=estimate, rate_mcps=rate)
    progress = out/'progress.json'; f.save(progress, state)
    try:
        for port in ((1,) if pilot_s is not None else (1, 2)):
            if f.identity() != state['source'] or f.runtime_identity() != state['runtime']:
                raise ValueError('frozen source/runtime changed')
            command = [sys.executable, '-m', 'tests.test_cap_coupled_bandpass_fixture', '--worker',
                '--mesh', str(n), '--kind', kind, '--loss', loss, '--control', control,
                '--port', str(port), '--order', str(order), '--out', str(out/f'port{port}')]
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
    root=Path(root);missing=[];rejected=[];results={};rows=[]
    for name,n,kind,loss,control,order in configurations():
        folder=root/name
        if not all((folder/f'port{p}/report.json').exists() for p in (1,2)):
            progress=folder/'progress.json'
            state=json.loads(progress.read_text()) if progress.exists() else {}
            if state.get('status')=='rejected':rejected.append(dict(name=name,reason=state.get('reason')))
            else:missing.append(name)
            continue
        try:actual,row=f.matrix(folder)
        except (ValueError,AssertionError,FileNotFoundError) as error:
            rejected.append(dict(name=name,reason=repr(error)));continue
        results[name]=actual;rows.append(row)
    by_order={}
    for order in f.ORDERS:
        prefix=f'o{order}_';mesh=[];controls=[]
        for kind in f.KINDS:
            for a,b in zip(f.MESHES[:-1],f.MESHES[1:]):
                first,second=f'{prefix}n{a}_{kind}_pec',f'{prefix}n{b}_{kind}_pec'
                if first not in results or second not in results:continue
                change=f.difference(results[second],results[first])
                mesh.append(dict(kind=kind,from_mesh=a,to_mesh=b,**change,
                    passes=change['s_abs']<=f.LIMITS['mesh_s_abs'] and change['phase_deg']<=f.LIMITS['mesh_phase_deg']))
        base=prefix+'n2_filter_pec'
        for control in f.STUDY_CONTROLS:
            other=base+'_'+control
            if base not in results or other not in results:continue
            value=align_plane(results[other],'pec') if control=='plane' else results[other]
            change=f.difference(value,results[base])
            controls.append(dict(control=control,**change,
                passes=change['s_abs']<=f.LIMITS['control_s_abs'] and change['phase_deg']<=f.LIMITS['control_phase_deg']))
        own=[row for row in rows if row['order']==order]
        absent=[v for v in missing if v.startswith(prefix)]
        bad=[v for v in rejected if v['name'].startswith(prefix)]
        numerical=bool(not absent and not bad and len(own)==18 and len(mesh)==9 and len(controls)==6
            and all(row['passes'] for row in mesh+controls)
            and all(row['native_complete'] and row['measurement_passes'] and row['point_target_passes'] for row in own))
        application=[row for row in own if row['kind']=='filter']
        specification=bool(application and all(row['requested_spec_passes'] and row['reference_spec_passes'] for row in application))
        by_order[str(order)]=dict(numerical_accuracy_qualified=numerical,requested_spec_passes=specification,
            qualified=numerical and specification,missing=absent,rejected=bad,mesh_changes=mesh,controls=controls)
    return dict(qualified=by_order['3']['qualified'],book_example_validated=by_order['3']['qualified'],
        own_order4_design_qualified=by_order['4']['qualified'],by_order=by_order,missing=missing,rejected=rejected,rows=rows,
        scope='Declared effective-TEM circuit dual only; book N3 remains unchanged and separate from own N4 control.')


def abcd_reference(freq,plane_mm=12.,order=3,kind='filter'):
    d=f.synthesis(order);freq=np.asarray(freq,float);w=2*np.pi*freq
    def series(cap):
        m=np.broadcast_to(np.eye(2,dtype=complex),(len(freq),2,2)).copy();m[:,0,1]=1/(1j*w*cap);return m
    def line(delay):
        theta=w*delay;m=np.empty((len(freq),2,2),complex)
        m[:,0,0]=m[:,1,1]=np.cos(theta);m[:,0,1]=1j*f.Z0*np.sin(theta);m[:,1,0]=1j*np.sin(theta)/f.Z0;return m
    total=np.broadcast_to(np.eye(2,dtype=complex),(len(freq),2,2)).copy()
    if kind=='through':total=line(sum(d['line_delay_s']))
    elif kind=='element':total=series(d['capacitance_f'][0])@line(sum(d['line_delay_s']))
    else:
        for k,cap in enumerate(d['capacitance_f']):
            total=total@series(cap)
            if k<order:total=total@line(d['line_delay_s'][k])
    a,b,c,d=(total[:,j,k] for j,k in ((0,0),(0,1),(1,0),(1,1)))
    den=a+b/f.Z0+c*f.Z0+d;s=np.empty_like(total)
    s[:,0,0],s[:,1,1]=(a+b/f.Z0-c*f.Z0-d)/den,(-a+b/f.Z0-c*f.Z0+d)/den
    s[:,1,0],s[:,0,1]=2/den,2*(a*d-b*c)/den
    return s*np.exp(-4j*np.pi*freq*plane_mm*1e-3/f.C0)[:,None,None]


class CapCoupledTests(unittest.TestCase):
    def test_nodal_circuit_matches_independent_cascade(self):
        for order in f.ORDERS:
            for kind in f.KINDS:
                np.testing.assert_allclose(f.reference(order=order,kind=kind),
                    abcd_reference(f.FREQUENCIES,order=order,kind=kind),atol=3e-12,rtol=0)

    def test_dual_port_signs_recover_the_series_capacitor_network(self):
        for order in f.ORDERS:
            for kind in f.KINDS:
                np.testing.assert_allclose(f.original_from_dual(f.reference(order=order,kind=kind,dual=True)),
                    f.reference(order=order,kind=kind),atol=3e-12,rtol=0)

    def test_through_reference_has_contiguous_nodes_and_fixed_air_delay(self):
        for order in f.ORDERS:
            delay=sum(f.synthesis(order)['line_delay_s'])+.024/f.C0
            target=f.reference(kind='through',order=order)
            np.testing.assert_allclose(target[:,1,0],np.exp(-2j*np.pi*f.FREQUENCIES*delay),atol=3e-14,rtol=0)
            np.testing.assert_allclose(target[:,(0,1),(0,1)],0.,atol=2e-14)

    def test_original_order_is_not_silently_changed_to_satisfy_the_spec(self):
        frequency=np.array([2.2e9])
        n3=float(-20*np.log10(abs(f.reference(frequency,order=3)[0,1,0])))
        n4=float(-20*np.log10(abs(f.reference(frequency,order=4)[0,1,0])))
        self.assertAlmostEqual(n3,15.3352520569,places=8)
        self.assertAlmostEqual(n4,25.5956744635,places=8)
        self.assertLess(n3,20.);self.assertGreater(n4,20.)
        self.assertLess(f.prototype_loss(frequency,3)[0],20.)
        self.assertEqual(f.synthesis()['order'],3)

    def test_reciprocity_and_lossless_power_are_independent_of_order(self):
        for order in f.ORDERS:
            s=f.reference(order=order)
            np.testing.assert_allclose(s,s.transpose(0,2,1),atol=3e-12)
            np.testing.assert_allclose(s.conj().transpose(0,2,1)@s,np.broadcast_to(np.eye(2),s.shape),atol=3e-12)

    def test_lengths_and_element_values_follow_the_declared_transform(self):
        for order in f.ORDERS:
            d=f.synthesis(order)
            np.testing.assert_allclose(np.asarray(d['line_length_mm'])*1e-3,
                f.C0*np.asarray(d['theta_rad'])/(2*np.pi*f.F0),atol=2e-17,rtol=0)
            np.testing.assert_allclose(d['dual_inductance_h'],f.Z0**2*np.asarray(d['capacitance_f']),rtol=1e-15)
            np.testing.assert_allclose(d['capacitance_f'],d['capacitance_f'][::-1],rtol=2e-15)

    def test_invalid_model_inputs_fail_before_native_propagation(self):
        for n in (True,-1,4,1.):
            with self.assertRaises(ValueError):f.build(n,1)
        for order in (True,2,5,3.):
            with self.assertRaises(ValueError):f.synthesis(order)
        for port in (True,0,3):
            with self.assertRaises(ValueError):f.build(0,port)
        for frequency in ([],[0],[4e9],[np.nan],[2e9+1j]):
            with self.assertRaises(ValueError):f.reference(np.array(frequency))
        with self.assertRaises(ValueError):f.original_from_dual(np.zeros((2,3,3)))

    def test_mesh_refines_without_moving_the_physical_boundaries(self):
        for order in f.ORDERS:
            bounds=[];counts=[]
            for n in f.MESHES:
                x,y,z=f.mesh_lines(n,order=order);bounds.append([x[8],x[-9],y[0],y[-1],z[0],z[-1]])
                counts.append(np.prod([len(a) for a in (x,y,z)]))
                h=np.diff(x);self.assertLessEqual(max(np.max(h[1:]/h[:-1]),np.max(h[:-1]/h[1:])),2+1e-10)
            np.testing.assert_allclose(bounds,np.broadcast_to(bounds[0],(4,6)),atol=1e-10,rtol=0)
            self.assertTrue(np.all(np.diff(counts)>0))

    def test_lumped_mapping_is_geometric_and_xml_matches_the_record(self):
        for order in f.ORDERS:
            for kind,count in (('through',0),('element',1),('filter',order+1)):
                sim,ports,m=f.build(0,1,kind=kind,order=order)
                self.assertEqual(len(ports),2);self.assertEqual(len(m['elements']),count)
                self.assertEqual(m['cell_span_factor'],m['physical_y_cells']/m['native_y_nodes'])
                with tempfile.TemporaryDirectory() as tmp:
                    path=Path(tmp)/'input.xml';sim.fdtd.Write2XML(str(path));root=ET.parse(path).getroot()
                native={v.get('Name'):v for v in root.findall('.//LumpedElement')}
                for el in m['elements']:
                    # The native XML writer rounds scalar element values.
                    self.assertLess(abs(float(native[el['name']].get('L'))/el['native_l_h']-1),5e-7)
                    self.assertAlmostEqual(el['native_l_h']/el['physical_l_h'],m['cell_span_factor'])
                    self.assertEqual(float(native[el['name']].get('LEtype')),0.)
                for feed in m['feeds']:
                    self.assertTrue(np.any(np.asarray(m['mesh_mm']['x'])==feed['start'][0]))
                    self.assertEqual(feed['start'][0],feed['stop'][0])
                with self.assertRaises(ValueError):sim.to_bundle()
        ordinary=f.build(0,1,kind='element')[2];unmapped=f.build(0,1,kind='element',control='unmapped')[2]
        self.assertEqual(ordinary['dimensions'],unmapped['dimensions'])
        self.assertEqual(unmapped['elements'][0]['native_l_h'],unmapped['elements'][0]['physical_l_h'])

    def test_probe_contours_preserve_the_full_width_tem_current(self):
        for n in f.MESHES:
            _,_,m=f.build(n,1);x,y,z=(np.asarray(m['mesh_mm'][axis]) for axis in 'xyz')
            for c in m['current_contours'].values():
                self.assertAlmostEqual(c['physical_span_mm']*c['weight'],f.WIDTH_MM)
            for name,(a,b) in m['expected_voltage_indices'].items():
                self.assertEqual(a[:2],b[:2]);self.assertEqual(z[a[2]],0.)
                self.assertAlmostEqual(z[b[2]],f.HEIGHT_MM)
                self.assertAlmostEqual(y[a[1]],f.WIDTH_MM/2)
            self.assertNotEqual(m['expected_current_indices']['i0_0_0'],m['expected_current_indices']['i0_1_0'])

    def test_source_plane_and_transverse_controls_are_explicit(self):
        a=f.build(0,1)[2]
        for control in ('source','plane'):
            b=f.build(0,1,control=control)[2]
            for key in ('dimensions','mesh_mm','declared_dt_s','cap_s','elements'):
                self.assertEqual(a[key],b[key])
        self.assertEqual(f.build(0,1,control='source')[2]['source_distance_mm'],40.)
        self.assertEqual(f.build(0,1,control='plane')[2]['plane_mm'],18.)
        b=f.build(0,1,control='yspan')[2]
        self.assertEqual(b['physical_y_cells'],2*a['physical_y_cells'])
        self.assertEqual(a['dimensions'],b['dimensions'])
        np.testing.assert_allclose(align_plane(f.reference(plane_mm=18.),'pec'),f.reference(),atol=2e-13,rtol=0)

    def test_full_wave_matrix_keeps_cross_incident_waves(self):
        target=f.reference(dual=True);a=np.broadcast_to(np.array([[1.,.2j],[.1,.9]]),target.shape);b=target@a
        v=(np.sqrt(f.Z0)*(a+b)).transpose(2,1,0);i=((a-b)/np.sqrt(f.Z0)).transpose(2,1,0)
        np.testing.assert_allclose(f.original_from_dual(f.scattering(v,i)),f.reference(),atol=3e-13)
        with self.assertRaises(ValueError):f.scattering(v[[0,0]],i[[0,0]])

    def test_staggered_dual_currents_and_contour_checks(self):
        target=f.reference(dual=True);a=np.broadcast_to(np.eye(2),target.shape)
        v=(np.sqrt(f.Z0)*(a+target)).transpose(2,1,0)
        i=((a-target)/np.sqrt(f.Z0)).transpose(2,1,0);phase=.04
        vv=np.stack((v*np.exp(1j*phase),v,v*np.exp(-1j*phase)),axis=2)
        ii=np.broadcast_to(i[:,:,None,None,:]*np.cos(phase/2),(2,2,2,2,len(f.FREQUENCIES))).copy()
        actual,qa=f.extract(vv,ii)
        np.testing.assert_allclose(f.original_from_dual(actual),f.reference(),atol=3e-13)
        self.assertLess(qa['contour_s_abs'],1e-13)
        ii[:,:,1]*=1.03;self.assertGreater(f.extract(vv,ii)[1]['contour_s_abs'],f.LIMITS['contour_s_abs'])

    def test_perfect_capped_data_cannot_validate_the_book_example(self):
        target=f.reference(dual=True);a=np.broadcast_to(np.eye(2),target.shape)
        v=(np.sqrt(f.Z0)*(a+target)).transpose(2,1,0)
        i=((a-target)/np.sqrt(f.Z0)).transpose(2,1,0);phase=.04
        vv=np.stack((v*np.exp(1j*phase),v,v*np.exp(-1j*phase)),axis=2)
        ii=np.broadcast_to(i[:,:,None,None,:]*np.cos(phase/2),(2,2,2,2,len(f.FREQUENCIES)))
        def source(path,allow_cap):
            self.assertTrue(allow_cap);port=int(path.name[-1])
            return dict(n=0,order=3,kind='filter',loss='pec',control='ordinary',cap_s=8e-9,
                mesh_mm={},plane_mm=12.,excited_port=port),dict(v=vv[port-1],i=ii[port-1]),dict(source=True,stopping=False)
        with tempfile.TemporaryDirectory() as tmp,patch.object(f,'read',side_effect=source):
            _,row=f.matrix(tmp,True)
            self.assertTrue(row['point_target_passes']);self.assertTrue(row['measurement_passes'])
            self.assertFalse(row['reference_spec_passes']);self.assertFalse(row['requested_spec_passes'])
            self.assertFalse(row['native_complete']);self.assertFalse(row['qualified'])

    def test_cap_exception_does_not_repair_a_bad_tail(self):
        m=dict(native=dict(timesteps=10,numerical_time_s=1.),max_timesteps=10,cap_s=1.)
        q=dict(source=True,clock=True,tail=True,stopping=False)
        self.assertFalse(f.admissible(m,q));self.assertTrue(f.admissible(m,q,True))
        self.assertFalse(f.admissible(m,dict(q,tail=False),True))

    def test_complete_electric_and_half_step_magnetic_clocks(self):
        dt=1e-13;stride=int(1/(2*f.FREQUENCIES[-1]*dt))//f.OVERSAMPLING
        m=dict(native=dict(dt_s=dt,numerical_time_s=3*stride*dt))
        e=np.column_stack((np.arange(4)*stride*dt,np.ones(4)));h=e+np.array([dt/2,0.])
        f.trace_clock(e,m);f.trace_clock(h,m,True)
        with self.assertRaises(ValueError):f.trace_clock(h,m)
        with self.assertRaises(ValueError):f.trace_clock(e[:-1],m)

    def test_native_header_accepts_scientific_counts_but_not_wrong_inputs(self):
        for grid in ([200,5,4],[193,122,98]):
            cells=int(np.prod(grid));text=(f'{f.MARKER}\nopenEMS 64bit -- version v0.37.0-rc3\n'
                f'fixed number of threads: 1\nFDTD simulation size: {"x".join(map(str,grid))} --> {cells:.6g} FDTD cells')
            m=dict(grid=grid,native_cells=cells)
            self.assertEqual(f.audit_header(text,m),dict(version='v0.37.0-rc3',threads=1))
            for bad in (text.replace('threads: 1','threads: 4'),text.replace('v0.37.0-rc3','v0.36.0'),text+f.MARKER):
                with self.assertRaises(ValueError):f.audit_header(bad,m)

    def test_forecast_refuses_long_runs_before_output_or_launch(self):
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group') as launch:
            out=Path(tmp)/'absent'
            with self.assertRaises(ValueError):acquire(out,rate=.00001)
            self.assertFalse(out.exists());launch.assert_not_called()

    def test_pilot_and_diagnostic_windows_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group') as launch:
            for value in (0.,np.nan,1e-9):
                with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',pilot_s=value)
            for value in (0.,np.nan,.5e-9,121e-9):
                with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',window_s=value,allow_cap=True)
            with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',window_s=12e-9)
            launch.assert_not_called()

    def test_failed_worker_preserves_order_window_and_receipt(self):
        proc=Mock(pid=123,win_job=None);proc.poll.return_value=2
        with tempfile.TemporaryDirectory() as tmp,patch(__name__+'.popen_group',return_value=proc),\
                patch(__name__+'.wait_owned',return_value=2),patch(__name__+'.release_group'):
            out=Path(tmp)/'failed'
            with self.assertRaises(RuntimeError):acquire(out,n=0,kind='element',rate=1000.,window_s=12e-9,allow_cap=True,order=4)
            state=json.loads((out/'progress.json').read_text());self.assertIsNone(state['active_launcher_pid'])
            receipt=json.loads((out/'port1.worker.json').read_text());command=receipt['command']
            self.assertEqual(receipt['exit_code'],2);self.assertEqual(command[command.index('--order')+1],'4')
            self.assertEqual(float(command[command.index('--window-seconds')+1]),12e-9)
            self.assertTrue(all(v['order']==4 for v in json.loads((out/'plan.json').read_text())['ports']))

    def test_unused_source_fails_operator_admission_before_fields(self):
        _,_,meta=f.build(0,1,kind='through');used={name:[True] for name in meta['required_properties']}
        used[next(name for name in used if 'port_excite_' in name)]=[False]
        operator=dict(timestep_s=meta['declared_dt_s']/.72,grid=meta['grid'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);freeze(root,0,'through','pec','ordinary')
            with patch.object(f.TemBandpassSimulation,'run',return_value=operator) as run,patch.object(f,'usage',return_value=used):
                with self.assertRaisesRegex(ValueError,'operator CFL/material-usage'):f.capture(root/'port1',0,1,kind='through')
                self.assertEqual(run.call_count,1);self.assertTrue(run.call_args.kwargs['setup_only'])
                self.assertFalse((root/'port1/raw').exists())

    def test_missing_or_rejected_cases_cannot_qualify_any_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            row=study(tmp);self.assertEqual(len(row['missing']),36)
            self.assertFalse(row['book_example_validated']);self.assertFalse(row['own_order4_design_qualified'])
            folder=Path(tmp)/'o3_n0_filter_pec';folder.mkdir();f.save(folder/'progress.json',dict(status='rejected',reason='tail'))
            row=study(tmp);self.assertEqual(len(row['missing']),35)
            self.assertEqual(row['rejected'],[dict(name='o3_n0_filter_pec',reason='tail')])


def main():
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True)
    for name in ('preflight','fdtd','compare','study','worker'):mode.add_argument('--'+name,action='store_true')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--mesh',type=int,choices=f.MESHES,default=0)
    p.add_argument('--order',type=int,choices=f.ORDERS,default=3)
    p.add_argument('--kind',choices=f.KINDS,default='filter');p.add_argument('--loss',choices=f.LOSSES,default='pec')
    p.add_argument('--control',choices=f.CONTROLS,default='ordinary');p.add_argument('--port',type=int,choices=(1,2),default=1)
    p.add_argument('--rate-mcps',type=float,default=15.);p.add_argument('--pilot-seconds',type=float)
    p.add_argument('--allow-capped-diagnostic',action='store_true');p.add_argument('--window-seconds',type=float)
    args=p.parse_args()
    if args.allow_capped_diagnostic and not (args.fdtd or args.compare):p.error('capped diagnostics are separate from strict study')
    if args.window_seconds is not None and (args.pilot_seconds is not None or not (args.fdtd or args.worker)
            or not np.isfinite(args.window_seconds)
            or not f.excitation.dgauss_duration_s(f.FREQUENCIES[-1])<=args.window_seconds<=f.CAP_S):
        p.error('diagnostic window must contain source, not exceed the ordinary window, and be separate from a pilot')
    if args.worker:
        cap=args.pilot_seconds if args.pilot_seconds is not None else args.window_seconds
        f.capture(args.out,args.mesh,args.port,args.kind,args.loss,args.control,cap,order=args.order);return
    if args.preflight:result=preflight(args.out,args.rate_mcps)
    elif args.fdtd:result=acquire(args.out,args.mesh,args.kind,args.loss,args.control,args.rate_mcps,
        args.pilot_seconds,args.allow_capped_diagnostic,args.window_seconds,order=args.order)
    elif args.compare:result=f.matrix(args.out,args.allow_capped_diagnostic)[1]
    else:result=study(args.out)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__':
    if any('--'+name in sys.argv for name in ('preflight','fdtd','compare','study','worker')):main()
    else:unittest.main()
