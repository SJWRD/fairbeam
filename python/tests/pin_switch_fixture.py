"""Own static load surrogate for Example 11.1 (p. 532), not a diode device.

One-port FDTD measures fixed forward/reverse impedances. Embedding those
loads in ideal series/shunt two-ports is algebra, not two-port field data.
All impedances use one explicit common scale; S is invariant under the
same reference-impedance scaling. The default is selected from independent
open/short controls and a held-out resistor, not a fit to the load data.
No bias, transport or switching physics.
"""
import json
import math
from pathlib import Path
import re

import numpy as np
from openEMS.physical_constants import C0, EPS0
from openEMS.ports import UI_data
from fairbeam import Simulation, excitation
from tests.coax_resonator_fixture import save, sha
from tests import coupled_stripline_fixture as environment

ROOT=Path(__file__).resolve().parents[2]
FREQUENCIES=np.linspace(1.2e9,2.4e9,121)
Z0, SCALE, RF, RR, LI, CJ=50.,2.25,1.5,2.,.5e-9,.5e-12
KINDS=('open','short','resistor','forward','reverse')
MESHES=(1,2,3)
CONTROLS=('ordinary','time','dt','domain','scale')
# -80 dB can stop the reverse load before its full pulse/local-tail window.
# A remaining global-energy floor still makes a run diagnostic-only.
CAP_S,END_DB,THREADS,OVERSAMPLING=8e-9,-115.,1,4
MARKER='FAIRBEAM-STATIC-PIN-PROPAGATION-BEGIN'
LIMITS=dict(reflection_abs=.005,z_relative=.02,mesh_reflection_abs=.0025,
    mesh_z_relative=.005,control_reflection_abs=.001,control_z_relative=.003,
    attenuation_db=.1,contrast_db=.1,terminal_tail_db=-70.)
SCOPE='One-port fixed linear load; ideal series/shunt embedding is postprocessing, not a full two-port FDTD switch'


def identity():
    names=('tests/pin_switch_fixture.py','tests/test_pin_switch_fixture.py',
        'tests/coax_resonator_fixture.py','tests/coupled_stripline_fixture.py',
        'tests/test_coax_resonator.py','fairbeam/simulation.py','fairbeam/excitation.py',
        'fairbeam/network.py','fairbeam/procutil.py')
    return {name:sha(ROOT/'python'/name) for name in names}


def diode_z(freq=FREQUENCIES,state='forward',scale=1.):
    if np.iscomplexobj(freq):raise ValueError('real frequency vector required')
    f=np.asarray(freq,float)
    if (state not in ('forward','reverse') or f.ndim!=1 or not f.size
        or not np.isfinite(f).all() or np.any(f<=0) or not np.isfinite(scale) or scale<=0):
        raise ValueError('positive real frequencies/scale and declared static state required')
    w=2*np.pi*f
    return scale*((RF if state=='forward' else RR)+1j*w*LI+(1/(1j*w*CJ) if state=='reverse' else 0))


def embed(z,layout,z0=Z0):
    z=np.asarray(z,complex)
    if (z.ndim!=1 or not z.size or not np.isfinite(z).all() or layout not in ('series','shunt')
        or not np.isfinite(z0) or z0<=0):raise ValueError('finite load vector, layout and positive real reference required')
    r=z/(2*z0+z) if layout=='series' else -z0/(2*z+z0)
    t=2*z0/(2*z0+z) if layout=='series' else 2*z/(2*z+z0)
    result=np.empty((len(z),2,2),complex)
    result[:,0,0]=result[:,1,1]=r;result[:,0,1]=result[:,1,0]=t
    return result


def contrast(forward,reverse,layout):
    loss=lambda z:-20*np.log10(abs(embed(z,layout)[:,1,0]))
    return loss(reverse)-loss(forward) if layout=='series' else loss(forward)-loss(reverse)


def reference_reflection(kind,scale=SCALE):
    if kind not in KINDS:raise ValueError('unknown load')
    if kind in ('open','short','resistor'):
        return np.full(len(FREQUENCIES),{'open':1.,'short':-1.,'resistor':0.}[kind],complex)
    z=diode_z(state=kind,scale=scale)
    return (z-Z0*scale)/(z+Z0*scale)


def capacitor_floor(mesh,start,stop):
    indices=[];grids=[np.asarray(mesh[a]) for a in 'xyz']
    for g,a,b in zip(grids,start,stop):
        dual=(g[:-1]+g[1:])/2;lo,hi=[int(np.searchsorted(dual,v,side='left'))for v in (a,b)]
        if not 0<lo<=hi<len(g)-1:raise ValueError('interior ordered capacitor required')
        indices.append((lo,hi))
    area=1.
    for g,(lo,hi) in zip(grids[:2],indices[:2]):area*=((g[hi]+g[hi+1])-(g[lo-1]+g[lo]))/2
    lo,hi=indices[2]
    if lo==hi:raise ValueError('capacitor must cross an axial edge')
    return float(EPS0*area/(grids[2][hi]-grids[2][lo])*1e-3)


class LoadSimulation(Simulation):
    def to_bundle(self,*args,**kwargs):raise ValueError('static research load has no Designer bundle representation')


def probe_bounds(mesh,start,stop,current=False):
    """rc3 operator snapping; current boxes enclose their dual-grid area.

    See Operator::SnapToMeshLine / SnapBox2Mesh and ProcessCurrent. A
    nearest-dual approximation misses the upper transverse boundary when
    an endpoint is exactly on a primary line.
    """
    low=[];high=[]
    for a,left,right in zip('xyz',start,stop):
        g=np.asarray(mesh[a]);dual=(g[:-1]+g[1:])/2
        if current:
            lo,hi=[int(np.searchsorted(g,v,side='left'))-1 for v in (left,right)]
            if hi>lo:
                if dual[lo]>left:lo-=1
                if dual[hi]<right:hi+=1
        else:lo,hi=[int(np.searchsorted(dual,v,side='left'))for v in (left,right)]
        if not 0<=lo<=hi<len(g)-(1 if current else 0):raise ValueError('probe outside declared grid')
        low.append(lo);high.append(hi)
    return [low,high]


def build(n,kind,control='ordinary',cap_s=None,*,impedance_scale=SCALE):
    if isinstance(n,bool) or n not in MESHES or not isinstance(n,int) or kind not in KINDS or control not in CONTROLS:
        raise ValueError('declared mesh/load/control required')
    if isinstance(impedance_scale,bool) or not np.isfinite(impedance_scale) or impedance_scale<=0:
        raise ValueError('positive finite impedance scale required')
    scale=float(impedance_scale)
    factor=.5 if control=='scale' else 1.
    margin=1.5 if control=='domain' else 1
    counts=(12,8,12);extents=(.03,.02,.02)
    mesh={a:(np.arange(-count*n*margin//2,count*n*margin//2+1)*2*extent/(count*n)*factor).tolist()
        for a,count,extent in zip('xyz',counts,extents)}
    grid=[len(mesh[a])for a in 'xyz'];cells=int(np.prod(grid))
    dt=(.375 if control=='dt' else .5)/(C0*np.sqrt(sum(1/(np.diff(mesh[a]).min()*1e-3)**2 for a in 'xyz')))
    cap=CAP_S*(1.5 if control=='time' else 1.) if cap_s is None else float(cap_s)
    if not np.isfinite(cap) or cap<=0:raise ValueError('positive finite allocation required')
    sim=LoadSimulation(FREQUENCIES[0],FREQUENCIES[-1],boundaries=['PEC']*6,
        end_criteria_db=END_DB,max_timesteps=math.ceil(cap/dt),excitation='dgauss')
    for axis,lines in mesh.items():sim.mesh.AddLine(axis,lines)
    sim.fdtd.SetTimeStep(float(dt));sim.fdtd.SetOverSampling(OVERSAMPLING)
    coords=lambda p:(np.asarray(p)*factor).tolist()
    plates=sim.metal('plates')
    for z in (-.01,.01):plates.AddBox(coords([-.02,-.01,z]),coords([.02,.01,z]),priority=10)
    sim.lumped_port(1,Z0*scale,coords([-.02,-.01,-.01]),coords([-.01,.01,.01]),'z')
    elements=[];nodes=[]
    if kind=='short':plates.AddBox(coords([.01,-.01,-.01]),coords([.02,.01,.01]),priority=10)
    specifications=[]
    if kind=='resistor':specifications=[('R',Z0*scale,-.01,.01)]
    elif kind=='forward':specifications=[('R',RF*scale,-.01,0.),('L',LI*scale,0.,.01)];nodes=[0.]
    elif kind=='reverse':
        specifications=[('R',RR*scale,-.01,-.01/3),('L',LI*scale,-.01/3,.01/3),('C',CJ/scale,.01/3,.01)]
        nodes=[-.01/3,.01/3]
    if nodes:
        pads=sim.metal('internal_nodes')
        for z in nodes:pads.AddBox(coords([.01,-.01,z]),coords([.02,.01,z]),priority=10)
    for key,value,a,b in specifications:
        start,stop=coords([.01,-.01,a]),coords([.02,.01,b]);name='load_'+key
        floor=capacitor_floor(mesh,start,stop) if key=='C' else None
        if floor is not None and value<=floor*(1+1e-6):raise ValueError('requested capacitance below native box floor')
        sim.lumped_element(name,start,stop,'z',**{key:float(value)},priority=7)
        elements.append(dict(name=name,branch=key,value=float(value),start_mm=start,stop_mm=stop,minimum_native_c_f=floor))
    for prop in sim.csx.GetAllProperties():prop.SetColor((128,128,128),alpha=prop.GetFillColor()[3])
    if sim.csx.Update():raise ValueError('invalid geometry')
    required=[p.GetName() for p in sim.csx.GetAllProperties() if p.GetTypeString() in ('Metal','LumpedElement','Excitation')]
    port=sim._port_objs[0];probe={};expected={}
    for name in port.U_filenames+port.I_filenames:
        box=sim.csx.GetPropertiesByName(name)[0].GetAllPrimitives()[0]
        start,stop=np.asarray(box.GetStart()),np.asarray(box.GetStop())
        probe[name]=dict(start_mm=start.tolist(),stop_mm=stop.tolist())
        expected[name]=probe_bounds(mesh,start,stop,name in port.I_filenames)
    return sim,dict(n=n,kind=kind,control=control,mesh_mm=mesh,grid=grid,native_cells=cells,
        declared_dt_s=float(dt),cap_s=cap,max_timesteps=math.ceil(cap/dt),end_db=END_DB,
        reference_ohm=Z0*scale,impedance_scale=scale,geometry_scale=factor,elements=elements,
        required_properties=required,probe_boxes=probe,expected_probe_indices=expected,
        voltage_names=list(port.U_filenames),current_names=list(port.I_filenames),
        source_duration_s=excitation.dgauss_duration_s(FREQUENCIES[-1]),threads=THREADS,
        scope=SCOPE,qualified=False,fdtd_twoport_switch_validated=False)


def used(sim,names):
    return {p.GetName():[bool(v.GetPrimitiveUsed())for v in p.GetAllPrimitives()]
        for p in sim.csx.GetAllProperties() if p.GetName() in names}


def reject_warnings(text):
    if re.search(r'(?:Calc_LumpedElements|LumpedRLC::BuildExtension).*Warning:',text):
        raise ValueError('native lumped warning; model rejected')


def trace_clock(data,m,magnetic=False):
    dt=m['native']['dt_s'];stride=int(1/(2*FREQUENCIES[-1]*dt))//OVERSAMPLING
    count=round(m['native']['numerical_time_s']/dt)//stride+1
    expected=(np.arange(count)*stride+(.5 if magnetic else 0))*dt
    if (data.shape!=(count,2) or not np.isfinite(data).all()
        or not np.allclose(data[:,0],expected,rtol=2e-10,atol=dt*1e-5)):
        raise ValueError('incomplete or changed native clock')


def source_info(path,m):
    data=np.loadtxt(path,comments='%',ndmin=2);dt=m['native']['dt_s']
    if data.shape not in ((m['max_timesteps'],2),(m['max_timesteps']+1,2)) or not np.isfinite(data).all():
        raise ValueError('incomplete allocated source')
    clock=np.arange(len(data))*dt
    if not np.allclose(data[:,0],clock,rtol=6e-6,atol=dt*1e-6):raise ValueError('source clock changed')
    tau=1/(np.sqrt(2)*np.pi*FREQUENCIES[-1]/2.76);t0=float(f'{5*tau:.6e}');tau=float(f'{tau:.6e}')
    q=(clock-t0)/tau;expected=-float(f'{np.sqrt(2*np.e):.8f}')*q*np.exp(-q*q)
    error=float(abs(data[:,1]-expected).max()/max(abs(expected).max(),np.finfo(float).tiny))
    if error>6e-6:raise ValueError('source waveform changed')
    return dict(sha256=sha(path),waveform_error_rel=error)


def quality(m):
    native,run,op=m['native'],m['run'],m['operator']
    energy=[run.get(k)for k in ('final_energy_db','final_energy_bound_db')]
    energy=[v for v in energy if isinstance(v,(int,float)) and np.isfinite(v)]
    return {k:bool(v)for k,v in dict(grid=native['cells']==m['native_cells'] and run.get('grid')==m['grid'],
        clock=abs(native['dt_s']/m['declared_dt_s']-1)<1e-8,
        time=native['timesteps']>0 and abs(native['numerical_time_s']/(native['timesteps']*native['dt_s'])-1)<1e-8,
        source=native['numerical_time_s']>=m['source_duration_s'],
        stopping=run.get('converged') is True and run.get('hit_timestep_limit') is False
            and bool(energy) and max(energy)<=m['end_db']+1e-7,
        operator=0<m['declared_dt_s']<=.8*op['run'].get('timestep_s',0),
        geometry=m['required_usage']==op['required_usage'] and set(m['required_usage'])==set(m['required_properties'])
            and all(v and all(v)for v in m['required_usage'].values()),
        probes=m.get('actual_probe_indices')==m['expected_probe_indices'],
        engine=run.get('engine')=='cpu' and run.get('threads')==THREADS and run.get('exact_endcriteria') is True,
        header=m.get('native_header')==dict(version='v0.37.0-rc3',threads=THREADS),
        tail=m['terminal_tail_db']<=LIMITS['terminal_tail_db']).items()}


def audit_header(text,m):
    reject_warnings(text);body=text.split(MARKER)[-1]
    grid=re.search(r'FDTD simulation size: (\d+)x(\d+)x(\d+) --> (\S+) FDTD cells',body)
    if (text.count(MARKER)!=1 or re.findall(r'openEMS 64bit -- version (\S+)',body)!=['v0.37.0-rc3']
        or f'fixed number of threads: {THREADS}' not in body or not grid
        or list(map(int,grid.groups()[:3]))!=m['grid']
        or float(grid[4])!=float(format(m['native_cells'],'.6g'))):raise ValueError('native header mismatch')
    return dict(version='v0.37.0-rc3',threads=THREADS)


def capture(out,n,kind,control='ordinary',cap_s=None,impedance_scale=SCALE):
    out=Path(out);out.mkdir(exist_ok=False,parents=True);sim,m=build(n,kind,control,cap_s,impedance_scale=impedance_scale)
    m.update(source=identity(),runtime=environment.runtime_identity())
    sim.fdtd.Write2XML(str(out/'input.xml'));m['input_sha256']=sha(out/'input.xml')
    plan=json.loads((out.parent/'plan.json').read_text())
    if plan!={**m}:raise ValueError('input differs from frozen pre-field plan')
    setup,_=build(n,kind,control,cap_s,impedance_scale=impedance_scale);setup.fdtd.SetTimeStep(0.);setup.fdtd.SetNumberOfTimeSteps(1024)
    setup.fdtd.Write2XML(str(out/'operator-input.xml'))
    op=dict(run=setup.run(str(out/'operator'),threads=THREADS,exact=True,echo=True,setup_only=True),
        required_usage=used(setup,m['required_properties']),input_sha256=sha(out/'operator-input.xml'))
    save(out/'operator.json',op)
    reject_warnings((out.parent/'worker.log').read_text())
    if (not 0<m['declared_dt_s']<=.8*op['run'].get('timestep_s',0.) or op['run'].get('grid')!=m['grid']
        or not all(v and all(v)for v in op['required_usage'].values())):raise ValueError('operator admission failed')
    if identity()!=m['source'] or environment.runtime_identity()!=m['runtime']:raise ValueError('frozen source/runtime changed')
    print(MARKER,flush=True)
    sim.run(str(out/'raw'),threads=THREADS,exact=True,echo=True,dump_statistics=True)
    st=np.loadtxt(out/'raw/openEMS_stats.txt',comments='%')
    m.update(native=dict(cells=int(st[0]),dt_s=float(st[1]),timesteps=int(st[2]),numerical_time_s=float(st[3]),iteration_wall_s=float(st[4])),
        run=sim.run_stats,operator=op,required_usage=used(sim,m['required_properties']))
    values=[];tails=[];hashes={};indices={}
    for name in m['voltage_names']+m['current_names']:
        path=out/'raw'/name;trace=np.loadtxt(path,comments='%',ndmin=2);trace_clock(trace,m,name in m['current_names'])
        values.append(np.asarray(UI_data([name],str(out/'raw'),FREQUENCIES).ui_f_val)[0])
        tail=trace[:,0]>=m['native']['numerical_time_s']-.5e-9
        tails.append(float(10*np.log10(max(np.mean(trace[tail,1]**2),np.finfo(float).tiny)/max(np.max(trace[:,1]**2),np.finfo(float).tiny))))
        hashes[name]=sha(path);indices[name]=[list(map(int,v))for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]','\n'.join(path.read_text().splitlines()[:3]))]
    v,i=values;port=sim._port_objs[0];port.CalcPort(str(out/'raw'),FREQUENCIES,ref_impedance=m['reference_ohm'])
    np.testing.assert_allclose(port.uf_tot,v,rtol=3e-13,atol=1e-23);np.testing.assert_allclose(port.if_tot,i,rtol=3e-13,atol=1e-23)
    m.update(terminal_tail_db=max(tails),raw_sha256=hashes,actual_probe_indices=indices,source_trace=source_info(out/'raw/et',m))
    np.savez_compressed(out/'data.npz',frequency_hz=FREQUENCIES,v=v,i=i)
    m.update(data_sha256=sha(out/'data.npz'),operator_sha256=sha(out/'operator.json'))
    if identity()!=m['source'] or environment.runtime_identity()!=m['runtime']:raise ValueError('source/runtime changed during fields')
    save(out/'report.json',m)
    return m


def admissible(m,checks,allow_cap=False):
    failed=[k for k,v in checks.items()if not v]
    return not failed or (allow_cap and failed==['stopping']
        and m['native']['timesteps']==m['max_timesteps'] and m['native']['numerical_time_s']>=m['cap_s'])


def read(out,allow_cap=False):
    out=Path(out);m=json.loads((out/'report.json').read_text())
    _,expected=build(m['n'],m['kind'],m['control'],m['cap_s'],impedance_scale=m['impedance_scale'])
    if (any(m.get(k)!=v for k,v in expected.items()) or m['source']!=identity()
        or m['runtime']!=environment.runtime_identity()):raise ValueError('stored model/source/runtime changed')
    q=quality(m)
    if not admissible(m,q,allow_cap):raise ValueError(f'rejected native source: {q}')
    for name,want in [('input.xml',m['input_sha256']),('operator-input.xml',m['operator']['input_sha256']),
        ('operator.json',m['operator_sha256']),('data.npz',m['data_sha256'])]:
        if sha(out/name)!=want:raise ValueError('stored input/operator/data changed')
    log=out.parent/'worker.log';receipt=json.loads((out.parent/'worker.json').read_text())
    if (sha(log)!=m['native_log_sha256'] or audit_header(log.read_text(),m)!=m['native_header']
        or receipt['exit_code']!=0 or receipt['failure'] is not None or receipt['deadline_seconds']!=1800.
        or max(receipt['wall_seconds'],receipt['monotonic_seconds'])>1800.):raise ValueError('native log or owned process receipt changed')
    plan=dict(expected,source=identity(),runtime=environment.runtime_identity(),input_sha256=m['input_sha256'])
    if json.loads((out.parent/'plan.json').read_text())!=plan:raise ValueError('stored pre-field plan changed')
    stats=np.loadtxt(out/'raw/openEMS_stats.txt',comments='%')
    if m['native']!=dict(cells=int(stats[0]),dt_s=float(stats[1]),timesteps=int(stats[2]),numerical_time_s=float(stats[3]),iteration_wall_s=float(stats[4])):
        raise ValueError('native statistics changed')
    values=[];tails=[]
    for name in m['voltage_names']+m['current_names']:
        path=out/'raw'/name
        if sha(path)!=m['raw_sha256'][name]:raise ValueError('raw probe changed')
        data=np.loadtxt(path,comments='%',ndmin=2);trace_clock(data,m,name in m['current_names'])
        t,x=data.T;values.append(np.asarray([2*(t[1]-t[0])*np.sum(x*np.exp(-2j*np.pi*f*t))for f in FREQUENCIES]))
        indices=[list(map(int,v))for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]','\n'.join(path.read_text().splitlines()[:3]))]
        if indices!=m['expected_probe_indices'][name]:raise ValueError('native probe indices changed')
        mask=t>=m['native']['numerical_time_s']-.5e-9
        tails.append(float(10*np.log10(max(np.mean(x[mask]**2),np.finfo(float).tiny)/max(np.max(x*x),np.finfo(float).tiny))))
    with np.load(out/'data.npz')as d:
        data=dict(d)
    np.testing.assert_array_equal(data['frequency_hz'],FREQUENCIES)
    for name,value in zip(('v','i'),values):np.testing.assert_allclose(data[name],value,rtol=3e-13,atol=1e-23)
    if max(tails)!=m['terminal_tail_db'] or source_info(out/'raw/et',m)!=m['source_trace']:raise ValueError('tail/source record changed')
    return m,data,q
