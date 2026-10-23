"""Own static PIN-load checks and explicitly opt-in bounded native driver."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET

import numpy as np
from fairbeam.network import network_s
from fairbeam.procutil import popen_group,release_group,terminate_group
from tests.test_coax_resonator import wait_owned
from tests import pin_switch_fixture as f


def forecast(meta,rate):
    if not np.isfinite(rate) or rate<=0:raise ValueError('positive conservative measured rate required')
    return float(1.2*meta['native_cells']*meta['max_timesteps']/(rate*1e6)+45.)


def freeze(out,n,kind,control,cap=None,impedance_scale=f.SCALE):
    sim,m=f.build(n,kind,control,cap,impedance_scale=impedance_scale)
    sim.fdtd.Write2XML(str(out/'planned.xml'))
    m.update(source=f.identity(),runtime=f.environment.runtime_identity(),input_sha256=f.sha(out/'planned.xml'))
    f.save(out/'plan.json',m);return m


def acquire(out,n,kind,control='ordinary',rate=60.,pilot_s=None,allow_cap=False,impedance_scale=f.SCALE):
    out=Path(out).resolve()
    if out.exists():raise ValueError('fresh acquisition directory required')
    if pilot_s is not None and (not np.isfinite(pilot_s) or not 0<pilot_s<=.5e-9):raise ValueError('pilot in 0..0.5 ns required')
    _,m=f.build(n,kind,control,pilot_s,impedance_scale=impedance_scale);seconds=forecast(m,rate)
    if seconds>1800.:raise ValueError('over 30 minutes/source; defer this acquisition')
    out.mkdir(parents=True,exist_ok=False);m=freeze(out,n,kind,control,pilot_s,impedance_scale)
    state=dict(status='prepared',started_utc=datetime.now(timezone.utc).isoformat(),source=m['source'],runtime=m['runtime'],
        driver_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=f.ROOT,text=True).strip(),
        forecast_seconds=seconds,rate_mcps=rate,pilot=pilot_s is not None,capped_diagnostic_requested=allow_cap,
        active_launcher_pid=None,qualified=False)
    proc=None;start,wall=time.monotonic(),time.time();code=None;failure=None;command=[]
    try:
        command=[sys.executable,'-m','tests.test_pin_switch_fixture','--worker','--mesh',str(n),
            '--kind',kind,'--control',control,'--impedance-scale',str(impedance_scale),'--out',str(out/'capture')]
        if pilot_s is not None:command+=['--pilot-seconds',str(pilot_s)]
        with (out/'worker.log').open('x',encoding='utf-8')as log:
            proc=popen_group(command,cwd=f.ROOT/'python',stdout=log,stderr=subprocess.STDOUT)
            state.update(status='running',active_launcher_pid=proc.pid);f.save(out/'progress.json',state)
            class Watched:
                def __getattr__(self,name):return getattr(proc,name)
                def wait(self,timeout=None):
                    text=(out/'worker.log').read_text(encoding='utf-8',errors='replace')
                    if f.MARKER in text and re.search(r'Energy:\s*~\s*[+-]?(?:nan|inf)\b',text.split(f.MARKER)[-1],re.I):
                        raise ValueError('non-finite native energy')
                    return proc.wait(timeout=timeout)
            try:code=wait_owned(Watched(),seconds=1800.)
            except BaseException as error:failure=repr(error);raise
            finally:
                if proc.poll() is None:terminate_group(proc.pid,grace=0,job=proc.win_job);proc.wait(timeout=15)
                release_group(proc)
                f.save(out/'worker.json',dict(command=command,pid=proc.pid,exit_code=code,failure=failure,
                    wall_seconds=time.time()-wall,monotonic_seconds=time.monotonic()-start,deadline_seconds=1800.,driver_head=state['driver_head']))
        if code:raise RuntimeError('native worker failed; raw prefix retained')
        folder=out/'capture';report=json.loads((folder/'report.json').read_text())
        report.update(native_header=f.audit_header((out/'worker.log').read_text(),report),native_log_sha256=f.sha(out/'worker.log'))
        f.save(folder/'report.json',report);checks=f.quality(report)
        if pilot_s is not None:
            required=('grid','clock','time','operator','geometry','probes','engine','header')
            if not all(checks[k]for k in required) or report['native']['timesteps']!=report['max_timesteps']:
                raise ValueError('invalid performance-only pilot')
            native=report['native'];rate_mcps=native['cells']*native['timesteps']/native['iteration_wall_s']/1e6
            state.update(status='performance_only',rate_measured_mcps=rate_mcps)
            return state
        result=compare(folder,allow_cap)
        state.update(status='complete' if result['native_complete'] else 'complete_with_known_deviation',result=result)
        return result
    except BaseException as error:
        state.update(status='rejected',reason=repr(error));raise
    finally:
        state['active_launcher_pid']=None;f.save(out/'progress.json',state)


def reflection(v,i,zref=f.Z0*f.SCALE):
    a=v+zref*i
    if (not np.isfinite(a).all() or not np.isfinite(v).all() or not np.isfinite(i).all()
        or abs(a).min()<.02*max(abs(a).max(),np.finfo(float).tiny)):
        raise ValueError('insufficient incident spectrum')
    return (v-zref*i)/a


def compare(folder,allow_cap=False):
    m,d,q=f.read(folder,allow_cap);s11=reflection(d['v'],d['i'],m['reference_ohm'])
    error=float(abs(s11-f.reference_reflection(m['kind'],m['impedance_scale'])).max())
    result=dict(n=m['n'],kind=m['kind'],control=m['control'],reflection_error_abs=error,
        point_passes=error<=f.LIMITS['reflection_abs'],native_complete=all(q.values()),native_quality=q,
        qualified=False,fdtd_twoport_switch_validated=False,scope=f.SCOPE)
    if m['kind'] in ('forward','reverse'):
        z=d['v']/d['i']/m['impedance_scale'];target=f.diode_z(state=m['kind'])
        if not np.isfinite(z).all():raise ValueError('finite measured load impedance required')
        result['z_error_relative']=float((abs(z-target)/abs(target)).max())
        result['point_passes'] &= result['z_error_relative']<=f.LIMITS['z_relative']
        result['samples']=[dict(f_hz=float(f.FREQUENCIES[k]),r_ohm=float(z[k].real),x_ohm=float(z[k].imag),
            target_r_ohm=float(target[k].real),target_x_ohm=float(target[k].imag))for k in (0,30,60,90,120)]
        for layout in ('series','shunt'):
            native=f.embed(z,layout);ideal=f.embed(target,layout)
            difference=float(abs(20*np.log10(abs(native[:,1,0]/ideal[:,1,0]))).max())
            result[layout+'_embedding_loss_error_db']=difference
            result['point_passes'] &= difference<=f.LIMITS['attenuation_db']
    path=Path(folder).parent/('cap-diagnostic-comparison.json' if allow_cap else 'comparison.json')
    if path.exists():
        if json.loads(path.read_text())!=result:raise ValueError('stored comparison changed')
    else:f.save(path,result)
    return result


def configurations():
    return [(f'n{n}_{kind}',n,kind,'ordinary')for n in f.MESHES for kind in f.KINDS]+[
        (f'n2_{kind}_{control}',2,kind,control)for kind in ('forward','reverse')for control in f.CONTROLS[1:]]


def study(root):
    root=Path(root);missing=[];rejected=[];rows=[];data={};changes=[]
    for name,n,kind,control in configurations():
        state=root/name/'progress.json'
        if not state.exists():missing.append(name);continue
        progress=json.loads(state.read_text())
        if progress['status']!='complete':rejected.append(dict(name=name,status=progress['status'],reason=progress.get('reason')));continue
        try:
            row=compare(root/name/'capture');m,d,_=f.read(root/name/'capture')
            expected = dict(n=n, kind=kind, control=control)
            if any(m.get(key) != value for key, value in expected.items()):
                raise ValueError(f'cohort identity mismatch for {name}: expected {expected}')
            if m['impedance_scale']!=f.SCALE:raise ValueError('strict study requires its single declared impedance scale')
        except (ValueError,AssertionError,FileNotFoundError)as error:
            rejected.append(dict(name=name,reason=repr(error)));continue
        rows.append(dict(name=name,**row));data[name]=(reflection(d['v'],d['i'],m['reference_ohm']),d['v']/d['i']/m['impedance_scale'] if kind in ('forward','reverse')else None)
    pairs=[]
    for kind in f.KINDS:
        pairs.extend((f'n{a}_{kind}',f'n{b}_{kind}',kind,'mesh')for a,b in zip(f.MESHES[:-1],f.MESHES[1:]))
    pairs.extend((f'n2_{kind}',f'n2_{kind}_{control}',kind,'control')for kind in ('forward','reverse')for control in f.CONTROLS[1:])
    for first,second,kind,category in pairs:
        if first not in data or second not in data:continue
        a,b=data[first],data[second];delta=float(abs(b[0]-a[0]).max())
        row=dict(first=first,second=second,category=category,reflection_change_abs=delta,
            passes=delta<=f.LIMITS[category+'_reflection_abs'])
        if a[1] is not None:
            zdelta=float((abs(b[1]-a[1])/abs(f.diode_z(state=kind))).max())
            row.update(z_change_relative=zdelta);row['passes'] &= zdelta<=f.LIMITS[category+'_z_relative']
        changes.append(row)
    contrasts=[]
    for n in f.MESHES:
        names=[f'n{n}_forward',f'n{n}_reverse']
        if not all(v in data for v in names):continue
        for layout in ('series','shunt'):
            actual=f.contrast(*(data[v][1]for v in names),layout)
            target=f.contrast(f.diode_z(),f.diode_z(state='reverse'),layout)
            delta=float(abs(actual-target).max());contrasts.append(dict(n=n,layout=layout,error_db=delta,passes=delta<=f.LIMITS['contrast_db']))
    qualified=bool(not missing and not rejected and len(rows)==23 and len(changes)==18 and len(contrasts)==6
        and all(r['point_passes']for r in rows) and all(r['passes']for r in changes+contrasts))
    return dict(static_load_qualified=qualified,qualified=qualified,fdtd_twoport_switch_validated=False,
        book_example_validated=False,scope=f.SCOPE,missing=missing,rejected=rejected,rows=rows,changes=changes,contrasts=contrasts)



class CohortIdentityTests(unittest.TestCase):
    """Study admission must bind replayed data to the requested cohort."""

    def test_replayed_identity_and_duplicate_mesh_admission(self):
        from unittest.mock import patch
        from contextlib import ExitStack
        fields = ['n', 'kind', 'control']
        chosen = [c for c in configurations() if c[2] == 'forward'
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
                    (folder / 'progress.json').write_text(json.dumps(dict(status='complete')))
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
                    stack.enter_context(patch(__name__ + '.compare',
                        side_effect=lambda folder: row_for(Path(folder).parent)))
                    def replay(folder):
                        meta = dict(metadata[Path(folder).parent.name], impedance_scale=f.SCALE,
                                    reference_ohm=f.Z0*f.SCALE)
                        z = f.diode_z()*f.SCALE
                        return meta, dict(v=z, i=np.ones_like(z)), {}
                    stack.enter_context(patch.object(f, 'read', side_effect=replay))
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

class PinSwitchTests(unittest.TestCase):
    def test_impedance_scaled_embedding_keeps_the_same_scattering(self):
        for state in ('forward','reverse'):
            z=f.diode_z(state=state)
            for layout in ('series','shunt'):
                np.testing.assert_allclose(f.embed(z,layout),f.embed(z*f.SCALE,layout,f.Z0*f.SCALE),rtol=0,atol=1e-15)

    def test_embedding_matches_an_independent_nodal_network(self):
        for state in ('forward','reverse'):
            z=f.diode_z(state=state)
            actual=network_s(f.FREQUENCIES,[0,1],z_ref=f.Z0,impedances=[(0,1,z)])
            np.testing.assert_allclose(f.embed(z,'series'),actual,atol=3e-13,rtol=0)
            # Distinct ports joined through declared matched lines; remove
            # their known round-trip phase to expose the central shunt.
            delay=25e-12
            actual=network_s(f.FREQUENCIES,[0,2],[(0,1,f.Z0,delay),(1,2,f.Z0,delay)],
                z_ref=f.Z0,admittances=[(1,-1,1/z)])
            actual*=np.exp(4j*np.pi*f.FREQUENCIES*delay)[:,None,None]
            np.testing.assert_allclose(f.embed(z,'shunt'),actual,atol=3e-13,rtol=0)

    def test_positive_resistance_gives_passive_reciprocal_embeddings(self):
        for state in ('forward','reverse'):
            for layout in ('series','shunt'):
                s=f.embed(f.diode_z(state=state),layout)
                np.testing.assert_allclose(s,s.transpose(0,2,1),atol=0)
                self.assertLessEqual(float((np.linalg.svd(s,compute_uv=False)[:,0]**2).max()),1+1e-14)

    def test_logical_shunt_on_uses_the_reverse_diode_state(self):
        fwd,rev=f.diode_z(),f.diode_z(state='reverse')
        self.assertGreater(float(f.contrast(fwd,rev,'shunt')[60]),float(f.contrast(fwd,rev,'series')[60]))
        self.assertTrue(np.all(f.contrast(fwd,rev,'shunt')>0))

    def test_native_geometry_uses_separate_parallel_type_components(self):
        for kind,keys in [('open',[]),('short',[]),('resistor',['R']),('forward',['R','L']),('reverse',['R','L','C'])]:
            sim,m=f.build(1,kind)
            self.assertEqual([e['branch']for e in m['elements']],keys)
            self.assertEqual(sim._port_objs[0].R,f.Z0*f.SCALE)
            self.assertFalse(m['fdtd_twoport_switch_validated'])
            with tempfile.TemporaryDirectory()as tmp:
                path=Path(tmp)/'input.xml';sim.fdtd.Write2XML(str(path));xml=ET.parse(path)
            elements=[e for e in xml.findall('.//LumpedElement')if e.get('Name','').startswith('load_')]
            self.assertEqual(len(elements),len(keys))
            for element,key in zip(elements,keys):
                self.assertEqual(sum(element.get(k)is not None for k in ('R','L','C')),1)
                self.assertEqual(float(element.get('LEtype','0')),0.)
            if kind=='reverse':self.assertGreater(m['elements'][-1]['value']/m['elements'][-1]['minimum_native_c_f'],50)

    def test_json_plan_is_stable_and_controls_are_explicit(self):
        _,base=f.build(1,'reverse')
        self.assertEqual(base,json.loads(json.dumps(base,allow_nan=False)))
        for control in f.CONTROLS[1:]:
            _,other=f.build(1,'reverse',control)
            self.assertEqual(other['impedance_scale'],base['impedance_scale'])
            self.assertEqual([e['value']for e in other['elements']],[e['value']for e in base['elements']])
        self.assertLess(f.build(1,'reverse','dt')[1]['declared_dt_s'],base['declared_dt_s'])
        self.assertGreater(f.build(1,'reverse','time')[1]['cap_s'],base['cap_s'])

    def test_explicit_conditioning_records_and_scales_every_branch(self):
        for scale in (2.25,10.):
            _,m=f.build(1,'reverse',impedance_scale=scale)
            self.assertEqual(m['reference_ohm'],f.Z0*scale);self.assertEqual(m['impedance_scale'],scale)
            np.testing.assert_allclose([e['value']for e in m['elements']],[f.RR*scale,f.LI*scale,f.CJ/scale],rtol=1e-15)
            self.assertEqual(m,json.loads(json.dumps(m,allow_nan=False)))
        for scale in (0.,-1.,np.nan,np.inf,True):
            with self.assertRaises(ValueError):f.build(1,'reverse',impedance_scale=scale)

    def test_nonfinite_or_incomplete_clocks_are_rejected(self):
        dt=1e-13;stride=int(1/(2*f.FREQUENCIES[-1]*dt))//4
        meta=dict(native=dict(dt_s=dt,numerical_time_s=3*stride*dt))
        data=np.column_stack((np.arange(4)*stride*dt,np.ones(4)))
        f.trace_clock(data,meta);f.trace_clock(data+np.array([dt/2,0]),meta,True)
        for value in (data[:-1],data+np.array([dt/2,0])):
            with self.assertRaises(ValueError):f.trace_clock(value,meta)

    def test_current_contour_encloses_dual_cells_at_primary_line_endpoints(self):
        _,m=f.build(1,'forward')
        # Actual rc3 pilot header: nearest dual picks upper [3,5,5]
        # and misses the outer transverse current segment.
        self.assertEqual(m['expected_probe_indices']['port_it_1'],[[1,1,5],[4,6,5]])
        self.assertEqual(m['expected_probe_indices']['port_ut_1'],[[3,4,3],[3,4,9]])

    def test_operator_warning_and_zero_incident_spectrum_cannot_be_admitted(self):
        with self.assertRaisesRegex(ValueError,'lumped warning'):f.reject_warnings('Operator::Calc_LumpedElements(): Warning: skipped C')
        with self.assertRaises(ValueError):reflection(np.zeros(121),np.zeros(121))

    def test_overlong_forecast_does_not_create_output_or_dispatch(self):
        with tempfile.TemporaryDirectory()as tmp,patch(__name__+'.popen_group')as dispatch:
            out=Path(tmp)/'absent'
            with self.assertRaisesRegex(ValueError,'over 30'):acquire(out,1,'forward',rate=.001)
            self.assertFalse(out.exists());dispatch.assert_not_called()

    def test_invalid_requests_have_no_native_dispatch(self):
        for n in (0,True,4,1.5):
            with self.assertRaises(ValueError):f.build(n,'forward')
        for value in ([0.],[np.nan],np.array([1e9+1j])):
            with self.assertRaises(ValueError):f.diode_z(value)
        with tempfile.TemporaryDirectory()as tmp,patch(__name__+'.popen_group')as dispatch:
            with self.assertRaises(ValueError):acquire(Path(tmp)/'absent',1,'forward',pilot_s=1e-9)
            dispatch.assert_not_called()

    def test_missing_study_never_claims_a_switch_field_validation(self):
        with tempfile.TemporaryDirectory()as tmp:
            result=study(tmp);self.assertEqual(len(result['missing']),23)
            self.assertFalse(result['qualified']);self.assertFalse(result['fdtd_twoport_switch_validated'])
            self.assertFalse(result['book_example_validated'])

    def test_explicit_cap_diagnostic_waives_only_global_stopping(self):
        m=dict(native=dict(timesteps=10,numerical_time_s=1.),max_timesteps=10,cap_s=1.)
        q=dict(stopping=False,source=True,clock=True,tail=True,geometry=True,probes=True)
        self.assertFalse(f.admissible(m,q));self.assertTrue(f.admissible(m,q,True))
        for key in ('source','clock','tail','geometry','probes'):
            self.assertFalse(f.admissible(m,dict(q,**{key:False}),True))
        self.assertFalse(f.admissible(dict(m,cap_s=2.),q,True))

    def test_capped_diagnostics_cannot_enter_strict_study_qualification(self):
        with tempfile.TemporaryDirectory()as tmp:
            folder=Path(tmp)/'n1_forward';folder.mkdir()
            f.save(folder/'progress.json',dict(status='complete_with_known_deviation'))
            result=study(tmp)
            self.assertFalse(result['qualified']);self.assertEqual(len(result['rejected']),1)
            self.assertFalse(result['book_example_validated'])

    def test_failed_worker_keeps_receipt_and_does_not_make_a_comparison(self):
        proc=Mock(pid=123,win_job=None);proc.poll.return_value=2
        with tempfile.TemporaryDirectory()as tmp,patch(__name__+'.popen_group',return_value=proc),\
            patch(__name__+'.wait_owned',return_value=2),patch(__name__+'.release_group'):
            out=Path(tmp)/'failed'
            with self.assertRaises(RuntimeError):acquire(out,1,'forward',rate=1000.)
            self.assertEqual(json.loads((out/'worker.json').read_text())['exit_code'],2)
            self.assertFalse((out/'comparison.json').exists())
            self.assertIsNone(json.loads((out/'progress.json').read_text())['active_launcher_pid'])

    def test_nonfinite_energy_terminates_only_its_owned_worker(self):
        proc=Mock(pid=123,win_job='owned');proc.poll.return_value=None;proc.wait.return_value=9
        def launch(command,**kwargs):
            kwargs['stdout'].write(f.MARKER+'\nEnergy: ~-nan(ind)\n');kwargs['stdout'].flush();return proc
        with tempfile.TemporaryDirectory()as tmp,patch(__name__+'.popen_group',side_effect=launch),\
            patch(__name__+'.terminate_group')as stop,patch(__name__+'.release_group'):
            out=Path(tmp)/'failed'
            with self.assertRaisesRegex(ValueError,'non-finite'):acquire(out,1,'forward',rate=1000.)
            stop.assert_called_once_with(123,grace=0,job='owned')
            self.assertFalse((out/'comparison.json').exists())


def main():
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True)
    for name in ('prepare','fdtd','worker','compare','study'):mode.add_argument('--'+name,action='store_true')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--mesh',type=int,choices=f.MESHES,default=1)
    p.add_argument('--kind',choices=f.KINDS,default='forward');p.add_argument('--control',choices=f.CONTROLS,default='ordinary')
    p.add_argument('--rate-mcps',type=float,default=60.);p.add_argument('--pilot-seconds',type=float)
    p.add_argument('--allow-capped-diagnostic',action='store_true')
    p.add_argument('--impedance-scale',type=float)
    args=p.parse_args()
    if args.allow_capped_diagnostic and not(args.fdtd or args.compare):p.error('capped diagnostics are separate from strict study')
    if args.impedance_scale is not None and (not(args.prepare or args.fdtd or args.worker)
        or not np.isfinite(args.impedance_scale) or args.impedance_scale<=0):p.error('positive conditioning scale belongs to a prepared acquisition')
    scale=f.SCALE if args.impedance_scale is None else args.impedance_scale
    if args.pilot_seconds is not None and (not(args.fdtd or args.worker) or not np.isfinite(args.pilot_seconds) or not 0<args.pilot_seconds<=.5e-9):p.error('performance-only pilot must be in 0..0.5 ns')
    if args.worker:f.capture(args.out,args.mesh,args.kind,args.control,args.pilot_seconds,scale);return
    if args.fdtd:result=acquire(args.out,args.mesh,args.kind,args.control,args.rate_mcps,args.pilot_seconds,args.allow_capped_diagnostic,scale)
    elif args.compare:result=compare(args.out/'capture',args.allow_capped_diagnostic)
    elif args.study:result=study(args.out)
    else:
        args.out.mkdir(parents=True,exist_ok=False);result=freeze(args.out,args.mesh,args.kind,args.control,impedance_scale=scale)
        result=dict(result,forecast_seconds=forecast(result,args.rate_mcps),native_propagations=0)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__':
    if any('--'+name in sys.argv for name in ('prepare','fdtd','worker','compare','study')):main()
    else:unittest.main()
