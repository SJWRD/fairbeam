"""Own homogeneous thin-PEC baseline for Example 7.7 (p. 355).

The finite copper/lossy realization is a separate, as yet unqualified case.
No gallery model, material implementation or saved-project format is changed.
Cohn's thin centered-strip conformal map is described in the primary note:
https://www.rogerscorp.cn/-/media/project/rogerscorp/documents/advanced-electronics-solutions/english/electrical-design-data/design-equations-for-broadside-and-edgewise-stripline.pdf
"""
import math
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import numpy as np
import CSXCAD
from openEMS import ports as native_ports
from openEMS.physical_constants import Z0 as ETA0

from fairbeam import Simulation, excitation, multiport
from tests.coax_resonator_fixture import save, sha, runtime_identity as base_runtime

ROOT = Path(__file__).resolve().parents[2]
C0, B, ER, F0, Z0, COUPLING = 299792458., 3.2, 2.2, 3e9, 50., .1
FREQUENCIES = np.linspace(1e9, 5e9, 201)
MESHES, PILOT_MESHES, CONTROL_MESH, THREADS = (6, 8, 10), (4,), 6, 4
CONTROLS = ("ordinary", "boundary", "sidewall", "guard", "time", "dt")
CAP_S, END_DB, DT_FACTOR = 6e-9, -80., .5
PROTOCOL = "coupled-stripline-v3-transfer-postprocessing"
NATIVE_STEP_RATIO_MAX = .97
PROPAGATION_MARKER = "FAIRBEAM-COUPLED-STRIPLINE-PROPAGATION-BEGIN"
LIMITS = dict(s_target_abs=.005, phase_target_deg=.5, beta_target_rel=.005,
              impedance_target_rel=.01, s_mesh_abs=.0025, phase_mesh_deg=.2,
              s_control_abs=.001, phase_control_deg=.1, contour_s_abs=.001,
              recurrence_residual=.001, incident_condition=30., incident_floor=.02,
              reciprocity_abs=.001, singular_power_max=1.005,
              transfer_det_abs=.001, transfer_symmetry_abs=.001, transfer_fit_rel=.001,
              transfer_condition_max=30., transfer_floor_min=.02)
SCOPE = ("Homogeneous centered thin PEC coupled strips; internal TEM planes, "
         "two ground halves, four-source inverse; finite sidewalls explicitly controlled. "
         "No finite-copper or broadband dielectric-loss certificate.")


def identity():
    names = ("tests/coupled_stripline_fixture.py", "tests/test_coupled_stripline_fixture.py",
             "tests/coax_resonator_fixture.py", "tests/test_coax_resonator.py",
             "fairbeam/simulation.py", "fairbeam/excitation.py", "fairbeam/multiport.py",
             "fairbeam/procutil.py")
    return {name: sha(ROOT/"python"/name) for name in names}


def runtime_identity():
    value=base_runtime()
    value.update(python=sys.version,executable=sys.executable,numpy=np.__version__,
                 port_helpers_sha256=sha(native_ports.__file__))
    folder=Path(CSXCAD.__file__).parent
    value['csxcad_bindings']={str(p.resolve()):sha(p) for p in
        sorted(folder.glob('*.pyd'))+sorted(folder.glob('*.so'))+[Path(CSXCAD.__file__)]}
    if os.name=='nt':
        prefix=os.environ.get('OPENEMS_INSTALL_PATH')
        if not prefix:
            raise ValueError('explicit bundled runtime required')
        value['dependencies']={name:sha(Path(prefix)/name) for name in ('fparser.dll','nf2ff.dll')}
    return value


def agm(a, b):
    for _ in range(32):
        a, b = (a+b)/2, np.sqrt(a*b)
    return a


def mode_impedance(k):
    k = np.asarray(k, float)
    if not np.isfinite(k).all() or np.any((k<=0)|(k>=1)):
        raise ValueError("elliptic modulus strictly between zero and one required")
    return ETA0/(4*np.sqrt(ER))*agm(1., np.sqrt(1-k*k))/agm(1., k)


def impedances(width, gap):
    if not np.isfinite([width,gap]).all() or min(width,gap)<=0:
        raise ValueError("positive finite strip width and gap required")
    a, b = np.tanh(np.pi*width/(2*B)), np.tanh(np.pi*(width+gap)/(2*B))
    return mode_impedance(np.array([a*b,a/b]))


def geometry():
    target = Z0*np.array([np.sqrt((1+COUPLING)/(1-COUPLING)),
                          np.sqrt((1-COUPLING)/(1+COUPLING))])
    roots=[]
    for z in target:
        lo, hi = 1e-12, 1-1e-12
        for _ in range(64):
            mid=(lo+hi)/2
            if mode_impedance(mid)>z:
                lo=mid
            else:
                hi=mid
        roots.append((lo+hi)/2)
    ke,ko=roots
    u,v=np.arctanh(np.sqrt(ke*ko)),np.arctanh(np.sqrt(ke/ko))
    width,gap=2*B*u/np.pi,2*B*(v-u)/np.pi
    return dict(width_mm=float(width),gap_mm=float(gap),b_mm=B,epsilon_r=ER,
                length_mm=C0/(4*F0*np.sqrt(ER))*1e3,
                modal_impedances_ohm=target.tolist(),metal="zero-thickness PEC",
                loss_tangent=0.,physical_copper_thickness_mm=.0508,
                requested_loss_tangent=.05,scope=SCOPE)


def reference(freq=FREQUENCIES):
    """Stamp separate even/odd lines, then transform back to conductor ports.

    Port order is left A, right A, right B, left B. The coupled port is 4;
    the isolated port is 3. No measured fields enter this reference.
    """
    freq=np.asarray(freq,float)
    if freq.ndim!=1 or not freq.size or not np.isfinite(freq).all() or np.any((freq<1e9)|(freq>5e9)):
        raise ValueError("finite frequencies in the declared 1..5 GHz band required")
    theta=np.pi*freq/(2*F0)
    q=np.array([[1,1],[1,-1]])/np.sqrt(2)
    y=np.zeros((len(freq),4,4),complex)
    for mode,z in enumerate(geometry()["modal_impedances_ohm"]):
        outer=np.outer(q[:,mode],q[:,mode])
        for side,other in (((0,3),(1,2)),((1,2),(0,3))):
            for a in range(2):
                for b in range(2):
                    y[:,side[a],side[b]] += -1j/np.tan(theta)/z*outer[a,b]
                    y[:,side[a],other[b]] += 1j/np.sin(theta)/z*outer[a,b]
    return 2*np.linalg.inv(np.eye(4)+Z0*y)-np.eye(4)


def graded(span, first):
    values,step=[0.],first
    while values[-1]<span:
        values.append(min(span,values[-1]+step))
        step=min(B/2,step*1.25)
    if len(values)>2 and values[-1]-values[-2]<first/2:
        values[-2]=(values[-3]+values[-1])/2
    return np.array(values)


def transverse_mesh(n, g):
    delta=min(g["width_mm"],g["gap_mm"])/n
    center=(g["width_mm"]+g["gap_mm"])/2
    anchors=[0.,-center,center]
    for c in (-center,center):
        lo,hi=c-g["width_mm"]/2,c+g["width_mm"]/2
        anchors.extend((lo-2*delta/3,lo+delta/3,hi-delta/3,hi+2*delta/3))
    anchors=np.unique(anchors)
    pieces=[np.linspace(a,b,max(1,math.ceil((b-a)/delta-1e-12))+1)[:-1]
            for a,b in zip(anchors,anchors[1:])]
    interior=np.r_[np.concatenate(pieces),anchors[-1]]
    outward=graded(6*B-anchors[-1],delta)
    y=np.r_[anchors[0]-outward[:0:-1],interior,anchors[-1]+outward[1:]]
    positive=graded(B/2,delta/3)
    z=np.r_[-positive[:0:-1],positive]
    return y,z,delta


def build(n, port, *, control="ordinary"):
    if (isinstance(n,bool) or n not in MESHES+PILOT_MESHES or not isinstance(n,int)
            or isinstance(port,bool) or not isinstance(port,int) or port not in range(1,5)
            or control not in CONTROLS):
        raise ValueError("declared integer mesh, source and independent control required")
    g=geometry();length=g["length_mm"];dx=length/(8*n)
    extent=20*n if control in ("guard","boundary") else 16*n
    ticks=np.arange(-extent,extent+1)
    x=ticks*dx
    for k in (-12*n,-8*n,-4*n,4*n,8*n,12*n):
        x[k+extent]=k/(8*n)*length
    y,z,delta=transverse_mesh(n,g)
    if control=="sidewall":
        step=y[-1]-y[-2]
        count=math.ceil(2*B/step)
        y=np.r_[y[0]-step*np.arange(count,0,-1),y,y[-1]+step*np.arange(1,count+1)]
    end_db=-90. if control=="time" else END_DB
    cap=9e-9 if control=="time" else CAP_S
    factor=.35 if control=="dt" else DT_FACTOR
    sim=Simulation(FREQUENCIES[0],FREQUENCIES[-1],excitation="dgauss",end_criteria_db=end_db,
                   boundaries=["PML_8","PML_8","PEC","PEC","PEC","PEC"])
    axes=dict(x=x,y=y,z=z)
    for a,lines in axes.items():
        sim.mesh.AddLine(a,lines)
    sim.dielectric("homogeneous",ER).AddBox([x[0],y[0],z[0]],[x[-1],y[-1],z[-1]],priority=0)
    ground=sim.metal("grounds")
    for height in (-B/2,B/2):
        ground.AddBox([x[0],y[0],height],[x[-1],y[-1],height],priority=10)
    metal=sim.metal("strips")
    center=(g["width_mm"]+g["gap_mm"])/2
    for c in (-center,center):
        metal.AddBox([x[0],c-g["width_mm"]/2,0.],[x[-1],c+g["width_mm"]/2,0.],priority=10)
    definitions=((1,-1,center),(2,1,center),(3,1,-center),(4,-1,-center))
    source_length=1.5*length if control=="guard" else length
    contours=[]
    for desired in (B/4,3*B/8):
        j=int(np.searchsorted(z,desired))
        contours.append(float((z[j-1]+z[j])/2))
    if contours[0]==contours[1]:
        raise ValueError("independent Ampere contours collapsed")
    for number,sx,c in definitions:
        source=sx*source_length
        lo,hi=c-g["width_mm"]/2,c+g["width_mm"]/2
        sim.lumped_port(number,Z0,[source,lo,-B/2],[source,hi,0.],"z",excite=number==port,
            group={"connection":"parallel","members":[{"start":[source,lo,0.],
                   "stop":[source,hi,B/2],"direction":"z","polarity":-1}]})
        plane_index=sx*4*n+extent
        for j,plane in enumerate(x[plane_index-1:plane_index+2]):
            for half,a,b,weight in ((0,-B/2,0.,-.5),(1,0.,B/2,.5)):
                sim.csx.AddProbe(f"v{number}_{j}_{half}",p_type=0,weight=weight).AddBox(
                    [plane,c,a],[plane,c,b])
        for j,index in enumerate((plane_index-1,plane_index)):
            plane=(x[index]+x[index+1])/2
            for loop,height in enumerate(contours):
                sim.csx.AddProbe(f"i{number}_{j}_{loop}",p_type=1,norm_dir=0).AddBox(
                    [plane,lo-delta/6,-height],[plane,hi+delta/6,height])
    dt=factor/(C0*np.sqrt(sum((np.diff(v).min()*sim.unit)**-2 for v in axes.values())))
    steps=math.ceil(cap/dt)
    sim.max_timesteps=steps
    sim.fdtd.SetTimeStep(float(dt));sim.fdtd.SetNumberOfTimeSteps(steps)
    sim.fdtd.SetOverSampling(4)
    if sim.csx.Update():
        raise ValueError("invalid native geometry")
    meta=dict(protocol=PROTOCOL,n=n,excited_port=port,control=control,geometry=g,ports=sim.ports,
              mesh_mm={a:v.tolist() for a,v in axes.items()},native_lines=[len(v) for v in axes.values()],
              native_cells=int(np.prod([len(v) for v in axes.values()])),declared_dt_s=float(dt),
              max_timesteps=steps,cap_s=cap,end_db=end_db,dt_factor=factor,dx_mm=dx,
              voltage_planes_mm=[-length/2,length/2],ampere_heights_mm=contours,
              source_distance_mm=source_length-length/2,source_duration_s=excitation.dgauss_duration_s(FREQUENCIES[-1]),
              threads=THREADS,scope=SCOPE)
    return sim,meta


def assemble(v, i):
    a,b=(v+Z0*i)/(2*np.sqrt(Z0)),(v-Z0*i)/(2*np.sqrt(Z0))
    incoming=a.transpose(2,1,0)
    singular=np.linalg.svd(incoming,compute_uv=False)
    floor=float(singular[:,-1].min()/max(singular[:,0].max(),np.finfo(float).tiny))
    condition=float(np.max(singular[:,0]/np.maximum(singular[:,-1],np.finfo(float).tiny)))
    if condition>LIMITS["incident_condition"] or floor<LIMITS["incident_floor"]:
        raise ValueError("unexcited or ill-conditioned incoming wave matrix")
    result=multiport.assemble_s(a,b,[1,2,3,4],4)
    np.testing.assert_allclose(result,b.transpose(2,1,0)@np.linalg.inv(incoming),rtol=0,atol=1e-12)
    return result,dict(incident_condition=condition,incident_floor=floor)


def extract(v,i,dx_m):
    """Infer gamma from both measured end states, not a tiny local difference.

    The same unknown spatial I-average factor at both ends is a similarity
    transform: it leaves the transfer trace unchanged. No analytical gamma
    is substituted. Determinant, symmetry and four-source fit are checked;
    signed attenuation is retained. Local gamma remains diagnostic only.
    """
    v,i=np.asarray(v,complex),np.asarray(i,complex)
    if (v.shape!=(4,4,3,len(FREQUENCIES)) or i.shape!=(4,4,2,2,len(FREQUENCIES))
            or not np.isfinite(v).all() or not np.isfinite(i).all()
            or not np.isfinite(dx_m) or dx_m<=0):
        raise ValueError('complete finite four-source waves and positive spacing required')
    length=geometry()['length_mm']*1e-3
    q=np.array([[1,1],[1,-1]])/np.sqrt(2)
    idsides=((0,3),(1,2))
    vm=[np.einsum('mc,ecjf->emjf',q,np.take(v,ids,axis=1)) for ids in idsides]
    local_gamma=[]
    for field in vm:
        center=field[:,:,1];denom=2*np.sum(abs(center)**2,axis=0)
        scale=np.max(np.sum(abs(field)**2,axis=0),axis=1)
        if np.any(denom<.01*scale) or np.any(scale<=0):
            raise ValueError('standing-wave node or missing modal excitation')
        r=np.sum(np.conj(center)*(field[:,:,0]+field[:,:,2]),axis=0)/denom
        gd=np.arccosh(r);local_gamma.append(np.where(gd.imag<0,-gd,gd)/dx_m)
    corrected=np.zeros((2,4,4,len(FREQUENCIES)),complex)
    raw=np.zeros_like(corrected)
    gamma_all=[];transfer_z=[];local_z=np.zeros((2,2,2,len(FREQUENCIES)),complex)
    maxdet,maxsym,maxres,maxcond,minfloor,recurrence=0.,0.,0.,0.,1.,0.
    for loop in (0,1):
        im=[np.einsum('mc,ecjf->emjf',q,np.take(i,ids,axis=1)[:,:,:,loop,:]).mean(axis=2)
            for ids in idsides]
        gamma,zchar=[],[]
        for mode in (0,1):
            states=[np.stack((vv[:,mode,1]/np.sqrt(Z0),ii[:,mode]*np.sqrt(Z0))).transpose(2,0,1)
                    for vv,ii in zip(vm,im)]
            left,right=states
            for state in states:
                singular=np.linalg.svd(state,compute_uv=False)
                floor=float(singular[:,-1].min()/max(singular[:,0].max(),np.finfo(float).tiny))
                condition=float(np.max(singular[:,0]/np.maximum(singular[:,-1],np.finfo(float).tiny)))
                if condition>LIMITS['transfer_condition_max'] or floor<LIMITS['transfer_floor_min']:
                    raise ValueError('node or unexcited/ill-conditioned transfer state')
                maxcond=max(maxcond,condition);minfloor=min(minfloor,floor)
            adjoint=right.conj().transpose(0,2,1)
            transfer=(left@adjoint)@np.linalg.inv(right@adjoint)
            residual=np.linalg.norm(left-transfer@right,axis=(1,2))/np.linalg.norm(left,axis=(1,2))
            maxres=max(maxres,float(residual.max()))
            maxdet=max(maxdet,float(abs(np.linalg.det(transfer)-1).max()))
            maxsym=max(maxsym,float(abs(transfer[:,0,0]-transfer[:,1,1]).max()))
            # No determinant normalization or attenuation clipping. Uniform
            # reciprocal/symmetric-line assumptions have independent gates.
            gl=np.arccosh((transfer[:,0,0]+transfer[:,1,1])/2)
            gl=np.where(gl.imag<0,-gl,gl)
            if np.any(abs(np.sinh(gl))<1e-5):
                raise ValueError('singular propagation branch')
            g=gl/length;half=np.cosh(g*dx_m/2)
            if not np.isfinite(g).all() or np.any(abs(half)<.5):
                raise ValueError('invalid spatial current interpolation')
            z=Z0*np.sqrt(transfer[:,0,1]/transfer[:,1,0])*half
            z=np.where(z.real<0,-z,z)
            if not np.isfinite(z).all():
                raise ValueError('singular modal impedance')
            gamma.append(g);zchar.append(z)
        gamma=np.array(gamma);half=np.cosh(gamma*dx_m/2)
        gamma_all.append(gamma);transfer_z.append(zchar)
        for side,(ids,sign) in enumerate(((idsides[0],1),(idsides[1],-1))):
            central_i=im[side]/half
            raw[loop][:,ids]=sign*np.einsum('cm,emf->ecf',q.T,im[side])
            corrected[loop][:,ids]=sign*np.einsum('cm,emf->ecf',q.T,central_i)
            field=vm[side]
            predicted=2*np.cosh(gamma*dx_m)*field[:,:,1]
            recurrence=max(recurrence,float(abs(field[:,:,0]+field[:,:,2]-predicted).max()/abs(field).max()))
            implied=-(field[:,:,2]-field[:,:,0])/(2*np.sinh(gamma*dx_m))
            power=np.sum(abs(central_i)**2,axis=0)
            if np.any(power<=0):
                raise ValueError('zero modal current')
            local_z[side,loop]=np.sum(np.conj(central_i)*implied,axis=0)/power
    spectra,quality=[],[]
    for loop in (0,1):
        s,row=assemble(v[:,:,1],corrected[loop]);spectra.append(s);quality.append(row)
    raw_s,_=assemble(v[:,:,1],raw[0])
    transfer_z=np.asarray(transfer_z)
    diagnostics=dict(contour_s_abs=float(abs(spectra[0]-spectra[1]).max()),
        staggering_s_abs=float(abs(spectra[0]-raw_s).max()),wave_quality=quality,
        recurrence_residual=recurrence,gamma_per_m=np.array(local_gamma),
        gamma_transfer_per_m=np.array(gamma_all),modal_z_ohm=local_z,transfer_z_ohm=transfer_z,
        transfer_det_abs=maxdet,transfer_symmetry_abs=maxsym,transfer_fit_rel=maxres,
        transfer_condition_max=maxcond,transfer_floor_min=minfloor,
        local_vs_transfer_z_rel=float(abs(local_z/transfer_z[None]-1).max()))
    return spectra[0],diagnostics


def phase(s):
    s=np.asarray(s,complex)
    if s.shape!=(len(FREQUENCIES),4,4) or not np.isfinite(s).all() or np.any(abs(s[:,(1,3),0])<.01):
        raise ValueError('finite non-null through and coupled waves required')
    return np.angle(s[:,3,0]*np.conj(s[:,1,0]),deg=True)


def phase_change(a,b):
    return float(np.max(abs((phase(a)-phase(b)+180)%360-180)))


def native_bound_ok(meta):
    record=meta.get('operator_preflight',{});run=record.get('run',{})
    bound=run.get('timestep_s',0.)
    return bool(isinstance(bound,(float,int)) and np.isfinite(bound) and bound>0
        and record.get('setup_only') is True and record.get('ratio_limit')==NATIVE_STEP_RATIO_MAX
        and record.get('planned_dt_s')==meta['declared_dt_s']
        and meta['declared_dt_s']/bound<=NATIVE_STEP_RATIO_MAX
        and run.get('grid')==meta['native_lines'] and run.get('threads')==THREADS
        and run.get('engine')=='cpu' and run.get('exact_endcriteria') is True
        and run.get('converged') is False and run.get('energy_trace')==[] and 'timesteps' not in run)


def operator_preflight(out,n,port,control='ordinary'):
    out=Path(out);sim,meta=build(n,port,control=control)
    sim.fdtd.SetTimeStep(0.)
    sim.fdtd.Write2XML(str(out/'operator-input.xml'))
    run=sim.run(str(out/'operator'),threads=THREADS,exact=True,echo=True,setup_only=True)
    record=dict(setup_only=True,ratio_limit=NATIVE_STEP_RATIO_MAX,planned_dt_s=meta['declared_dt_s'],
                input_sha256=sha(out/'operator-input.xml'),run=run)
    record['passes']=native_bound_ok({**meta,'operator_preflight':record})
    save(out/'operator.json',record)
    if not record['passes']:
        raise ValueError('planned step exceeds the setup-only native stability bound')
    return record


def trace_clock(data,meta):
    data=np.asarray(data,float)
    native=meta['native'];stride=int(1/(2*FREQUENCIES[-1]*native['dt_s']*4))
    if (data.ndim!=2 or len(data)<3 or data.shape[1]<2 or not np.isfinite(data).all() or stride<1
            or np.any(np.diff(data[:,0])<=0)
            or not np.allclose(np.diff(data[:,0]),stride*native['dt_s'],rtol=2e-5,atol=native['dt_s']*1e-5)
            or data[-1,0]>native['numerical_time_s']+stride*native['dt_s']):
        raise ValueError('finite native traces on the declared sampling clock required')
    return dict(samples=len(data),first_s=float(data[0,0]),last_s=float(data[-1,0]),stride_steps=stride)


def source_trace_info(path,meta):
    # Native et writes the allocated input, not the decimated field-probe clock
    # or the energy. Its timestamp text has only six significant digits.
    data=np.loadtxt(path,comments='%',ndmin=2)
    dt=meta['native']['dt_s']
    if (data.ndim!=2 or data.shape[1]!=2 or not np.isfinite(data).all()
            or len(data) not in (meta['max_timesteps'],meta['max_timesteps']+1)):
        raise ValueError('complete finite allocated excitation trace required')
    if not np.allclose(data[:,0],np.arange(len(data))*dt,rtol=6e-6,atol=dt*1e-6):
        raise ValueError('excitation timestamps differ beyond native text precision')
    return dict(sha256=sha(path),samples=len(data),last_s=float(data[-1,0]),timestamp_print_rtol=6e-6)


def quality(meta):
    run,native,header=meta['run'],meta['native'],meta.get('native_header',{})
    return {k:bool(v) for k,v in dict(
        grid=native['cells']==meta['native_cells'] and run.get('grid')==meta['native_lines'],
        clock=abs(native['dt_s']/meta['declared_dt_s']-1)<1e-8,
        operator=native_bound_ok(meta),
        time=native['timesteps']>0 and abs(native['numerical_time_s']/(native['dt_s']*native['timesteps'])-1)<1e-8,
        header=header.get('version')=='v0.37.0-rc3' and header.get('threads')==THREADS
            and header.get('nyquist_interval')==int(1/(2*FREQUENCIES[-1]*native['dt_s']))
            and header.get('excitation_timesteps')==meta['max_timesteps'],
        source=native['numerical_time_s']>meta['source_duration_s'],
        energy=run.get('converged') and run.get('exact_endcriteria') and not run.get('hit_timestep_limit')
            and run.get('threads')==THREADS and native['timesteps']<meta['max_timesteps']).items()}


def probe_names():
    return ([f'v{p}_{j}_{h}' for p in range(1,5) for j in range(3) for h in range(2)],
            [f'i{p}_{j}_{loop}' for p in range(1,5) for j in range(2) for loop in range(2)])


def probe_spectra(raw):
    vn,cn=probe_names()
    v=np.asarray(native_ports.UI_data(vn,str(raw),FREQUENCIES).ui_f_val).reshape(4,3,2,-1).sum(axis=2)
    current=np.asarray(native_ports.UI_data(cn,str(raw),FREQUENCIES).ui_f_val).reshape(4,2,2,-1)
    return v,current


def acquire(out,n,port,control='ordinary'):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    sim,meta=build(n,port,control=control)
    source,runtime=identity(),runtime_identity()
    sim.fdtd.Write2XML(str(out/'input.xml'))
    meta.update(source_sha256=source,runtime=runtime,limits=LIMITS,input_sha256=sha(out/'input.xml'),
                started_utc=datetime.now(timezone.utc).isoformat())
    save(out/'declared.json',meta)
    meta['operator_preflight']=operator_preflight(out,n,port,control)
    meta['operator_sha256']=sha(out/'operator.json')
    if source!=identity() or runtime!=runtime_identity():
        raise RuntimeError('source/runtime changed during native setup')
    save(out/'declared.json',meta)
    print(PROPAGATION_MARKER,flush=True)
    sim.run(str(out/'raw'),threads=THREADS,exact=True,echo=True,dump_statistics=True)
    stats=np.loadtxt(out/'raw/openEMS_stats.txt',comments='%')
    meta.update(native=dict(cells=int(stats[0]),dt_s=float(stats[1]),timesteps=int(stats[2]),
                numerical_time_s=float(stats[3]),iteration_wall_s=float(stats[4])),run=sim.run_stats)
    vn,cn=probe_names()
    v,current=probe_spectra(out/'raw')
    traces={name:dict(sha256=sha(out/'raw'/name),**trace_clock(
        np.loadtxt(out/'raw'/name,comments='%',ndmin=2),meta)) for name in vn+cn}
    np.savez_compressed(out/'waves.npz',frequency_hz=FREQUENCIES,v=v,i=current)
    if source!=identity() or runtime!=runtime_identity():
        raise RuntimeError('source/runtime changed during acquisition')
    meta.update(waves_sha256=sha(out/'waves.npz'),raw_traces=traces,source_trace=source_trace_info(out/'raw/et',meta),
                completed_utc=datetime.now(timezone.utc).isoformat())
    save(out/'report.json',meta)
    return meta


def read(out):
    out=Path(out);meta=json.loads((out/'report.json').read_text(encoding='utf-8'))
    if meta['source_sha256']!=identity() or meta['runtime']!=runtime_identity() or meta['limits']!=LIMITS:
        raise ValueError('source/runtime/limits changed; preserve separate acquisition epochs')
    for name,key in (('input.xml','input_sha256'),('waves.npz','waves_sha256'),('operator.json','operator_sha256')):
        if sha(out/name)!=meta[key]:
            raise ValueError('changed native input, operator receipt or wave data')
    record=json.loads((out/'operator.json').read_text())
    if record!=meta['operator_preflight'] or sha(out/'operator-input.xml')!=record['input_sha256'] or not native_bound_ok(meta):
        raise ValueError('invalid native stability admission')
    _,expected=build(meta['n'],meta['excited_port'],control=meta['control'])
    if any(meta[k]!=value for k,value in json.loads(json.dumps(expected)).items()):
        raise ValueError('changed geometry, measurement planes or acquisition settings')
    vn,cn=probe_names()
    if set(meta['raw_traces'])!=set(vn+cn):
        raise ValueError('every voltage/current trace required')
    if source_trace_info(out/'raw/et',meta)!=meta['source_trace']:
        raise ValueError('changed allocated excitation trace')
    for name,record in meta['raw_traces'].items():
        path=out/'raw'/name
        if dict(sha256=sha(path),**trace_clock(np.loadtxt(path,comments='%',ndmin=2),meta))!=record:
            raise ValueError('changed raw trace or sampling clock')
    with np.load(out/'waves.npz') as data:
        np.testing.assert_array_equal(data['frequency_hz'],FREQUENCIES)
        v,i=data['v'].copy(),data['i'].copy()
    if v.shape!=(4,3,len(FREQUENCIES)) or i.shape!=(4,2,2,len(FREQUENCIES)) or not np.isfinite(v).all() or not np.isfinite(i).all():
        raise ValueError('complete finite spectra required')
    raw_v,raw_i=probe_spectra(out/'raw')
    np.testing.assert_array_equal(v,raw_v)
    np.testing.assert_array_equal(i,raw_i)
    return meta,v,i,quality(meta)


def matrix(root):
    root=Path(root);records=[read(root/f'port{p}') for p in range(1,5)]
    first=records[0][0]
    keys=('n','control','geometry','mesh_mm','cap_s','end_db','dt_factor','dx_mm')
    if any(r[0]['excited_port']!=p or any(r[0][k]!=first[k] for k in keys) for p,r in enumerate(records,1)):
        raise ValueError('mixed acquisition cohort')
    s,d=extract(np.array([r[1] for r in records]),np.array([r[2] for r in records]),first['dx_mm']*1e-3)
    target=reference();beta=2*np.pi*FREQUENCIES*np.sqrt(ER)/C0
    zm=np.array(first['geometry']['modal_impedances_ohm'])[None,None,:,None]
    result={k:first[k] for k in keys if k!='mesh_mm'}
    result.update(all_native_complete=all(all(r[3].values()) for r in records),
        native_quality=[r[3] for r in records],qualified=False,directivity_qualified=False,
        s_target_abs=float(abs(s-target).max()),phase_target_deg=phase_change(s,target),
        beta_target_rel=float(abs(d['gamma_transfer_per_m'].imag/beta-1).max()),
        local_beta_diagnostic_rel=float(abs(d['gamma_per_m'].imag/beta-1).max()),
        impedance_target_rel=max(float(abs(d['modal_z_ohm']/zm-1).max()),
            float(abs(d['transfer_z_ohm']/zm[0]-1).max())),
        contour_s_abs=d['contour_s_abs'],staggering_s_abs=d['staggering_s_abs'],
        recurrence_residual=d['recurrence_residual'],wave_quality=d['wave_quality'],
        transfer_det_abs=d['transfer_det_abs'],transfer_symmetry_abs=d['transfer_symmetry_abs'],
        transfer_fit_rel=d['transfer_fit_rel'],transfer_condition_max=d['transfer_condition_max'],
        transfer_floor_min=d['transfer_floor_min'],local_vs_transfer_z_rel=d['local_vs_transfer_z_rel'],
        reciprocity_abs=float(abs(s-s.transpose(0,2,1)).max()),
        singular_power_max=float((np.linalg.svd(s,compute_uv=False)[:,0]**2).max()))
    result['target_passes']=all(result[k]<=LIMITS[k] for k in
        ('s_target_abs','phase_target_deg','beta_target_rel','impedance_target_rel',
         'contour_s_abs','recurrence_residual','reciprocity_abs','singular_power_max',
         'transfer_det_abs','transfer_symmetry_abs','transfer_fit_rel'))
    result['directivity_note']='Ideal isolation is zero; finite numerical directivity is diagnostic, not an infinite-directivity certificate.'
    np.savez_compressed(root/'matrix.npz',frequency_hz=FREQUENCIES,s=s,
                        gamma_per_m=d['gamma_per_m'],modal_z_ohm=d['modal_z_ohm'],
                        gamma_transfer_per_m=d['gamma_transfer_per_m'],transfer_z_ohm=d['transfer_z_ohm'])
    save(root/'comparison.json',result)
    return s,result


def study(root):
    root=Path(root);ordinary=[matrix(root/f'n{n}') for n in MESHES]
    if any((r['n'],r['control'])!=(n,'ordinary') for n,(_,r) in zip(MESHES,ordinary)):
        raise ValueError('incorrect ordinary cohort identity')
    mesh=[dict(s_abs=float(abs(b[0]-a[0]).max()),phase_deg=phase_change(b[0],a[0]))
          for a,b in zip(ordinary,ordinary[1:])]
    nominal=ordinary[MESHES.index(CONTROL_MESH)][0]
    controls={}
    for kind in CONTROLS[1:]:
        s,row=matrix(root/f'n{CONTROL_MESH}_{kind}')
        if (row['n'],row['control'])!=(CONTROL_MESH,kind):
            raise ValueError('incorrect independent control identity')
        change,pchange=float(abs(s-nominal).max()),phase_change(s,nominal)
        controls[kind]=dict(s_abs=change,phase_deg=pchange,native=row,
            passes=change<=LIMITS['s_control_abs'] and pchange<=LIMITS['phase_control_deg'])
    qualified=bool(all(r['all_native_complete'] and r['target_passes'] for _,r in ordinary)
        and all(r['s_abs']<=LIMITS['s_mesh_abs'] and r['phase_deg']<=LIMITS['phase_mesh_deg'] for r in mesh)
        and all(r['passes'] and r['native']['all_native_complete'] and r['native']['target_passes'] and
                r['native']['contour_s_abs']<=LIMITS['contour_s_abs'] and
                r['native']['recurrence_residual']<=LIMITS['recurrence_residual'] for r in controls.values()))
    result=dict(protocol=PROTOCOL,scope=SCOPE,limits=LIMITS,meshes=[r for _,r in ordinary],
                mesh_changes=mesh,controls=controls,qualified=qualified,directivity_qualified=False,
                finite_copper_loss_qualified=False)
    save(root/'comparison.json',result)
    return result
