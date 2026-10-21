"""Own three-section thin-PEC coupled stripline: Example 7.8, p. 358.

The circuit has ideal joins. The field model has explicit overlapping abrupt
joins; no length is fitted to remove them. Gallery models are not edited.
The centered-strip map is described in the primary design note:
https://www.rogerscorp.cn/-/media/project/rogerscorp/documents/advanced-electronics-solutions/english/electrical-design-data/design-equations-for-broadside-and-edgewise-stripline.pdf
"""
from datetime import datetime, timezone
import math
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
from tests.stripline_fixture import agm

ROOT = Path(__file__).resolve().parents[2]
C0, B, ER, F0, Z0 = 299792458., 3.2, 2.2, 3e9, 50.
COUPLINGS = (.0125, .125, .0125)
LENGTH = C0/(4*F0*np.sqrt(ER))*1e3
FREQUENCIES = np.linspace(1e9, 5e9, 201)
MESHES, PILOT_MESHES, CONTROL_MESH, THREADS = (6, 8, 10), (4,), 6, 4
CONTROLS = ('ordinary', 'boundary', 'sidewall', 'guard', 'time', 'dt')
CAP_S, END_DB, DT_FACTOR = 9e-9, -100., .5
PROTOCOL = 'multisection-stripline-v1'
NATIVE_STEP_RATIO_MAX = .97
PROPAGATION_MARKER = 'FAIRBEAM-MULTISECTION-STRIPLINE-PROPAGATION-BEGIN'
LIMITS = dict(s_target_abs=.005, phase_target_deg=.5, s_mesh_abs=.0025,
    phase_mesh_deg=.2, s_control_abs=.001, phase_control_deg=.1,
    contour_s_abs=.001, recurrence_residual=.001, incident_condition=30.,
    incident_floor=.02, reciprocity_abs=.001, singular_power_max=1.005)
SCOPE = ('Three homogeneous centered thin-PEC coupled-strip sections with abrupt '
         'overlapping joins, internal planes and four-source inverse. No finite '
         'copper, dielectric loss, fabricated launch or infinite-directivity certificate.')


def identity():
    names = ('tests/multisection_stripline_fixture.py',
        'tests/test_multisection_stripline_fixture.py', 'tests/stripline_fixture.py',
        'tests/coax_resonator_fixture.py', 'tests/test_coax_resonator.py',
        'fairbeam/simulation.py', 'fairbeam/excitation.py', 'fairbeam/multiport.py',
        'fairbeam/procutil.py')
    return {name: sha(ROOT/'python'/name) for name in names}


def runtime_identity():
    value = base_runtime()
    value.update(python=sys.version, executable=sys.executable, numpy=np.__version__,
                 port_helpers_sha256=sha(native_ports.__file__))
    folder = Path(CSXCAD.__file__).parent
    value['csxcad_bindings'] = {str(p.resolve()): sha(p) for p in
        sorted(folder.glob('*.pyd'))+sorted(folder.glob('*.so'))+[Path(CSXCAD.__file__)]}
    if os.name == 'nt':
        prefix = os.environ.get('OPENEMS_INSTALL_PATH')
        if not prefix:
            raise ValueError('explicit bundled runtime required')
        value['dependencies'] = {name: sha(Path(prefix)/name) for name in ('fparser.dll', 'nf2ff.dll')}
    return value


def mode_impedance(k):
    k = np.asarray(k, float)
    if not np.isfinite(k).all() or np.any((k <= 0) | (k >= 1)):
        raise ValueError('elliptic modulus strictly between zero and one required')
    return ETA0/(4*np.sqrt(ER))*agm(1., np.sqrt(1-k*k))/agm(1., k)


def section(coupling):
    if isinstance(coupling, bool) or not np.isfinite(coupling) or not 0 < coupling < 1:
        raise ValueError('positive coupling strictly below one required')
    target = Z0*np.exp(np.array([1., -1.])*np.arctanh(coupling))
    roots = []
    for value in target:
        lo, hi = 1e-12, 1-1e-12
        for _ in range(64):
            mid = (lo+hi)/2
            if mode_impedance(mid) > value:
                lo = mid
            else:
                hi = mid
        roots.append((lo+hi)/2)
    ke, ko = roots
    width = 2*B/np.pi*np.arctanh(np.sqrt(ke*ko))
    gap = 2*B/np.pi*np.arctanh(np.sqrt(ke/ko))-width
    return dict(coupling=float(coupling), width_mm=float(width), gap_mm=float(gap),
                length_mm=float(LENGTH), modal_impedances_ohm=target.tolist())


def geometry():
    sections = [section(c) for c in COUPLINGS]
    return dict(sections=sections, epsilon_r=ER, b_mm=B, reference_ohm=Z0,
                design_hz=F0, joins_mm=[-LENGTH/2, LENGTH/2],
                measurement_planes_mm=[-1.5*LENGTH, 1.5*LENGTH],
                metal='zero-thickness PEC', join='abrupt overlapping strip rectangles', scope=SCOPE)


def reference(freq=FREQUENCIES):
    """Independent even/odd ABCD cascades; no field-derived inputs.

    Port order: left A, right A, right B, left B. Coupled port 4, isolated 3.
    This exact circuit is distinct from the first-order 0.1 coupling target.
    """
    freq = np.asarray(freq, float)
    if freq.ndim != 1 or not freq.size or not np.isfinite(freq).all() or np.any((freq < 1e9) | (freq > 5e9)):
        raise ValueError('finite frequencies in the declared 1..5 GHz band required')
    modal = np.zeros((len(freq), 4, 4), complex)
    theta = np.pi*freq/(2*F0)
    for mode, ids in enumerate(((0, 2), (1, 3))):
        total = np.broadcast_to(np.eye(2, dtype=complex), (len(freq), 2, 2)).copy()
        for part in geometry()['sections']:
            z = part['modal_impedances_ohm'][mode]
            line = np.zeros_like(total)
            line[:, 0, 0] = line[:, 1, 1] = np.cos(theta)
            line[:, 0, 1], line[:, 1, 0] = 1j*z*np.sin(theta), 1j*np.sin(theta)/z
            total = total@line
        a, b, c, d = total[:, 0, 0], total[:, 0, 1], total[:, 1, 0], total[:, 1, 1]
        den = a+b/Z0+c*Z0+d
        modal[:, ids[0], ids[0]] = (a+b/Z0-c*Z0-d)/den
        modal[:, ids[1], ids[1]] = (-a+b/Z0-c*Z0+d)/den
        modal[:, ids[0], ids[1]] = 2*(a*d-b*c)/den
        modal[:, ids[1], ids[0]] = 2/den
    q = np.array([[1., 1.], [1., -1.]])/np.sqrt(2)
    p = np.zeros((4, 4))
    p[0, :2], p[3, :2], p[1, 2:], p[2, 2:] = q[0], q[1], q[0], q[1]
    return p@modal@p.T


def graded(span, first):
    values, step = [0.], first
    while values[-1] < span:
        values.append(min(span, values[-1]+step))
        step = min(B/2, step*1.25)
    if len(values) > 2 and values[-1]-values[-2] < first/2:
        values[-2] = (values[-3]+values[-1])/2
    return np.array(values)


def transverse_mesh(n, g):
    delta = min(min(p['width_mm'], p['gap_mm']) for p in g['sections'])/n
    anchors = [0.]
    for part in g['sections'][:2]:
        center = (part['width_mm']+part['gap_mm'])/2
        for c in (-center, center):
            lo, hi = c-part['width_mm']/2, c+part['width_mm']/2
            anchors.extend((c, lo-2*delta/3, lo+delta/3, hi-delta/3, hi+2*delta/3))
    anchors = np.unique(anchors)
    pieces = [np.linspace(a, b, max(1, math.ceil((b-a)/delta-1e-12))+1)[:-1]
              for a, b in zip(anchors, anchors[1:])]
    outward = graded(6*B-anchors[-1], delta)
    y = np.r_[anchors[0]-outward[:0:-1], np.concatenate(pieces), anchors[-1], anchors[-1]+outward[1:]]
    positive = graded(B/2, delta/3)
    return y, np.r_[-positive[:0:-1], positive], delta


def build(n, port, *, control='ordinary'):
    if (isinstance(n, bool) or not isinstance(n, int) or n not in MESHES+PILOT_MESHES
            or isinstance(port, bool) or not isinstance(port, int) or port not in range(1, 5)
            or control not in CONTROLS):
        raise ValueError('declared integer mesh, source and independent control required')
    g = geometry()
    dx = LENGTH/(8*n)
    extent = (28 if control in ('guard', 'boundary') else 24)*n
    x = np.arange(-extent, extent+1)*dx
    for k in (-24, -20, -16, -12, -4, 4, 12, 16, 20, 24):
        x[k*n+extent] = k/8*LENGTH
    y, z, delta = transverse_mesh(n, g)
    if control == 'sidewall':
        step = y[-1]-y[-2]
        count = math.ceil(2*B/step)
        y = np.r_[y[0]-step*np.arange(count, 0, -1), y, y[-1]+step*np.arange(1, count+1)]
    cap = CAP_S*(1.5 if control == 'time' else 1.)
    end_db = END_DB-(10. if control == 'time' else 0.)
    factor = .35 if control == 'dt' else DT_FACTOR
    sim = Simulation(FREQUENCIES[0], FREQUENCIES[-1], excitation='dgauss',
                     end_criteria_db=end_db, boundaries=['PML_8', 'PML_8', 'PEC', 'PEC', 'PEC', 'PEC'])
    axes = dict(x=x, y=y, z=z)
    for axis, lines in axes.items():
        sim.mesh.AddLine(axis, lines)
    sim.dielectric('homogeneous', ER).AddBox([x[0], y[0], z[0]], [x[-1], y[-1], z[-1]], priority=0)
    ground = sim.metal('grounds')
    for height in (-B/2, B/2):
        ground.AddBox([x[0], y[0], height], [x[-1], y[-1], height], priority=10)
    strips = sim.metal('strips')
    for part, left, right in zip(g['sections'], (x[0], -LENGTH/2, LENGTH/2), (-LENGTH/2, LENGTH/2, x[-1])):
        center = (part['width_mm']+part['gap_mm'])/2
        for c in (-center, center):
            strips.AddBox([left, c-part['width_mm']/2, 0.], [right, c+part['width_mm']/2, 0.], priority=10)
    part = g['sections'][0]
    center = (part['width_mm']+part['gap_mm'])/2
    source_length = (2.5 if control == 'guard' else 2.)*LENGTH
    contours = []
    for desired in (B/4, 3*B/8):
        j = int(np.searchsorted(z, desired))
        contours.append(float((z[j-1]+z[j])/2))
    if contours[0] == contours[1]:
        raise ValueError('independent Ampere contours collapsed')
    for number, sx, c in ((1, -1, center), (2, 1, center), (3, 1, -center), (4, -1, -center)):
        source = sx*source_length
        lo, hi = c-part['width_mm']/2, c+part['width_mm']/2
        sim.lumped_port(number, Z0, [source, lo, -B/2], [source, hi, 0.], 'z', excite=number == port,
            group={'connection': 'parallel', 'members': [{'start': [source, lo, 0.],
                'stop': [source, hi, B/2], 'direction': 'z', 'polarity': -1}]})
        plane_index = sx*12*n+extent
        for j, plane in enumerate(x[plane_index-1:plane_index+2]):
            for half, a, b, weight in ((0, -B/2, 0., -.5), (1, 0., B/2, .5)):
                sim.csx.AddProbe(f'v{number}_{j}_{half}', p_type=0, weight=weight).AddBox([plane, c, a], [plane, c, b])
        for j, index in enumerate((plane_index-1, plane_index)):
            plane = (x[index]+x[index+1])/2
            for loop, height in enumerate(contours):
                sim.csx.AddProbe(f'i{number}_{j}_{loop}', p_type=1, norm_dir=0).AddBox(
                    [plane, lo-delta/6, -height], [plane, hi+delta/6, height])
    dt = factor/(C0*np.sqrt(sum((np.diff(v).min()*sim.unit)**-2 for v in axes.values())))
    steps = math.ceil(cap/dt)
    sim.max_timesteps = steps
    sim.fdtd.SetTimeStep(float(dt))
    sim.fdtd.SetNumberOfTimeSteps(steps)
    sim.fdtd.SetOverSampling(4)
    if sim.csx.Update():
        raise ValueError('invalid native geometry')
    meta = dict(protocol=PROTOCOL, n=n, excited_port=port, control=control, geometry=g, ports=sim.ports,
        mesh_mm={a: v.tolist() for a, v in axes.items()}, native_lines=[len(v) for v in axes.values()],
        native_cells=int(np.prod([len(v) for v in axes.values()])), declared_dt_s=float(dt),
        max_timesteps=steps, cap_s=cap, end_db=end_db, dt_factor=factor, dx_mm=dx,
        voltage_planes_mm=[-1.5*LENGTH, 1.5*LENGTH], ampere_heights_mm=contours,
        source_distance_mm=source_length-1.5*LENGTH,
        source_duration_s=excitation.dgauss_duration_s(FREQUENCIES[-1]), threads=THREADS, scope=SCOPE)
    return sim, meta


def assemble(v, i):
    a, b = (v+Z0*i)/(2*np.sqrt(Z0)), (v-Z0*i)/(2*np.sqrt(Z0))
    incoming = a.transpose(2, 1, 0)
    singular = np.linalg.svd(incoming, compute_uv=False)
    floor = float(singular[:, -1].min()/max(singular[:, 0].max(), np.finfo(float).tiny))
    condition = float(np.max(singular[:, 0]/np.maximum(singular[:, -1], np.finfo(float).tiny)))
    if condition > LIMITS['incident_condition'] or floor < LIMITS['incident_floor']:
        raise ValueError('unexcited or ill-conditioned incoming wave matrix')
    return multiport.assemble_s(a, b, [1, 2, 3, 4], 4), dict(incident_condition=condition, incident_floor=floor)


def extract(v, i):
    """Correct only local current staggering in uniform OUTER feed sections.

    r=cosh(gamma*dx) follows from the measured voltage recurrence;
    cosh(gamma*dx/2)=sqrt((r+1)/2). No acosh, target gamma, whole-DUT
    uniformity assumption, determinant normalization or loss clipping is used.
    Left/right and even/odd feed corrections are inferred independently.
    """
    v, i = np.asarray(v, complex), np.asarray(i, complex)
    if (v.shape != (4, 4, 3, len(FREQUENCIES)) or i.shape != (4, 4, 2, 2, len(FREQUENCIES))
            or not np.isfinite(v).all() or not np.isfinite(i).all()):
        raise ValueError('complete finite four-source waves required')
    q = np.array([[1., 1.], [1., -1.]])/np.sqrt(2)
    corrected = np.zeros((2, 4, 4, len(FREQUENCIES)), complex)
    raw = np.zeros_like(corrected)
    halves, recurrence = [], 0.
    for ids, sign in (((0, 3), 1), ((1, 2), -1)):
        field = np.einsum('mc,ecjf->emjf', q, np.take(v, ids, axis=1))
        center = field[:, :, 1]
        denom = 2*np.sum(abs(center)**2, axis=0)
        scale = np.max(np.sum(abs(field)**2, axis=0), axis=1)
        if np.any(scale <= 0) or np.any(denom < .01*scale):
            raise ValueError('standing-wave node or missing modal excitation')
        r = np.sum(np.conj(center)*(field[:, :, 0]+field[:, :, 2]), axis=0)/denom
        half = np.sqrt((r+1)/2)
        if not np.isfinite(half).all() or np.any((abs(half) < .5) | (abs(half) > 1.5)):
            raise ValueError('invalid measured local current correction')
        recurrence = max(recurrence, float(abs(field[:, :, 0]+field[:, :, 2]-2*r*center).max()/abs(field).max()))
        halves.append(half)
        for loop in (0, 1):
            modal_i = np.einsum('mc,ecjf->emjf', q, np.take(i, ids, axis=1)[:, :, :, loop, :]).mean(axis=2)
            raw[loop][:, ids] = sign*np.einsum('cm,emf->ecf', q.T, modal_i)
            corrected[loop][:, ids] = sign*np.einsum('cm,emf->ecf', q.T, modal_i/half)
    spectra, quality = [], []
    for loop in (0, 1):
        s, row = assemble(v[:, :, 1], corrected[loop])
        spectra.append(s)
        quality.append(row)
    return spectra[0], dict(contour_s_abs=float(abs(spectra[1]-spectra[0]).max()),
        recurrence_residual=recurrence, local_current_half_factor=np.array(halves),
        raw_s=assemble(v[:, :, 1], raw[0])[0], **quality[0])


def phase(s):
    s = np.asarray(s, complex)
    if s.shape != (len(FREQUENCIES), 4, 4) or not np.isfinite(s).all() or np.any(abs(s[:, (1, 3), 0]) < .01):
        raise ValueError('finite non-null through and coupled waves required')
    return np.angle(s[:, 3, 0]*np.conj(s[:, 1, 0]), deg=True)


def phase_change(a, b):
    return float(np.max(abs((phase(a)-phase(b)+180) % 360-180)))


def native_bound_ok(meta):
    record = meta.get('operator_preflight', {})
    run = record.get('run', {})
    bound = run.get('timestep_s', 0.)
    return bool(isinstance(bound, (float, int)) and np.isfinite(bound) and bound > 0
        and record.get('setup_only') is True and record.get('ratio_limit') == NATIVE_STEP_RATIO_MAX
        and record.get('planned_dt_s') == meta['declared_dt_s']
        and meta['declared_dt_s']/bound <= NATIVE_STEP_RATIO_MAX
        and run.get('grid') == meta['native_lines'] and run.get('threads') == THREADS
        and run.get('engine') == 'cpu' and run.get('exact_endcriteria') is True
        and run.get('converged') is False and run.get('energy_trace') == [] and 'timesteps' not in run)


def operator_preflight(out, n, port, control='ordinary'):
    out = Path(out)
    sim, meta = build(n, port, control=control)
    sim.fdtd.SetTimeStep(0.)
    sim.fdtd.Write2XML(str(out/'operator-input.xml'))
    run = sim.run(str(out/'operator'), threads=THREADS, exact=True, echo=True, setup_only=True)
    record = dict(setup_only=True, ratio_limit=NATIVE_STEP_RATIO_MAX, planned_dt_s=meta['declared_dt_s'],
                  input_sha256=sha(out/'operator-input.xml'), run=run)
    record['passes'] = native_bound_ok({**meta, 'operator_preflight': record})
    save(out/'operator.json', record)
    if not record['passes']:
        raise ValueError('planned step exceeds the setup-only native stability bound')
    return record


def trace_clock(data, meta):
    data = np.asarray(data, float)
    native = meta['native']
    stride = int(1/(2*FREQUENCIES[-1]*native['dt_s']*4))
    if (data.ndim != 2 or len(data) < 3 or data.shape[1] < 2 or not np.isfinite(data).all() or stride < 1
            or np.any(np.diff(data[:, 0]) <= 0)
            or not np.allclose(np.diff(data[:, 0]), stride*native['dt_s'], rtol=2e-5, atol=native['dt_s']*1e-5)
            or data[-1, 0] > native['numerical_time_s']+stride*native['dt_s']):
        raise ValueError('finite native traces on the declared sampling clock required')
    return dict(samples=len(data), first_s=float(data[0, 0]), last_s=float(data[-1, 0]), stride_steps=stride)


def source_trace_info(path, meta):
    # et is the allocated input at six-significant-digit text precision;
    # it is neither the decimated field clock nor an energy trace.
    data = np.loadtxt(path, comments='%', ndmin=2)
    dt = meta['native']['dt_s']
    if (data.ndim != 2 or data.shape[1] != 2 or not np.isfinite(data).all()
            or len(data) not in (meta['max_timesteps'], meta['max_timesteps']+1)):
        raise ValueError('complete finite allocated excitation trace required')
    if not np.allclose(data[:, 0], np.arange(len(data))*dt, rtol=6e-6, atol=dt*1e-6):
        raise ValueError('excitation timestamps differ beyond native text precision')
    return dict(sha256=sha(path), samples=len(data), last_s=float(data[-1, 0]), timestamp_print_rtol=6e-6)


def quality(meta):
    run, native, header = meta['run'], meta['native'], meta.get('native_header', {})
    return {k: bool(v) for k, v in dict(
        grid=native['cells'] == meta['native_cells'] and run.get('grid') == meta['native_lines'],
        clock=abs(native['dt_s']/meta['declared_dt_s']-1) < 1e-8,
        operator=native_bound_ok(meta),
        time=native['timesteps'] > 0 and abs(native['numerical_time_s']/(native['dt_s']*native['timesteps'])-1) < 1e-8,
        header=header.get('version') == 'v0.37.0-rc3' and header.get('threads') == THREADS
            and header.get('nyquist_interval') == int(1/(2*FREQUENCIES[-1]*native['dt_s']))
            and header.get('excitation_timesteps') == meta['max_timesteps'],
        source=native['numerical_time_s'] > meta['source_duration_s'],
        energy=run.get('converged') and run.get('exact_endcriteria') and not run.get('hit_timestep_limit')
            and run.get('threads') == THREADS and native['timesteps'] < meta['max_timesteps']).items()}


def probe_names():
    return ([f'v{p}_{j}_{h}' for p in range(1, 5) for j in range(3) for h in range(2)],
            [f'i{p}_{j}_{loop}' for p in range(1, 5) for j in range(2) for loop in range(2)])


def probe_spectra(raw):
    vn, cn = probe_names()
    v = np.asarray(native_ports.UI_data(vn, str(raw), FREQUENCIES).ui_f_val).reshape(4, 3, 2, -1).sum(axis=2)
    current = np.asarray(native_ports.UI_data(cn, str(raw), FREQUENCIES).ui_f_val).reshape(4, 2, 2, -1)
    return v, current


def acquire(out, n, port, control='ordinary'):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    sim, meta = build(n, port, control=control)
    source, runtime = identity(), runtime_identity()
    sim.fdtd.Write2XML(str(out/'input.xml'))
    meta.update(source_sha256=source, runtime=runtime, limits=LIMITS, input_sha256=sha(out/'input.xml'),
                started_utc=datetime.now(timezone.utc).isoformat())
    save(out/'declared.json', meta)
    meta['operator_preflight'] = operator_preflight(out, n, port, control)
    meta['operator_sha256'] = sha(out/'operator.json')
    if source != identity() or runtime != runtime_identity():
        raise RuntimeError('source/runtime changed during native setup')
    save(out/'declared.json', meta)
    print(PROPAGATION_MARKER, flush=True)
    sim.run(str(out/'raw'), threads=THREADS, exact=True, echo=True, dump_statistics=True)
    stats = np.loadtxt(out/'raw/openEMS_stats.txt', comments='%')
    meta.update(native=dict(cells=int(stats[0]), dt_s=float(stats[1]), timesteps=int(stats[2]),
                numerical_time_s=float(stats[3]), iteration_wall_s=float(stats[4])), run=sim.run_stats)
    vn, cn = probe_names()
    v, current = probe_spectra(out/'raw')
    traces = {name: dict(sha256=sha(out/'raw'/name), **trace_clock(
        np.loadtxt(out/'raw'/name, comments='%', ndmin=2), meta)) for name in vn+cn}
    np.savez_compressed(out/'waves.npz', frequency_hz=FREQUENCIES, v=v, i=current)
    if source != identity() or runtime != runtime_identity():
        raise RuntimeError('source/runtime changed during acquisition')
    meta.update(waves_sha256=sha(out/'waves.npz'), raw_traces=traces,
                source_trace=source_trace_info(out/'raw/et', meta), completed_utc=datetime.now(timezone.utc).isoformat())
    save(out/'report.json', meta)
    return meta


def read(out):
    out = Path(out)
    meta = json.loads((out/'report.json').read_text(encoding='utf-8'))
    if meta['source_sha256'] != identity() or meta['runtime'] != runtime_identity() or meta['limits'] != LIMITS:
        raise ValueError('source/runtime/limits changed; preserve separate acquisition epochs')
    for name, key in (('input.xml', 'input_sha256'), ('waves.npz', 'waves_sha256'), ('operator.json', 'operator_sha256')):
        if sha(out/name) != meta[key]:
            raise ValueError('changed native input, operator receipt or wave data')
    record = json.loads((out/'operator.json').read_text())
    if record != meta['operator_preflight'] or sha(out/'operator-input.xml') != record['input_sha256'] or not native_bound_ok(meta):
        raise ValueError('invalid native stability admission')
    _, expected = build(meta['n'], meta['excited_port'], control=meta['control'])
    if any(meta[k] != value for k, value in json.loads(json.dumps(expected)).items()):
        raise ValueError('changed geometry, measurement planes or acquisition settings')
    vn, cn = probe_names()
    if set(meta['raw_traces']) != set(vn+cn):
        raise ValueError('every voltage/current trace required')
    if source_trace_info(out/'raw/et', meta) != meta['source_trace']:
        raise ValueError('changed allocated excitation trace')
    for name, record in meta['raw_traces'].items():
        path = out/'raw'/name
        if dict(sha256=sha(path), **trace_clock(np.loadtxt(path, comments='%', ndmin=2), meta)) != record:
            raise ValueError('changed raw trace or sampling clock')
    with np.load(out/'waves.npz') as data:
        np.testing.assert_array_equal(data['frequency_hz'], FREQUENCIES)
        v, i = data['v'].copy(), data['i'].copy()
    raw_v, raw_i = probe_spectra(out/'raw')
    np.testing.assert_array_equal(v, raw_v)
    np.testing.assert_array_equal(i, raw_i)
    return meta, v, i, quality(meta)


def matrix(root):
    root = Path(root)
    records = [read(root/f'port{p}') for p in range(1, 5)]
    first = records[0][0]
    keys = ('n', 'control', 'geometry', 'mesh_mm', 'cap_s', 'end_db', 'dt_factor', 'dx_mm')
    if any(r[0]['excited_port'] != p or any(r[0][k] != first[k] for k in keys) for p, r in enumerate(records, 1)):
        raise ValueError('mixed acquisition cohort')
    s, diagnostic = extract(np.array([r[1] for r in records]), np.array([r[2] for r in records]))
    target = reference()
    result = {k: first[k] for k in keys if k != 'mesh_mm'}
    result.update(all_native_complete=all(all(r[3].values()) for r in records), native_quality=[r[3] for r in records],
        qualified=False, directivity_qualified=False, s_target_abs=float(abs(s-target).max()),
        phase_target_deg=phase_change(s, target), reciprocity_abs=float(abs(s-s.transpose(0, 2, 1)).max()),
        singular_power_max=float((np.linalg.svd(s, compute_uv=False)[:, 0]**2).max()))
    for key in ('contour_s_abs', 'recurrence_residual', 'incident_condition', 'incident_floor'):
        result[key] = diagnostic[key]
    upper = ('s_target_abs', 'phase_target_deg', 'reciprocity_abs', 'singular_power_max',
             'contour_s_abs', 'recurrence_residual', 'incident_condition')
    result['target_passes'] = bool(result['all_native_complete'] and all(result[k] <= LIMITS[k] for k in upper)
                                   and result['incident_floor'] >= LIMITS['incident_floor'])
    np.savez_compressed(root/'matrix.npz', frequency_hz=FREQUENCIES, s=s, reference=target, **diagnostic)
    save(root/'comparison.json', result)
    return s, result


def study(root):
    root = Path(root)
    matrices = [matrix(root/f'n{n}') for n in MESHES]
    changes = []
    for (n1, (s1, _)), (n2, (s2, _)) in zip(zip(MESHES, matrices), zip(MESHES[1:], matrices[1:])):
        ds, dp = float(abs(s2-s1).max()), phase_change(s2, s1)
        changes.append(dict(meshes=[n1, n2], s_abs=ds, phase_deg=dp,
            passes=ds <= LIMITS['s_mesh_abs'] and dp <= LIMITS['phase_mesh_deg']))
    nominal = matrices[MESHES.index(CONTROL_MESH)][0]
    controls = {}
    for name in CONTROLS[1:]:
        s, result = matrix(root/f'n{CONTROL_MESH}_{name}')
        ds, dp = float(abs(s-nominal).max()), phase_change(s, nominal)
        controls[name] = dict(s_abs=ds, phase_deg=dp, native=result,
            passes=result['target_passes'] and ds <= LIMITS['s_control_abs'] and dp <= LIMITS['phase_control_deg'])
    qualified = (all(r['target_passes'] for _, r in matrices) and all(r['passes'] for r in changes)
                 and all(r['passes'] for r in controls.values()))
    result = dict(protocol=PROTOCOL, source=identity(), runtime=runtime_identity(), limits=LIMITS, scope=SCOPE,
        meshes=[r for _, r in matrices], mesh_changes=changes, controls=controls,
        qualified=bool(qualified), directivity_qualified=False, finite_copper_qualified=False,
        dielectric_loss_qualified=False, fabricated_launches_qualified=False)
    save(root/'comparison.json', result)
    return result
