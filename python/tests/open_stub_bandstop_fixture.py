"""Own air twin-wire realization associated with Example 8.8, p. 440.

The circuit does not prescribe a physical substrate. Cylindrical PEC wire
pairs supply the declared TEM impedances; finite junctions, radiation and
open-end fields are outside the ideal line circuit. No dimensions are fitted.
"""
import math
import json
import re
from pathlib import Path

import numpy as np
from openEMS.physical_constants import C0, Z0 as ETA0
from openEMS import ports as native_ports
from openEMS.ports import UI_data
from fairbeam import Simulation, excitation, multiport
from fairbeam.network import network_s
from tests.microstrip_fixture import graded
from tests.coax_resonator_fixture import save, sha
from tests import coupled_stripline_fixture as native_environment

ROOT = Path(__file__).resolve().parents[2]
F0, FBW, RIPPLE_DB, Z0 = 2e9, .15, .5, 50.
ER, MAIN_RADIUS_MM = 1., 2.
FREQUENCIES = np.linspace(1.5e9, 2.5e9, 401)
MESHES, KINDS, LOSSES = (0, 1, 2, 3), ('through', 'filter'), ('pec',)
SPACING_MM = {0: .45, 1: .35, 2: .25, 3: .175}
STUDY_CONTROLS = ('time', 'dt', 'boundary', 'plane', 'source')
DIAGNOSTIC_CONTROLS = ()
CONTROLS = ('ordinary', *STUDY_CONTROLS)
CAP_S, END_DB, THREADS, OVERSAMPLING = 32e-9, -80., 4, 4
MARKER = 'FAIRBEAM-OPEN-STUB-BANDSTOP-PROPAGATION-BEGIN'
LIMITS = dict(s_abs=.02, phase_deg=2., mesh_s_abs=.005, mesh_phase_deg=.5,
    control_s_abs=.0025, control_phase_deg=.2, contour_s_abs=.0025,
    recurrence_rel=.005, incident_condition=30., incident_floor=.02,
    reciprocity_abs=.005, singular_power_max=1.01, terminal_tail_db=-70.)


def prototype():
    epsilon = np.sqrt(10**(RIPPLE_DB/10)-1)
    gamma = np.sinh(np.arcsinh(1/epsilon)/3)
    a = np.sin((2*np.arange(1, 4)-1)*np.pi/6)
    b = gamma**2+np.sin(np.arange(1, 3)*np.pi/3)**2
    values = [2*a[0]/gamma]
    for k in (1, 2): values.append(4*a[k-1]*a[k]/(b[k-1]*values[-1]))
    values[2] = values[0]
    return np.asarray(values)


def wire_impedance(radius, spacing):
    if (not np.isfinite([radius, spacing]).all() or radius <= 0 or spacing <= 2*radius):
        raise ValueError('separated positive wire radii required')
    return float(ETA0/np.pi*np.arccosh(spacing/(2*radius)))


def dimensions():
    impedances = 4*Z0/(np.pi*FBW*prototype())
    spacing = 2*MAIN_RADIUS_MM*np.cosh(np.pi*Z0/ETA0)
    radii = spacing/(2*np.cosh(np.pi*impedances/ETA0))
    quarter = C0/(4*F0)*1e3
    return dict(main_radius_mm=MAIN_RADIUS_MM, axis_spacing_mm=float(spacing),
        main_gap_mm=float(spacing-2*MAIN_RADIUS_MM),
        stub_radii_mm=radii.tolist(), stub_impedances_ohm=impedances.tolist(),
        junctions_mm=(np.arange(3)*quarter).tolist(), stub_length_mm=float(quarter),
        metal='PEC cylinders', medium='air',
        length_definition='Stub centerline from main-line axis to open end; finite T junction excluded from ideal circuit')


def reference(freq=FREQUENCIES, kind='filter', plane_mm=12., loss='pec'):
    if np.iscomplexobj(freq): raise ValueError('real frequencies required')
    f = np.asarray(freq, float)
    if (kind not in KINDS or loss != 'pec' or f.ndim != 1 or not f.size
            or not np.isfinite(f).all() or np.any((f < 1e9) | (f > 3e9))
            or not np.isfinite(plane_mm) or plane_mm <= 0):
        raise ValueError('declared lossless kind, 1..3 GHz and positive reference planes required')
    delay = 1/(4*F0)
    if kind == 'through':
        s = network_s(f, [0, 1], [(0, 1, Z0, 2*delay)], z_ref=Z0)
    else:
        z = dimensions()['stub_impedances_ohm']
        s = network_s(f, [0, 2], [(0, 1, Z0, delay), (1, 2, Z0, delay),
            *[(k, k+3, value, delay) for k, value in enumerate(z)]], z_ref=Z0)
        centre = f == F0
        # Exact quarter-wave open stubs short their junctions. This is a
        # mathematical limit, never an infinite native attenuation target.
        s[centre] = -np.eye(2, dtype=complex)
    return s*np.exp(-4j*np.pi*f*plane_mm*1e-3/C0)[:, None, None]


def prototype_loss(freq=FREQUENCIES):
    f = np.asarray(freq, float); result = np.full(f.shape, np.inf)
    off = f != F0
    omega = FBW/(F0/f[off]-f[off]/F0)
    result[off] = 10*np.log10(1+(10**(RIPPLE_DB/10)-1)*(4*omega**3-3*omega)**2)
    return result


def _balanced(anchors, maximum):
    anchors = np.unique(np.round(anchors, 12))
    values = np.unique(np.concatenate([np.linspace(a, b, int(np.ceil((b-a)/maximum-1e-12))+1)
        for a, b in zip(anchors[:-1], anchors[1:])]))
    for _ in range(10000):
        h = np.diff(values); growth = np.maximum(h[1:]/h[:-1], h[:-1]/h[1:])
        if growth.max() <= 2*(1+1e-12): return values
        j = int(growth.argmax()); large = j if h[j] > h[j+1] else j+1
        count = int(np.ceil(h[large]/(2*min(h[j], h[j+1]))-1e-12))
        values = np.sort(np.r_[values, np.linspace(values[large], values[large+1], count+1)[1:-1]])
    raise ValueError('mesh neighbour growth did not settle')


def _outward(distance, first, maximum):
    values = graded(distance, first, maximum)
    if values[-1]-values[-2] < (values[-2]-values[-3])/2:
        values[-2] = (values[-3]+values[-1])/2
    return values


def mesh_lines(n, control='ordinary'):
    if isinstance(n, bool) or not isinstance(n, int) or n not in MESHES or control not in CONTROLS:
        raise ValueError('declared integer mesh and control required')
    d = dimensions(); h = SPACING_MM[n]; maximum = 2.5/(1+.25*n)
    end, length = d['junctions_mm'][-1], d['stub_length_mm']
    dx = 1/(1+.25*n)
    anchors = [-50., end+50., -40., -30., end+30., end+40.]
    for centre in (-18., -12., end+12., end+18.): anchors.extend((centre-dx, centre, centre+dx))
    for centre, radius in zip(d['junctions_mm'], d['stub_radii_mm']):
        count = max(2, math.ceil(4*radius/h))
        anchors.extend(centre+np.arange(-count, count+1)*h)
    x = _balanced(anchors, maximum)
    pad = 40. if control == 'boundary' else 25.
    core = np.linspace(-4., 4., 2*math.ceil(4/h)+1); dy = core[1]-core[0]
    lower = _outward(2+pad-4, dy, maximum)
    first = length-3*dy
    middle = _outward(first-4, dy, maximum)
    tip = length+np.arange(-3, 4)*dy
    upper = _outward(pad-3*dy, dy, maximum)
    y = _balanced(np.r_[-4-lower[:0:-1], core, 4+middle[1:], tip[1:], tip[-1]+upper[1:]], maximum)
    centre = d['axis_spacing_mm']/2; gap = d['main_gap_mm']
    zcore = _balanced([*np.linspace(-gap/2, gap/2, 2*n+2),
        -centre-MAIN_RADIUS_MM, -centre, centre, centre+MAIN_RADIUS_MM], h)
    dz = min(np.diff(zcore)[0], np.diff(zcore)[-1])
    outer = _outward(pad, dz, maximum)
    z = _balanced(np.r_[zcore[0]-outer[:0:-1], zcore, zcore[-1]+outer[1:]], maximum)
    # Fixed physical PML interfaces; eight uniform cells are outside each.
    def pml(axis):
        return np.r_[axis[0]-maximum*np.arange(8, 0, -1), axis,
            axis[-1]+maximum*np.arange(1, 9)]
    return tuple(pml(axis) for axis in (x, y, z))


def identity():
    names = ('tests/open_stub_bandstop_fixture.py', 'tests/test_open_stub_bandstop_fixture.py',
        'tests/coupled_stripline_fixture.py', 'tests/microstrip_fixture.py',
        'tests/coax_resonator_fixture.py', 'tests/test_coax_resonator.py',
        'fairbeam/simulation.py', 'fairbeam/design.py', 'fairbeam/excitation.py',
        'fairbeam/multiport.py', 'fairbeam/procutil.py', 'fairbeam/network.py')
    return {name: sha(ROOT/'python'/name) for name in names}


def runtime_identity():
    value = native_environment.runtime_identity()
    value['native_ports_sha256'] = sha(native_ports.__file__)
    return value


class OpenStubSimulation(Simulation):
    def to_bundle(self, *args, **kwargs):
        raise ValueError('research wire-contour probes have no bundle representation')


def build(n, port, kind='filter', loss='pec', control='ordinary', cap_s=None):
    if isinstance(port, bool) or port not in (1, 2) or kind not in KINDS or loss != 'pec':
        raise ValueError('declared source, kind and lossless case required')
    x,y,z = mesh_lines(n, control); grid = [len(a) for a in (x,y,z)]
    cells = int(np.prod(grid))
    if cells > 5_000_000: raise ValueError('allocation exceeds five million cells')
    dt = (.4875 if control == 'dt' else .65)/(C0*np.sqrt(sum(
        1/(np.diff(a).min()*1e-3)**2 for a in (x,y,z))))
    cap = CAP_S*(1.5 if control == 'time' else 1) if cap_s is None else float(cap_s)
    if not np.isfinite(cap) or cap <= 0: raise ValueError('positive finite window required')
    steps = math.ceil(cap/dt); end_db = END_DB-(10 if control == 'time' else 0)
    sim = OpenStubSimulation(FREQUENCIES[0], FREQUENCIES[-1], excitation='dgauss',
        end_criteria_db=end_db, max_timesteps=steps, boundaries=['PML_8']*6)
    for axis,lines in zip('xyz',(x,y,z)): sim.mesh.AddLine(axis,lines)
    sim.fdtd.SetTimeStep(float(dt)); sim.fdtd.SetOverSampling(OVERSAMPLING)
    sim.dielectric('air',1.).AddBox([x[0],y[0],z[0]],[x[-1],y[-1],z[-1]],priority=1)
    d = dimensions(); end = d['junctions_mm'][-1]
    centre = d['axis_spacing_mm']/2
    actual_centres = [float(z[np.argmin(abs(z-v))]) for v in (-centre,centre)]
    junctions = [float(x[np.argmin(abs(x-v))]) for v in d['junctions_mm']]
    length = float(y[np.argmin(abs(y-d['stub_length_mm']))])
    solids = ['air']
    for name,cz in zip(('negative_wire','positive_wire'),actual_centres):
        wire = sim.metal(name); solids.append(name)
        wire.AddCylinder([x[0],0.,cz],[x[-1],0.,cz],MAIN_RADIUS_MM,priority=10)
        if kind == 'filter':
            for sx,radius in zip(junctions,d['stub_radii_mm']):
                wire.AddCylinder([sx,0.,cz],[sx,length,cz],radius,priority=10)
    source_distance = 40. if control == 'source' else 30.
    plane = 18. if control == 'plane' else 12.
    dx = 1/(1+.25*n); ports = []; feeds = []; indices = {}; voltage_indices = {}
    mid_y,mid_z = (y[:-1]+y[1:])/2,(z[:-1]+z[1:])/2
    gap = d['main_gap_mm']; low_z,high_z = [float(z[np.argmin(abs(z-v))]) for v in (-gap/2,gap/2)]
    gap_dual = np.flatnonzero((mid_z>low_z)&(mid_z<high_z))
    if not len(gap_dual): raise ValueError('no current-contour plane in the wire gap')
    lower_indices = (int(gap_dual[-1]),int(gap_dual[0]))
    top = int(np.searchsorted(mid_z,actual_centres[1]+MAIN_RADIUS_MM+.25*SPACING_MM[n]))
    yl = int(np.searchsorted(mid_y,-MAIN_RADIUS_MM-.25*SPACING_MM[n])-1)
    yh = int(np.searchsorted(mid_y,MAIN_RADIUS_MM+.25*SPACING_MM[n]))
    for endpoint in (0,1):
        number,direction = endpoint+1,1 if endpoint==0 else -1
        target = -source_distance if endpoint==0 else end+source_distance
        source = float(x[np.argmin(abs(x-target))])
        for label,a,b in (('low',low_z-.75,low_z),('high',high_z,high_z+.75)):
            name = f'feedcap_{endpoint}_{label}';solids.append(name)
            sim.metal(name).AddBox([source,-.5,a],[source,.5,b],priority=11)
        record = dict(number=number,R=Z0,start=[source,-.5,low_z],stop=[source,.5,high_z],
            direction='z',excite=number==port)
        ports.append(sim.lumped_port(**record));feeds.append(record)
        centre_x = -plane if endpoint==0 else end+plane
        ix = int(np.argmin(abs(x-centre_x)))
        np.testing.assert_allclose(x[ix-1:ix+2],[centre_x-dx,centre_x,centre_x+dx],atol=1e-11,rtol=0)
        for j,sx in enumerate(x[ix-1:ix+2]):
            name = f'v{endpoint}_{j}'
            voltage_indices[name] = [[ix+j-1, int(np.argmin(abs(y))), int(np.argmin(abs(z-cz)))]
                for cz in actual_centres]
            sim.csx.AddProbe(name,p_type=0,weight=-1.).AddBox(
                [sx,0.,actual_centres[0]],[sx,0.,actual_centres[1]])
        for loop,zl in enumerate(lower_indices):
            zh,lo,hi = top+loop,yl-loop,yh+loop
            if not (mid_y[lo] < -MAIN_RADIUS_MM and mid_y[hi] > MAIN_RADIUS_MM
                    and low_z < mid_z[zl] < high_z
                    and mid_z[zh] > actual_centres[1]+MAIN_RADIUS_MM):
                raise ValueError('current contour must enclose only the positive wire')
            for j,sx in enumerate(((x[ix-1]+x[ix])/2,(x[ix]+x[ix+1])/2)):
                name = f'i{endpoint}_{loop}_{j}'
                sim.csx.AddProbe(name,p_type=1,norm_dir=0,weight=direction).AddBox(
                    [sx,mid_y[lo],mid_z[zl]],[sx,mid_y[hi],mid_z[zh]])
                indices[name] = [[ix+j-1,lo,zl],[ix+j-1,hi,zh]]
    for prop in sim.csx.GetAllProperties():prop.SetColor((128,128,128),alpha=prop.GetFillColor()[3])
    if sim.csx.Update(): raise ValueError('invalid native geometry')
    required = solids+[p.GetName() for p in sim.csx.GetAllProperties()
        if 'port_resist_' in p.GetName() or 'port_excite_' in p.GetName()]
    return sim,ports,dict(protocol='open-stub-bandstop-v1',n=n,excited_port=port,kind=kind,loss=loss,
        control=control,dimensions=d,grid=grid,native_cells=cells,
        mesh_mm={a:v.tolist() for a,v in zip('xyz',(x,y,z))},plane_mm=plane,
        declared_dt_s=float(dt),cap_s=cap,max_timesteps=steps,end_db=end_db,threads=THREADS,
        source_duration_s=excitation.dgauss_duration_s(FREQUENCIES[-1]),solids=solids,
        required_properties=required,expected_current_indices=indices,expected_voltage_indices=voltage_indices,
        source_distance_mm=source_distance,feeds=feeds,native_copper=False,
        actual_wire_centres_z_mm=actual_centres,actual_junctions_x_mm=junctions,
        actual_stub_length_mm=length,minimum_nominal_stub_diameter_cells=float(
            2*min(d['stub_radii_mm'])/SPACING_MM[n]),
        scope='Own finite air wire pairs; radiation, open ends and T-junction fields absent from ideal circuit')


def usage(sim, names):
    return {p.GetName(): [bool(v.GetPrimitiveUsed()) for v in p.GetAllPrimitives()]
        for p in sim.csx.GetAllProperties() if p.GetName() in names}


def probe_names():
    return ([f'v{p}_{j}' for p in range(2) for j in range(3)],
            [f'i{p}_{c}_{j}' for p in range(2) for c in range(2) for j in range(2)])


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
    grid = re.search(r'FDTD simulation size: (\d+)x(\d+)x(\d+) --> (\d+) FDTD cells', text_after)
    if (text.count(MARKER) != 1 or re.findall(r'openEMS 64bit -- version (\S+)', text_after) != ['v0.37.0-rc3']
            or f'fixed number of threads: {THREADS}' not in text_after or not grid
            or list(map(int, grid.groups()[:3])) != meta['grid'] or int(grid[4]) != meta['native_cells']):
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


def capture(out, n, port, kind='filter', loss='pec', control='ordinary', cap_s=None):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    sim, ports, meta = build(n, port, kind, loss, control, cap_s)
    meta.update(source=identity(), runtime=runtime_identity())
    sim.fdtd.Write2XML(str(out/'input.xml')); meta['input_sha256'] = sha(out/'input.xml')
    plan = json.loads((out.parent/'plan.json').read_text())
    row = next(r for r in plan['ports'] if r['excited_port'] == port)
    if (plan['source'] != meta['source'] or plan['runtime'] != meta['runtime']
            or any(row.get(k) != v for k, v in meta.items() if k not in ('source', 'runtime'))):
        raise ValueError('native inputs differ from the pre-field plan')
    setup_holder = build(n, port, kind, loss, control, cap_s)
    setup = setup_holder[0]; setup.fdtd.SetTimeStep(0.); setup.fdtd.SetNumberOfTimeSteps(1024)
    setup.fdtd.Write2XML(str(out/'operator-input.xml'))
    if sha(out/'operator-input.xml') != row['operator_input_sha256']:
        raise ValueError('operator input differs from the pre-field plan')
    op = dict(run=setup.run(str(out/'operator'), threads=THREADS, exact=True, echo=True, setup_only=True),
        required_usage=usage(setup, meta['required_properties']), input_sha256=sha(out/'operator-input.xml'))
    save(out/'operator.json', op)
    if (not 0 < meta['declared_dt_s'] <= .8*op['run'].get('timestep_s', 0.)
            or op['run'].get('grid') != meta['grid'] or set(op['required_usage']) != set(meta['required_properties'])
            or not all(v and all(v) for v in op['required_usage'].values())):
        raise ValueError('operator CFL/material-usage preflight failed')
    if identity() != meta['source'] or runtime_identity() != meta['runtime']:
        raise ValueError('source or runtime changed before fields')
    print(MARKER, flush=True)
    sim.run(str(out/'raw'), threads=THREADS, exact=True, echo=True, dump_statistics=True)
    stats = np.loadtxt(out/'raw/openEMS_stats.txt', comments='%')
    native = dict(cells=int(stats[0]), dt_s=float(stats[1]), timesteps=int(stats[2]),
        numerical_time_s=float(stats[3]), iteration_wall_s=float(stats[4]))
    meta.update(native=native, run=sim.run_stats, operator=op, required_usage=usage(sim, meta['required_properties']))
    vn, cn = probe_names(); values = []; tails = []; indices = {}; raw_hashes = {}
    for name in vn+cn:
        path = out/'raw'/name; data = np.loadtxt(path, comments='%', ndmin=2)
        trace_clock(data, meta, name in cn)
        values.append(np.asarray(UI_data([name], str(out/'raw'), FREQUENCIES).ui_f_val)[0])
        mask = data[:, 0] >= native['numerical_time_s']-.5e-9
        tails.append(float(10*np.log10(max(np.mean(data[mask, 1]**2), np.finfo(float).tiny)
            / max(np.max(data[:, 1]**2), np.finfo(float).tiny))))
        raw_hashes[name] = sha(path)
        header = '\n'.join(path.read_text().splitlines()[:3])
        indices[name] = [list(map(int, v)) for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]', header)]
    voltage = np.asarray(values[:6]).reshape(2, 3, -1)
    meta.update(actual_current_indices={k:indices[k] for k in cn},
        actual_voltage_indices={k:indices[k] for k in vn}, terminal_tail_db=max(tails), raw_sha256=raw_hashes,
        source_trace=source_info(out/'raw/et', meta))
    np.savez_compressed(out/'data.npz', frequency_hz=FREQUENCIES,
        v=voltage, i=np.asarray(values[6:]).reshape(2, 2, 2, -1))
    if identity() != meta['source'] or runtime_identity() != meta['runtime']:
        raise ValueError('source or runtime changed during fields')
    meta.update(data_sha256=sha(out/'data.npz'), operator_sha256=sha(out/'operator.json'))
    save(out/'report.json', meta)
    return meta


def read(out, allow_cap=False):
    out = Path(out); meta = json.loads((out/'report.json').read_text())
    holder = build(meta['n'], meta['excited_port'], meta['kind'], meta['loss'], meta['control'], meta['cap_s'])
    if (any(meta.get(k) != v for k, v in holder[2].items())
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
            or any(row.get(k) != v for k, v in holder[2].items())
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
    vn, cn = probe_names(); values = []; tails = []
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
    np.testing.assert_allclose(np.asarray(values[6:]).reshape(2, 2, 2, -1), data['i'], rtol=3e-13, atol=1e-23)
    if max(tails) != meta['terminal_tail_db'] or source_info(out/'raw/et', meta) != meta['source_trace']:
        raise ValueError('source or tail record changed')
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


def extract(v, i):
    v, i = np.asarray(v), np.asarray(i)
    if v.shape != (2, 2, 3, len(FREQUENCIES)) or i.shape != (2, 2, 2, 2, len(FREQUENCIES)):
        raise ValueError('three voltage planes and two dual currents on both contours required')
    u = v[:, :, 1]; den = 2*np.sum(abs(u)**2, axis=0)
    if np.any(den < .01*np.max(np.sum(abs(v)**2, axis=0), axis=1)) or np.any(den == 0):
        raise ValueError('voltage node in feed recurrence')
    recurrence = np.sum(np.conj(u)*(v[:, :, 0]+v[:, :, 2]), axis=0)/den
    half = np.sqrt((recurrence+1)/2)
    if not np.isfinite(half).all() or np.any((abs(half) < .5) | (abs(half) > 1.5)):
        raise ValueError('invalid stagger correction')
    rows = [scattering(u, i[:, :, loop].mean(axis=2)/half) for loop in range(2)]
    return rows[0], dict(contour_s_abs=float(abs(rows[0]-rows[1]).max()),
        recurrence_rel=float(abs(v[:, :, 0]+v[:, :, 2]-2*recurrence[None]*u).max()/abs(v).max()))


def difference(a, b):
    mask = abs(b[:, 1, 0]) >= .01
    return dict(s_abs=float(abs(a-b).max()), phase_deg=float(abs(np.angle(a[mask, 1, 0]/b[mask, 1, 0], deg=True)).max()))


def matrix(root, allow_cap=False):
    root = Path(root); rows = [read(root/f'port{p}', allow_cap) for p in (1, 2)]
    metas = [m for m, _, _ in rows]
    if any(m['excited_port'] != p or any(m[k] != metas[0][k] for k in ('n', 'kind', 'loss', 'control', 'cap_s', 'mesh_mm', 'plane_mm'))
            for p, m in enumerate(metas, 1)):
        raise ValueError('same model and distinct source columns required')
    actual, qa = extract(np.asarray([d['v'] for _, d, _ in rows]), np.asarray([d['i'] for _, d, _ in rows]))
    target = reference(kind=metas[0]['kind'], plane_mm=metas[0]['plane_mm'], loss=metas[0]['loss'])
    result = dict(n=metas[0]['n'], kind=metas[0]['kind'], loss=metas[0]['loss'], control=metas[0]['control'],
        **difference(actual, target), **qa, native_complete=all(all(q.values()) for _, _, q in rows),
        native_quality=[q for _, _, q in rows], qualified=False,
        reciprocity_abs=float(abs(actual-actual.transpose(0, 2, 1)).max()),
        singular_power_max=float((np.linalg.svd(actual, compute_uv=False)[:, 0]**2).max()),
        native_copper=False, numerical_source=identity())
    result['measurement_passes'] = all(result[k] <= LIMITS[k] for k in ('contour_s_abs', 'recurrence_rel', 'reciprocity_abs', 'singular_power_max'))
    result['point_target_passes'] = result['s_abs'] <= LIMITS['s_abs'] and result['phase_deg'] <= LIMITS['phase_deg']
    def attenuation(value):
        return float(-20*np.log10(abs(value))) if abs(value) > 0 else None
    prototype_db = prototype_loss()
    result['samples'] = [dict(f_hz=float(FREQUENCIES[k]),
        native_s21_abs=float(abs(actual[k,1,0])), native_loss_db=attenuation(actual[k,1,0]),
        line_loss_db=attenuation(target[k,1,0]), exact_notch_limit=bool(abs(target[k,1,0])==0),
        prototype_loss_db=(float(prototype_db[k]) if np.isfinite(prototype_db[k]) else None)
            if metas[0]['kind']=='filter' else 0.) for k in (120,160,200,240,280)]
    prefix = 'cap-diagnostic-' if allow_cap else ''
    report, arrays = root/(prefix+'comparison.json'), root/(prefix+'matrix.npz')
    if report.exists() or arrays.exists():
        if not report.exists() or not arrays.exists() or json.loads(report.read_text()) != result:
            raise ValueError('stored comparison changed')
        with np.load(arrays) as old:
            for k, v in dict(frequency_hz=FREQUENCIES, s=actual, reference=target).items(): np.testing.assert_array_equal(old[k], v)
    else:
        save(report, result); np.savez_compressed(arrays, frequency_hz=FREQUENCIES, s=actual, reference=target)
    return actual, result
