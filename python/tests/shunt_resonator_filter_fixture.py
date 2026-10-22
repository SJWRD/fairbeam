"""Own coaxial realization of the circuit in Example 8.10, p. 446.

The zero-connection circuit, the circuit with declared finite connecting
lines, and the finite coaxial T-junction geometry are distinct references.
No PCB layout, package, component or line length is fitted to a response.
"""
import math
import json
import re
from pathlib import Path
import numpy as np
from openEMS.physical_constants import C0, Z0 as ETA0
from fairbeam import Simulation, excitation, multiport
from fairbeam.network import network_s
from tests.coax_resonator_fixture import save, sha
from tests import coupled_stripline_fixture as native_environment
from openEMS import ports as native_ports
from openEMS.ports import UI_data

ROOT=Path(__file__).resolve().parents[2]
F0,FBW,RIPPLE_DB,Z0=2.5e9,.1,.5,50.
FREQUENCIES=np.linspace(1.5e9,3.5e9,401)
MESHES=(0,1,2)
KINDS=('through','capacitor','stub','filter')
CONTROLS=('ordinary','time','dt','boundary','plane','source')
COARSE_MM=(.6,.45,.36)
RO,RI,ROS=3.,3.*np.exp(-2*np.pi*Z0/ETA0),3.5
GAP_MM=1.
STUB_X_MM=(-8.,0.,8.)
CAP_X_MM=(-12.,-4.,4.,12.)
END_DB,CAP_S,THREADS,OVERSAMPLING=-100.,120e-9,4,4
MARKER='FAIRBEAM-SHUNT-COAX-FILTER-PROPAGATION-BEGIN'
LIMITS=dict(s_abs=.03,phase_deg=3.,mesh_s_abs=.005,mesh_phase_deg=.5,
    control_s_abs=.0025,control_phase_deg=.2,incident_condition=30.,incident_floor=.02,
    reciprocity_abs=.005,singular_power_max=1.02,terminal_tail_db=-70.)


def identity():
    names=('tests/shunt_resonator_filter_fixture.py','tests/test_shunt_resonator_filter_fixture.py',
        'tests/coax_resonator_fixture.py','tests/test_coax_resonator.py','tests/coupled_stripline_fixture.py',
        'fairbeam/simulation.py','fairbeam/coax.py','fairbeam/excitation.py','fairbeam/multiport.py',
        'fairbeam/network.py','fairbeam/procutil.py')
    return {name:sha(ROOT/'python'/name) for name in names}


def runtime_identity():
    value=native_environment.runtime_identity();value['native_ports_sha256']=sha(native_ports.__file__)
    return value


def usage(sim,names):
    return {p.GetName():[bool(v.GetPrimitiveUsed()) for v in p.GetAllPrimitives()]
        for p in sim.csx.GetAllProperties() if p.GetName() in names}


def synthesis():
    order=3;epsilon=np.sqrt(10**(RIPPLE_DB/10)-1)
    beta=np.arcsinh(1/epsilon);gamma=np.sinh(beta/order)
    a=np.sin((2*np.arange(1,order+1)-1)*np.pi/(2*order))
    b=gamma**2+np.sin(np.arange(1,order)*np.pi/order)**2
    g=[1.,2*a[0]/gamma]
    for k in range(1,order):g.append(4*a[k-1]*a[k]/(b[k-1]*g[-1]))
    g.append(1.)
    j=np.array([np.sqrt(np.pi*FBW/(4*g[0]*g[1])),
        *[np.pi*FBW/(4*np.sqrt(g[k]*g[k+1])) for k in range(1,order)],
        np.sqrt(np.pi*FBW/(4*g[order]*g[order+1]))])/Z0
    cap=j/(2*np.pi*F0);cap[[0,-1]]/=np.sqrt(1-(Z0*j[[0,-1]])**2)
    delta=-(cap[:-1]+cap[1:])
    theta=np.pi/2+2*np.pi*F0*Z0*delta
    delay=theta/(2*np.pi*F0)
    return dict(order=order,prototype_g=g,inverter_siemens=j.tolist(),capacitance_f=cap.tolist(),
        delta_capacitance_f=delta.tolist(),stub_theta_rad=theta.tolist(),stub_delay_s=delay.tolist(),
        stub_length_air_mm=(C0*delay*1e3).tolist(),r_inner_mm=float(RI),r_outer_mm=RO,r_shield_mm=ROS,
        stub_x_mm=list(STUB_X_MM),capacitor_x_mm=list(CAP_X_MM),gap_mm=GAP_MM,
        scope='Own air coaxial T-junction realization; finite connecting lines and junction parasitics remain; no PCB or package specified')


def prototype_loss(freq=FREQUENCIES):
    f=np.asarray(freq,float);omega=abs((f/F0-F0/f)/FBW)
    p=np.empty_like(omega);inside=omega<=1
    p[inside]=np.cos(3*np.arccos(omega[inside]));p[~inside]=np.cosh(3*np.arccosh(omega[~inside]))
    return 10*np.log10(1+(10**(RIPPLE_DB/10)-1)*p**2)


def reference(freq=FREQUENCIES,kind='filter',finite=True,plane_mm=28.):
    if np.iscomplexobj(freq):raise ValueError('real frequency vector required')
    f=np.asarray(freq,float)
    if (f.ndim!=1 or not f.size or not np.isfinite(f).all() or np.any((f<1e9)|(f>4e9))
            or kind not in KINDS or not np.isfinite(plane_mm) or plane_mm<=16):
        raise ValueError('declared kind, 1..4 GHz and exterior reference plane required')
    d=synthesis();w=2*np.pi*f
    if not finite:
        if kind!='filter':raise ValueError('the zero-connection reference is the original filter only')
        return network_s(f,[0,4],z_ref=Z0,
            impedances=[(k,k+1,1/(1j*w*c)) for k,c in enumerate(d['capacitance_f'])],
            admittances=[(k+1,-1,-1j/(Z0*np.tan(w*t))) for k,t in enumerate(d['stub_delay_s'])])
    if kind=='through':return network_s(f,[0,1],[(0,1,Z0,2*plane_mm*1e-3/C0)],z_ref=Z0)
    if kind in ('capacitor','stub'):
        if kind=='capacitor':
            gap=GAP_MM/2;delay=(plane_mm-gap)*1e-3/C0
            return network_s(f,[0,3],[(0,1,Z0,delay),(2,3,Z0,delay)],z_ref=Z0,
                impedances=[(1,2,1/(1j*w*d['capacitance_f'][0]))])
        delay=plane_mm*1e-3/C0
        return network_s(f,[0,2],[(0,1,Z0,delay),(1,2,Z0,delay)],z_ref=Z0,
            admittances=[(1,-1,-1j/(Z0*np.tan(w*d['stub_delay_s'][1])))])
    x=np.array([-plane_mm,-12.5,-11.5,-8.,-4.5,-3.5,0.,3.5,4.5,8.,11.5,12.5,plane_mm])
    gaps=(1,4,7,10);stubs=(3,6,9)
    return network_s(f,[0,12],z_ref=Z0,
        lines=[(k,k+1,Z0,(x[k+1]-x[k])*1e-3/C0) for k in range(12) if k not in gaps],
        impedances=[(k,k+1,1/(1j*w*c)) for k,c in zip(gaps,d['capacitance_f'])],
        admittances=[(k,-1,-1j/(Z0*np.tan(w*t))) for k,t in zip(stubs,d['stub_delay_s'])])


def _balanced(anchors,maximum):
    anchors=np.unique(np.round(anchors,12))
    values=np.unique(np.concatenate([np.linspace(a,b,int(np.ceil((b-a)/maximum-1e-12))+1)
        for a,b in zip(anchors[:-1],anchors[1:])]))
    for _ in range(10000):
        h=np.diff(values);growth=np.maximum(h[1:]/h[:-1],h[:-1]/h[1:])
        if growth.max()<=1.8*(1+1e-12):return values
        j=int(growth.argmax());large=j if h[j]>h[j+1] else j+1
        count=int(np.ceil(h[large]/(1.8*min(h[j],h[j+1]))-1e-12))
        values=np.sort(np.r_[values,np.linspace(values[large],values[large+1],count+1)[1:-1]])
    raise ValueError('mesh growth did not settle')


def mesh_lines(n,control='ordinary'):
    if isinstance(n,bool) or n not in MESHES or not isinstance(n,int) or control not in CONTROLS:
        raise ValueError('declared integer mesh/control required')
    step=COARSE_MM[n];cross=min(.5,step);extra=8. if control=='boundary' else 0.
    end=52.+extra
    xanchors=[-end,end,-48.,48.,-38.,38.,-34.,34.,-28.,28.,-24.,24.,-16.,16.,*STUB_X_MM]
    for centre in (*CAP_X_MM,0.):xanchors.extend((centre-GAP_MM/2,centre+GAP_MM/2))
    # Both selectable measurement planes have three equally spaced lines.
    for centre in (-28.,-24.,24.,28.):xanchors.extend((centre-.5,centre+.5))
    x=_balanced(xanchors,step)
    # PML uses its own uniform exterior cells. Port starts remain inside it.
    x=np.r_[x[0]-step*np.arange(8,0,-1),x,x[-1]+step*np.arange(1,9)]
    radial=[-4.5,-RO,-RI,0.,RI,RO,4.5]
    y=_balanced(radial,cross)
    lengths=synthesis()['stub_length_air_mm']
    z=_balanced([-max(lengths)-4.5,*[-v for v in lengths],*radial],cross)
    return x,y,z


def port_probe_geometry(sim):
    """Audit actual native loop coordinates against both coax conductors."""
    result=[]
    grids=[np.asarray(sim.mesh.GetLines(axis)) for axis in 'xyz']
    for port in sim._port_objs:
        idx=int(np.argmin(abs(grids[0]-(port.start[0]+port.direction*port.measplane_shift))))
        probes={};expected={};loops={}
        for name in [*port.U_filenames,*port.I_filenames]:
            prop=sim.csx.GetPropertiesByName(name)
            if len(prop)!=1 or len(prop[0].GetAllPrimitives())!=1:raise ValueError('one native coax probe primitive required')
            box=prop[0].GetAllPrimitives()[0]
            start,stop=np.asarray(box.GetStart()),np.asarray(box.GetStop())
            probes[name]=dict(start_mm=start.tolist(),stop_mm=stop.tolist())
            current=name in port.I_filenames;low=[];high=[]
            for axis,values in enumerate(grids):
                nearest=lambda value:int(np.flatnonzero(abs(values-value)<=abs(values-value).min()+1e-11)[0])
                if current:
                    dual=(values[:-1]+values[1:])/2
                    a,b=int(np.argmin(abs(dual-start[axis]))),int(np.argmin(abs(dual-stop[axis])))
                else:a,b=nearest(start[axis]),nearest(stop[axis])
                if not 0<=a<=b<len(values)-(1 if current else 0):raise ValueError('native probe escapes the grid')
                low.append(a);high.append(b)
            expected[name]=[low,high]
            if current:
                bounds=[]
                for axis in (1,2):
                    dual=(grids[axis][:-1]+grids[axis][1:])/2
                    bounds.append([float(dual[low[axis]]),float(dual[high[axis]])])
                if (any(a>=-RI or b<=RI for a,b in bounds)
                        or max(np.hypot(a,b) for a in bounds[0] for b in bounds[1])>=RO):
                    raise ValueError('native current contour must enclose the inner rod and exclude its shield')
                loops[name]=bounds
        result.append(dict(number=port.number,measurement_x_mm=float(grids[0][idx]),
            native_u_names=list(port.U_filenames),native_i_names=list(port.I_filenames),probe_boxes=probes,
            expected_indices=expected,current_loop_bounds_mm=loops))
    return result


class CoaxFilterSimulation(Simulation):
    def to_bundle(self,*args,**kwargs):
        raise ValueError('research circuit and native probe admission records are separate from Designer bundles')


def build(n,port,kind='filter',control='ordinary',cap_s=None):
    if isinstance(port,bool) or port not in (1,2) or not isinstance(port,int) or kind not in KINDS:
        raise ValueError('declared source/kind required')
    x,y,z=mesh_lines(n,control);grid=[len(v) for v in (x,y,z)];cells=int(np.prod(grid))
    if cells>5000000:raise ValueError('allocation exceeds five million cells')
    dt=(.375 if control=='dt' else .5)/(C0*np.sqrt(sum(1/(np.diff(v).min()*1e-3)**2 for v in (x,y,z))))
    cap=CAP_S*(1.5 if control=='time' else 1.) if cap_s is None else float(cap_s)
    if not np.isfinite(cap) or cap<=0:raise ValueError('positive finite window required')
    # -100/-110 dB can stop before the fixed 0.5 ns local-tail window is quiet;
    # -130 dB is below the observed calibration roundoff floor. Retain the
    # independent local-tail gate rather than treating either stop as proof.
    end=END_DB-(15 if kind!='filter' else 0)-(10 if control=='time' else 0)
    sim=CoaxFilterSimulation(FREQUENCIES[0],FREQUENCIES[-1],excitation='dgauss',
        boundaries=['PML_8','PML_8','PEC','PEC','PEC','PEC'],end_criteria_db=end,max_timesteps=math.ceil(cap/dt))
    for axis,lines in zip('xyz',(x,y,z)):sim.mesh.AddLine(axis,lines)
    sim.fdtd.SetTimeStep(float(dt));sim.fdtd.SetOverSampling(OVERSAMPLING)
    # The grounded block is the common shield. Higher-priority air cylinders
    # carve its joined channels; higher-priority rods/shorts remain PEC.
    sim.metal('grounded_block').AddBox([x[0],y[0],z[0]],[x[-1],y[-1],z[-1]],priority=1)
    air=sim.dielectric('coax_air',1.)
    air.AddCylinder([x[0],0.,0.],[x[-1],0.,0.],RO,priority=2)
    dimensions=synthesis();required=['grounded_block','coax_air'];elements=[]
    selected=range(3) if kind=='filter' else ((1,) if kind=='stub' else ())
    rods=sim.metal('inner_rods');required.append('inner_rods')
    if kind=='filter':gaps=[(c-GAP_MM/2,c+GAP_MM/2) for c in CAP_X_MM]
    elif kind=='capacitor':gaps=[(-GAP_MM/2,GAP_MM/2)]
    else:gaps=[]
    starts=[x[0],*[b for _,b in gaps]];stops=[*[a for a,_ in gaps],x[-1]]
    for a,b in zip(starts,stops):rods.AddCylinder([a,0.,0.],[b,0.,0.],RI,priority=4)
    shorts=sim.metal('branch_shorts') if selected else None
    if selected:required.append('branch_shorts')
    for k in selected:
        centre=STUB_X_MM[k] if kind=='filter' else 0.;length=dimensions['stub_length_air_mm'][k]
        air.AddCylinder([centre,0.,-length],[centre,0.,0.],RO,priority=2)
        rods.AddCylinder([centre,0.,-length],[centre,0.,0.],RI,priority=4)
        shorts.AddCylinder([centre,0.,-length-.6],[centre,0.,-length],RO,priority=7)
    for k,(a,b) in enumerate(gaps):
        capacitance=dimensions['capacitance_f'][k if kind=='filter' else 0]
        name=f'coupling_C_{k}'
        sim.lumped_capacitor(name,capacitance,[a,-RI,-RI],[b,RI,RI],'x',priority=6)
        required.append(name);elements.append(dict(name=name,value_f=capacitance,start_mm=[a,-float(RI),-float(RI)],stop_mm=[b,float(RI),float(RI)]))
    plane=24. if control=='plane' else 28.;feed=34. if control=='source' else 38.
    for number,sign in ((1,-1),(2,1)):
        sim.coaxial_port(number,[sign*48.,0.,0.],[sign*16.,0.,0.],'x',float(RI),RO,ROS,
            feed_shift=48.-feed,measurement_shift=48.-plane,excite=number==port)
    sim.finalize_coaxial_ports()
    for prop in sim.csx.GetAllProperties():prop.SetColor((128,128,128),alpha=prop.GetFillColor()[3])
    if sim.csx.Update():raise ValueError('invalid native coaxial geometry')
    required += [p.GetName() for p in sim.csx.GetAllProperties() if 'Coaxial port ' in p.GetName() or 'excite_' in p.GetName()]
    return sim,dict(protocol='shunt-coax-filter-v1',n=n,kind=kind,control=control,excited_port=port,
        dimensions=dimensions,mesh_mm={a:v.tolist() for a,v in zip('xyz',(x,y,z))},grid=grid,native_cells=cells,
        declared_dt_s=float(dt),cap_s=cap,max_timesteps=math.ceil(cap/dt),end_db=end,threads=THREADS,
        source_duration_s=excitation.dgauss_duration_s(FREQUENCIES[-1]),elements=elements,
        reference_plane_mm=plane,source_distance_mm=feed,required_properties=required,
        ports=[dict(v) for v in sim.ports],native_probe_geometry=port_probe_geometry(sim),
        expected_voltage_indices={name:record['expected_indices'][name] for record in port_probe_geometry(sim) for name in record['native_u_names']},
        expected_current_indices={name:record['expected_indices'][name] for record in port_probe_geometry(sim) for name in record['native_i_names']},qualified=False,
        scope=dimensions['scope'])


def trace_clock(data, meta, magnetic=False):
    data = np.asarray(data, float); native = meta['native']; dt = native['dt_s']
    stride = int(1/(2*FREQUENCIES[-1]*dt))//OVERSAMPLING
    count = round(native['numerical_time_s']/dt)//stride+1 if stride > 0 else 0
    expected = (np.arange(count)*stride+(.5 if magnetic else 0.))*dt
    if (count < 3 or data.shape != (count, 2) or not np.isfinite(data).all()
            or not np.allclose(data[:, 0], expected, rtol=2e-10, atol=dt*1e-5)):
        raise ValueError('complete native electric/magnetic clock required')


def source_info(path, meta):
    data = np.loadtxt(path, comments='%', ndmin=2); dt = meta['native']['dt_s']
    if data.shape not in ((meta['max_timesteps'], 2), (meta['max_timesteps']+1, 2)) or not np.isfinite(data).all():
        raise ValueError('complete allocated excitation trace required')
    clock = np.arange(len(data))*dt
    if not np.allclose(data[:, 0], clock, rtol=6e-6, atol=dt*1e-6):
        raise ValueError('excitation clock changed')
    tau = 1/(np.sqrt(2)*np.pi*FREQUENCIES[-1]/2.76)
    t0, tau = float(f'{5*tau:.6e}'), float(f'{tau:.6e}')
    q = (clock-t0)/tau
    expected = -float(f'{np.sqrt(2*np.e):.8f}')*q*np.exp(-q*q)
    error = float(abs(data[:, 1]-expected).max()/max(abs(expected).max(), np.finfo(float).tiny))
    if error > 6e-6: raise ValueError('excitation waveform changed')
    return dict(sha256=sha(path), waveform_error_rel=error)


def audit_header(text, meta):
    text_after = text.split(MARKER)[-1]
    grid = re.search(r'FDTD simulation size: (\d+)x(\d+)x(\d+) --> (\S+) FDTD cells', text_after)
    if (text.count(MARKER) != 1 or re.findall(r'openEMS 64bit -- version (\S+)', text_after) != ['v0.37.0-rc3']
            or f'fixed number of threads: {THREADS}' not in text_after or not grid
            or list(map(int, grid.groups()[:3])) != meta['grid']
            # The engine prints the cell count with six significant digits,
            # including scientific notation; dimensions and statistics stay exact.
            or float(grid[4]) != float(format(meta['native_cells'], '.6g'))):
        raise ValueError('native propagation version/thread/grid header mismatch')
    return dict(version='v0.37.0-rc3', threads=THREADS)


def quality(meta):
    native, run, op = meta['native'], meta['run'], meta['operator']
    energies = [run.get(k) for k in ('final_energy_db', 'final_energy_bound_db')]
    energies = [v for v in energies if isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)]
    return {k: bool(v) for k, v in dict(
        grid=native['cells'] == meta['native_cells'] and run.get('grid') == meta['grid'],
        clock=abs(native['dt_s']/meta['declared_dt_s']-1) < 1e-8,
        time=native['timesteps'] > 0 and abs(native['numerical_time_s']/(native['timesteps']*native['dt_s'])-1) < 1e-8,
        source=native['numerical_time_s'] >= meta['source_duration_s'],
        stopping=run.get('converged') is True and run.get('hit_timestep_limit') is False
            and bool(energies) and max(energies) <= meta['end_db']+1e-7,
        operator=0 < meta['declared_dt_s'] <= .8*op['run'].get('timestep_s', 0.),
        geometry=meta['required_usage'] == op['required_usage'] and set(meta['required_usage']) == set(meta['required_properties'])
            and all(v and all(v) for v in meta['required_usage'].values()),
        probes=meta['actual_current_indices'] == meta['expected_current_indices']
            and meta['actual_voltage_indices'] == meta['expected_voltage_indices'],
        header=meta.get('native_header') == dict(version='v0.37.0-rc3', threads=THREADS),
        engine=run.get('threads') == THREADS and run.get('engine') == 'cpu' and run.get('exact_endcriteria') is True,
        tail=meta['terminal_tail_db'] <= LIMITS['terminal_tail_db']).items()}


def admissible(meta, checks, allow_cap=False):
    failed = [k for k, v in checks.items() if not v]
    return not failed or (allow_cap and failed == ['stopping']
        and meta['native']['timesteps'] == meta['max_timesteps']
        and meta['native']['numerical_time_s'] >= meta['cap_s'])


def probe_names(meta):
    return ([name for row in meta['native_probe_geometry'] for name in row['native_u_names']],
            [name for row in meta['native_probe_geometry'] for name in row['native_i_names']])


def waves(v,i):
    v,i=np.asarray(v,complex),np.asarray(i,complex)
    if v.shape!=(2,3,len(FREQUENCIES)) or i.shape!=(2,2,len(FREQUENCIES)):
        raise ValueError('three native voltages and two native currents on both coaxial ports required')
    voltage,current=v[:,1],i.mean(axis=1)
    return (voltage+Z0*current)/(2*np.sqrt(Z0)),(voltage-Z0*current)/(2*np.sqrt(Z0))


def capture(out,n,port,kind='filter',control='ordinary',cap_s=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    sim,meta=build(n,port,kind,control,cap_s)
    meta.update(source=identity(),runtime=runtime_identity())
    sim.fdtd.Write2XML(str(out/'input.xml'));meta['input_sha256']=sha(out/'input.xml')
    plan=json.loads((out.parent/'plan.json').read_text())
    row=next(r for r in plan['ports'] if r['excited_port']==port)
    if (plan['source']!=meta['source'] or plan['runtime']!=meta['runtime']
            or any(row.get(k)!=v for k,v in meta.items() if k not in ('source','runtime'))):
        raise ValueError('native inputs differ from the pre-field plan')
    setup,_=build(n,port,kind,control,cap_s)
    setup.fdtd.SetTimeStep(0.);setup.fdtd.SetNumberOfTimeSteps(1024)
    setup.fdtd.Write2XML(str(out/'operator-input.xml'))
    if sha(out/'operator-input.xml')!=row['operator_input_sha256']:
        raise ValueError('operator input differs from the pre-field plan')
    op=dict(run=setup.run(str(out/'operator'),threads=THREADS,exact=True,echo=True,setup_only=True),
        required_usage=usage(setup,meta['required_properties']),input_sha256=sha(out/'operator-input.xml'))
    save(out/'operator.json',op)
    if (not 0<meta['declared_dt_s']<=.8*op['run'].get('timestep_s',0.)
            or op['run'].get('grid')!=meta['grid'] or set(op['required_usage'])!=set(meta['required_properties'])
            or not all(v and all(v) for v in op['required_usage'].values())):
        raise ValueError('operator CFL/material-usage preflight failed')
    if identity()!=meta['source'] or runtime_identity()!=meta['runtime']:
        raise ValueError('source or runtime changed before fields')
    print(MARKER,flush=True)
    sim.run(str(out/'raw'),threads=THREADS,exact=True,echo=True,dump_statistics=True)
    stats=np.loadtxt(out/'raw/openEMS_stats.txt',comments='%')
    meta.update(native=dict(cells=int(stats[0]),dt_s=float(stats[1]),timesteps=int(stats[2]),
        numerical_time_s=float(stats[3]),iteration_wall_s=float(stats[4])),run=sim.run_stats,operator=op,
        required_usage=usage(sim,meta['required_properties']))
    vn,cn=probe_names(meta);values=[];tails=[];indices={};raw_hashes={}
    for name in vn+cn:
        path=out/'raw'/name;trace=np.loadtxt(path,comments='%',ndmin=2)
        trace_clock(trace,meta,name in cn)
        values.append(np.asarray(UI_data([name],str(out/'raw'),FREQUENCIES).ui_f_val)[0])
        mask=trace[:,0]>=meta['native']['numerical_time_s']-.5e-9
        tails.append(float(10*np.log10(max(np.mean(trace[mask,1]**2),np.finfo(float).tiny)
            /max(np.max(trace[:,1]**2),np.finfo(float).tiny))))
        raw_hashes[name]=sha(path)
        indices[name]=[list(map(int,v)) for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]',
            '\n'.join(path.read_text().splitlines()[:3]))]
    voltage=np.asarray(values[:6]).reshape(2,3,-1);current=np.asarray(values[6:]).reshape(2,2,-1)
    aa,bb=waves(voltage,current)
    for p,native in enumerate(sim._port_objs):
        native.CalcPort(str(out/'raw'),FREQUENCIES,ref_impedance=Z0)
        np.testing.assert_allclose(native.uf_inc/np.sqrt(Z0),aa[p],rtol=3e-13,atol=1e-23)
        np.testing.assert_allclose(native.uf_ref/np.sqrt(Z0),bb[p],rtol=3e-13,atol=1e-23)
    meta.update(actual_current_indices={k:indices[k] for k in cn},actual_voltage_indices={k:indices[k] for k in vn},
        terminal_tail_db=max(tails),raw_sha256=raw_hashes,source_trace=source_info(out/'raw/et',meta))
    np.savez_compressed(out/'data.npz',frequency_hz=FREQUENCIES,v=voltage,i=current,a=aa,b=bb)
    if identity()!=meta['source'] or runtime_identity()!=meta['runtime']:
        raise ValueError('source or runtime changed during fields')
    meta.update(data_sha256=sha(out/'data.npz'),operator_sha256=sha(out/'operator.json'))
    save(out/'report.json',meta)
    return meta


def read(out, allow_cap=False):
    out = Path(out); meta = json.loads((out/'report.json').read_text())
    holder = build(meta['n'], meta['excited_port'], meta['kind'], meta['control'], meta['cap_s'])
    if (any(meta.get(k) != v for k, v in holder[1].items())
            or meta['source'] != identity() or meta['runtime'] != runtime_identity()):
        raise ValueError('stored model/source/runtime differs')
    checks = quality(meta)
    if not admissible(meta, checks, allow_cap): raise ValueError(f'rejected source: {checks}')
    for name, expected in [('input.xml', meta['input_sha256']), ('operator-input.xml', meta['operator']['input_sha256']),
            ('operator.json', meta['operator_sha256']), ('data.npz', meta['data_sha256'])]:
        if sha(out/name) != expected: raise ValueError('stored input/operator/data changed')
    if json.loads((out/'operator.json').read_text()) != meta['operator']:
        raise ValueError('operator admission record changed')
    plan = json.loads((out.parent/'plan.json').read_text()); row = next(r for r in plan['ports'] if r['excited_port'] == meta['excited_port'])
    if (plan['source'] != identity() or plan['runtime'] != runtime_identity()
            or any(row.get(k) != v for k, v in holder[1].items())
            or row['input_sha256'] != meta['input_sha256']
            or row['operator_input_sha256'] != meta['operator']['input_sha256']):
        raise ValueError('stored plan changed')
    log = out.parent/f'port{meta["excited_port"]}.log'
    if sha(log) != meta['native_log_sha256'] or audit_header(log.read_text(), meta) != meta['native_header']:
        raise ValueError('native log/header changed')
    receipt = json.loads((out.parent/f'port{meta["excited_port"]}.worker.json').read_text())
    if (receipt['exit_code'] != 0 or receipt['failure'] is not None or receipt['deadline_seconds'] != 1800.
            or max(receipt['wall_seconds'], receipt['monotonic_seconds']) > 1800.):
        raise ValueError('worker did not complete within its owned deadline')
    stats = np.loadtxt(out/'raw/openEMS_stats.txt', comments='%')
    if dict(cells=int(stats[0]), dt_s=float(stats[1]), timesteps=int(stats[2]),
            numerical_time_s=float(stats[3]), iteration_wall_s=float(stats[4])) != meta['native']:
        raise ValueError('native statistics changed')
    vn, cn = probe_names(meta); values = []; tails = []
    for name in vn+cn:
        path = out/'raw'/name
        if sha(path) != meta['raw_sha256'][name]: raise ValueError('raw probe changed')
        trace = np.loadtxt(path, comments='%', ndmin=2); trace_clock(trace, meta, name in cn)
        clock, value = trace[:, 0], trace[:, 1]
        values.append(np.asarray([2*(clock[1]-clock[0])*np.sum(value*np.exp(-2j*np.pi*f*clock)) for f in FREQUENCIES]))
        mask = clock >= meta['native']['numerical_time_s']-.5e-9
        tails.append(float(10*np.log10(max(np.mean(value[mask]**2), np.finfo(float).tiny)
            / max(np.max(value**2), np.finfo(float).tiny))))
        indices = [list(map(int, v)) for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]', '\n'.join(path.read_text().splitlines()[:3]))]
        expected = meta['expected_current_indices'] if name in cn else meta['expected_voltage_indices']
        if indices != expected[name]: raise ValueError('probe indices changed')
    with np.load(out/'data.npz') as stored: data = dict(stored)
    if not np.array_equal(data['frequency_hz'], FREQUENCIES): raise ValueError('frequency grid changed')
    np.testing.assert_allclose(np.asarray(values[:6]).reshape(2, 3, -1), data['v'], rtol=3e-13, atol=1e-23)
    np.testing.assert_allclose(np.asarray(values[6:]).reshape(2, 2, -1), data['i'], rtol=3e-13, atol=1e-23)
    if max(tails) != meta['terminal_tail_db'] or source_info(out/'raw/et', meta) != meta['source_trace']:
        raise ValueError('source or tail record changed')
    aa,bb=waves(data['v'],data['i'])
    np.testing.assert_allclose(aa,data['a'],rtol=3e-13,atol=1e-23)
    np.testing.assert_allclose(bb,data['b'],rtol=3e-13,atol=1e-23)
    return meta, data, checks


def scattering(v, i):
    v, i = np.asarray(v, complex), np.asarray(i, complex)
    if (v.shape != (2, 2, len(FREQUENCIES)) or i.shape != v.shape
            or not np.isfinite(v).all() or not np.isfinite(i).all()):
        raise ValueError('two complete finite source columns required')
    a, b = (v+Z0*i)/(2*np.sqrt(Z0)), (v-Z0*i)/(2*np.sqrt(Z0))
    singular = np.linalg.svd(a.transpose(2, 1, 0), compute_uv=False)
    condition = float(np.max(singular[:, 0]/np.maximum(singular[:, -1], np.finfo(float).tiny)))
    floor = float(singular[:, -1].min()/max(singular[:, 0].max(), np.finfo(float).tiny))
    if condition > LIMITS['incident_condition'] or floor < LIMITS['incident_floor']:
        raise ValueError('ill-conditioned incident matrix')
    return multiport.assemble_s(a, b, [1, 2], 2)


def difference(a, b):
    mask = abs(b[:, 1, 0]) >= .01
    return dict(s_abs=float(abs(a-b).max()), phase_deg=float(abs(np.angle(a[mask, 1, 0]/b[mask, 1, 0], deg=True)).max()))


def matrix(root,allow_cap=False):
    root=Path(root);rows=[read(root/f'port{p}',allow_cap) for p in (1,2)]
    metas=[m for m,_,_ in rows]
    if any(m['excited_port']!=p or any(m[k]!=metas[0][k] for k in ('n','kind','control','cap_s','mesh_mm','reference_plane_mm'))
            for p,m in enumerate(metas,1)):
        raise ValueError('same model and distinct source columns required')
    actual=scattering(np.asarray([d['v'][:,1] for _,d,_ in rows]),np.asarray([d['i'].mean(axis=1) for _,d,_ in rows]))
    meta=metas[0];target=reference(kind=meta['kind'],plane_mm=meta['reference_plane_mm'])
    result=dict(n=meta['n'],kind=meta['kind'],control=meta['control'],**difference(actual,target),
        native_complete=all(all(q.values()) for _,_,q in rows),native_quality=[q for _,_,q in rows],
        reciprocity_abs=float(abs(actual-actual.transpose(0,2,1)).max()),
        singular_power_max=float((np.linalg.svd(actual,compute_uv=False)[:,0]**2).max()),
        qualified=False,scope=meta['scope'],numerical_source=identity(),reference='Own circuit with declared finite connecting lines')
    result['measurement_passes']=all(result[k]<=LIMITS[k] for k in ('reciprocity_abs','singular_power_max'))
    result['point_target_passes']=result['s_abs']<=LIMITS['s_abs'] and result['phase_deg']<=LIMITS['phase_deg']
    ideal=reference(finite=False) if meta['kind']=='filter' else None
    prototype=prototype_loss() if ideal is not None else None
    loss=lambda value:float(-20*np.log10(abs(value))) if abs(value)>0 else None
    result['samples']=[dict(f_hz=float(FREQUENCIES[k]),native_loss_db=loss(actual[k,1,0]),
        finite_reference_loss_db=loss(target[k,1,0]),original_circuit_loss_db=loss(ideal[k,1,0]) if ideal is not None else None,
        prototype_loss_db=float(prototype[k]) if prototype is not None else None) for k in (100,150,200,250,300)]
    prefix='cap-diagnostic-' if allow_cap else ''
    report,arrays=root/(prefix+'comparison.json'),root/(prefix+'matrix.npz')
    if report.exists() or arrays.exists():
        if not report.exists() or not arrays.exists() or json.loads(report.read_text())!=result:
            raise ValueError('stored comparison changed')
        with np.load(arrays) as old:
            for k,v in dict(frequency_hz=FREQUENCIES,s=actual,reference=target).items():np.testing.assert_array_equal(old[k],v)
    else:
        save(report,result);np.savez_compressed(arrays,frequency_hz=FREQUENCIES,s=actual,reference=target)
    return actual,result
