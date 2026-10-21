"""Own six-section microstrip comparison associated with Example 8.6, p. 424.

The Butterworth prototype, ideal line cascade and finite stepped layout are
different models. Dimensions are calculated before fields, never response-fit.
Native dielectric loss is constant conductivity, not constant broadband tan_d.
Copper is an analytic estimate only: neither a PEC run nor a scalar sheet
certifies resolved skin loss on both faces of a finite microstrip conductor.
"""
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
from openEMS import ports as native_ports
from openEMS.ports import UI_data
from openEMS.physical_constants import C0, EPS0, MUE0

from fairbeam import Simulation, excitation, multiport
from fairbeam.analytic import (cascade_lines_s, microstrip_eps_eff,
    microstrip_width, stepped_impedance_lowpass)
from fairbeam.network import network_s
from tests.coax_resonator_fixture import save, sha, runtime_identity as base_runtime
from tests.microstrip_fixture import graded

ROOT = Path(__file__).resolve().parents[2]
FC, Z0, ER, H = 2.5e9, 50., 4.2, 1.58
ORDER, Z_HIGH, Z_LOW = 6, 120., 20.
TAN_D, SIGMA, COPPER_MM = .02, 5.8e7, .0127
FREQUENCIES = np.linspace(.5e9, 5e9, 451)
MESHES, KINDS, LOSSES = (1, 2, 3), ('through', 'filter'), ('pec', 'dielectric')
CONTROLS = ('ordinary', 'time', 'dt', 'boundary', 'plane')
CAP_S, END_DB, THREADS, OVERSAMPLING = 8e-9, -80., 4, 4
MARKER = 'FAIRBEAM-STEPPED-LOWPASS-PROPAGATION-BEGIN'
LIMITS = dict(s_abs=.02, phase_deg=2., mesh_s_abs=.005, mesh_phase_deg=.5,
    control_s_abs=.0025, control_phase_deg=.2, contour_s_abs=.0025,
    recurrence_rel=.005, incident_condition=30., incident_floor=.02,
    reciprocity_abs=.005, singular_power_max=1.01, terminal_tail_db=-70.)


def identity():
    names = ('tests/stepped_filter_fixture.py', 'tests/test_stepped_filter_fixture.py',
        'tests/microstrip_fixture.py', 'tests/coax_resonator_fixture.py',
        'tests/test_coax_resonator.py', 'fairbeam/simulation.py', 'fairbeam/analytic.py',
        'fairbeam/network.py', 'fairbeam/excitation.py', 'fairbeam/multiport.py',
        'fairbeam/procutil.py')
    return {name: sha(ROOT/'python'/name) for name in names}


def runtime_identity():
    value = base_runtime()
    value.update(python=sys.version, executable=sys.executable, numpy=np.__version__,
                 native_ports_sha256=sha(native_ports.__file__))
    return value


def dimensions():
    sections = stepped_impedance_lowpass(FC, ORDER, 0., Z0, Z_HIGH, Z_LOW, ER, H)
    return dict(sections=sections, junctions_mm=np.r_[0., np.cumsum(
        [s['length'] for s in sections])].tolist(), feed_width_mm=microstrip_width(Z0, ER, H))


def reference(freq=FREQUENCIES, kind='filter', plane_mm=6., loss='pec'):
    """Quasi-TEM line circuit; the dielectric approximation matches native kappa."""
    f = np.asarray(freq, float)
    if (kind not in KINDS or loss not in LOSSES or f.ndim != 1 or not f.size
            or not np.isfinite(f).all() or np.any(f <= 0)
            or not np.isfinite(plane_mm) or plane_mm <= 0):
        raise ValueError('declared kind/loss, positive real frequencies and planes required')
    d = dimensions()
    lengths = ([dict(z=Z0, width=d['feed_width_mm'],
        eps_eff=microstrip_eps_eff(ER, H, d['feed_width_mm']),
        length=d['junctions_mm'][-1])] if kind == 'through' else d['sections'])
    feed = dict(z=Z0, width=d['feed_width_mm'],
        eps_eff=microstrip_eps_eff(ER, H, d['feed_width_mm']), length=plane_mm)
    sections = [feed, *lengths, feed]
    if loss == 'pec':
        return network_s(f, [0, len(sections)], [(k, k+1, s['z'],
            s['length']*1e-3*np.sqrt(s['eps_eff'])/C0) for k, s in enumerate(sections)], z_ref=Z0)
    # Filling-factor attenuation approximation, stamped separately from the
    # lossless nodal API. No material dispersion or discontinuity correction.
    total = np.broadcast_to(np.eye(2, dtype=complex), (len(f), 2, 2)).copy()
    for s in sections:
        alpha = 2*np.pi*f/C0*ER*(s['eps_eff']-1)/(2*np.sqrt(s['eps_eff'])*(ER-1))*TAN_D*FC/f
        gl = (alpha+2j*np.pi*f*np.sqrt(s['eps_eff'])/C0)*s['length']*1e-3
        block = np.empty_like(total)
        block[:, 0, 0] = block[:, 1, 1] = np.cosh(gl)
        block[:, 0, 1], block[:, 1, 0] = s['z']*np.sinh(gl), np.sinh(gl)/s['z']
        total = total@block
    return abcd_s(total)


def abcd_s(t):
    a, b, c, d = (t[:, i, j] for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)))
    den = a+b/Z0+c*Z0+d
    s = np.empty_like(t)
    s[:, 0, 0], s[:, 1, 1] = (a+b/Z0-c*Z0-d)/den, (-a+b/Z0-c*Z0+d)/den
    s[:, 1, 0], s[:, 0, 1] = 2/den, 2*(a*d-b*c)/den
    return s


def loss_estimates(freq=FREQUENCIES):
    """Uniform-current one-face estimate only; not a native copper result."""
    f = np.asarray(freq, float)
    skin = np.sqrt(1/(np.pi*f*MUE0*SIGMA))
    rs = np.sqrt(np.pi*f*MUE0/SIGMA)
    sections = dimensions()['sections']
    # Omits ground loss, crowding, roughness, step radiation and both-face split.
    nepers = sum(rs/(s['z']*s['width']*1e-3)*s['length']*1e-3 for s in sections)
    return dict(frequency_hz=f.tolist(), skin_depth_m=skin.tolist(),
        thickness_m=COPPER_MM*1e-3, one_face_strip_attenuation_db=(20/np.log(10)*nepers).tolist(),
        native_kappa_s_m=TAN_D*2*np.pi*FC*EPS0*ER,
        native_tan_d=(TAN_D*FC/f).tolist(), constant_tan_d=TAN_D,
        native_copper_runs=0, copper_qualified=False)


def _balanced(anchors, maximum):
    anchors = np.unique(anchors)
    values = np.unique(np.concatenate([np.linspace(a, b, int(np.ceil((b-a)/maximum))+1)
        for a, b in zip(anchors[:-1], anchors[1:])]))
    for _ in range(10000):
        h = np.diff(values)
        growth = np.maximum(h[1:]/h[:-1], h[:-1]/h[1:])
        if not len(growth) or growth.max() <= 2*(1+1e-12):
            return values
        j = int(growth.argmax())
        large = j if h[j] > h[j+1] else j+1
        count = int(np.ceil(h[large]/(2*min(h[j], h[j+1]))))
        values = np.sort(np.r_[values, np.linspace(values[large], values[large+1], count+1)[1:-1]])
    raise ValueError('mesh growth did not settle')


def mesh_lines(n, control='ordinary'):
    if isinstance(n, bool) or not isinstance(n, int) or n not in MESHES or control not in CONTROLS:
        raise ValueError('declared integer mesh/control required')
    d = dimensions(); length = d['junctions_mm'][-1]
    maximum, dx = H/n, H/(2*n)
    air = 18. if control == 'boundary' else 12.
    xanchors = [-18., length+18., -12., length+12., *d['junctions_mm']]
    for centre in (-9., -6., length+6., length+9.):
        xanchors.extend((centre-dx, centre, centre+dx))
    xb = _balanced(xanchors, maximum)
    small = min(s['width'] for s in d['sections'])/(2*n)
    yanchors = [-air, 0., air]
    for width in [d['feed_width_mm'], *[s['width'] for s in d['sections']]]:
        for sign in (-1, 1):
            yanchors.extend((sign*(width/2-small/3), sign*(width/2+2*small/3)))
    yb = _balanced(yanchors, maximum)
    above = graded(air, dx, maximum)
    # The clipped final interval must also meet the first uniform PML cell.
    # Redistribute its last pair without moving the physical interface.
    if above[-1]-above[-2] < maximum/2:
        above[-2] = (above[-3]+above[-1])/2
    zb = np.r_[np.linspace(0., H, 2*n+1), H+above[1:]]
    return (np.r_[xb[0]-maximum*np.arange(8, 0, -1), xb, xb[-1]+maximum*np.arange(1, 9)],
            np.r_[yb[0]-maximum*np.arange(8, 0, -1), yb, yb[-1]+maximum*np.arange(1, 9)],
            np.r_[zb, zb[-1]+maximum*np.arange(1, 9)])


class SteppedSimulation(Simulation):
    def to_bundle(self, *args, **kwargs):
        raise ValueError('research MSL and contour probes have no bundle representation')


def build(n, port, kind='filter', loss='pec', control='ordinary', cap_s=None):
    if isinstance(port, bool) or port not in (1, 2) or kind not in KINDS or loss not in LOSSES:
        raise ValueError('declared source, kind and loss required')
    d = dimensions(); length = d['junctions_mm'][-1]
    x, y, z = mesh_lines(n, control)
    grid = [len(a) for a in (x, y, z)]; cells = int(np.prod(grid))
    if cells > 4_000_000:
        raise ValueError('research allocation exceeds four million cells')
    dt = (.35 if control == 'dt' else .5)/(C0*np.sqrt(sum(
        1/(np.diff(a).min()*1e-3)**2 for a in (x, y, z))))
    cap = CAP_S*(2 if control == 'time' else 1) if cap_s is None else float(cap_s)
    if not np.isfinite(cap) or cap <= 0:
        raise ValueError('positive finite time cap required')
    steps = math.ceil(cap/dt); end = END_DB-(10 if control == 'time' else 0)
    sim = SteppedSimulation(FREQUENCIES[0], FREQUENCIES[-1], excitation='dgauss',
        end_criteria_db=end, max_timesteps=steps, boundaries=['PML_8']*4+['PEC', 'PML_8'])
    for axis, lines in zip('xyz', (x, y, z)): sim.mesh.AddLine(axis, lines)
    sim.fdtd.SetTimeStep(float(dt)); sim.fdtd.SetOverSampling(OVERSAMPLING)
    sim.dielectric('substrate', ER, tan_d=TAN_D if loss == 'dielectric' else 0.,
        tan_d_freq=FC).AddBox([x[0], y[0], 0.], [x[-1], y[-1], H], priority=1)
    feed = sim.metal('feed'); wf = d['feed_width_mm']; plane = 9. if control == 'plane' else 6.
    ports = []
    for number, start, stop, centre in ((1, x[0], 0., -plane), (2, x[-1], length, length+plane)):
        direction = 1 if number == 1 else -1
        ports.append(sim.fdtd.AddMSLPort(number, feed, [start, -wf/2, H], [stop, wf/2, 0.],
            'x', 'z', excite=-1 if number == port else 0,
            FeedShift=abs(start-(stop-direction*12.)), MeasPlaneShift=abs(start-centre), priority=10))
    solids = ['substrate', 'feed']
    if kind == 'through':
        sim.metal('body').AddBox([0., -wf/2, H], [length, wf/2, H], priority=10); solids.append('body')
    else:
        for k, section in enumerate(d['sections']):
            name = f'section{k}'; width = section['width']
            sim.metal(name).AddBox([d['junctions_mm'][k], -width/2, H],
                [d['junctions_mm'][k+1], width/2, H], priority=10); solids.append(name)
    iy = np.flatnonzero(abs(y) < wf/2); iz = int(np.flatnonzero(z == H)[0]); indices = {}
    for endpoint, centre, direction in ((0, -plane, 1), (1, length+plane, -1)):
        ix = int(np.argmin(abs(x-centre)))
        np.testing.assert_allclose(x[ix-1:ix+2], [centre-H/(2*n), centre, centre+H/(2*n)], rtol=0, atol=1e-12)
        for j, sx in enumerate(x[ix-1:ix+2]):
            sim.csx.AddProbe(f'v{endpoint}_{j}', p_type=0).AddBox([sx, 0., H], [sx, 0., 0.])
        for loop, margin in enumerate((0, 1)):
            lo, hi = int(iy[0]-margin), int(iy[-1]+margin)
            ly, hy = (y[lo-1]+y[lo])/2, (y[hi]+y[hi+1])/2
            lz, hz = (z[iz-1]+H)/2, (H+z[iz+1])/2
            for j, sx in enumerate(((x[ix-1]+centre)/2, (centre+x[ix+1])/2)):
                name = f'i{endpoint}_{loop}_{j}'
                sim.csx.AddProbe(name, p_type=1, norm_dir=0, weight=direction).AddBox([sx, ly, lz], [sx, hy, hz])
                indices[name] = [[ix+j-1, lo-1, iz-1], [ix+j-1, hi, iz]]
    for prop in sim.csx.GetAllProperties(): prop.SetColor((128, 128, 128), alpha=prop.GetFillColor()[3])
    if sim.csx.Update(): raise ValueError('invalid native geometry')
    meta = dict(protocol='stepped-lowpass-v1', n=n, excited_port=port, kind=kind, loss=loss,
        control=control, dimensions=d, grid=grid, native_cells=cells,
        mesh_mm={a: v.tolist() for a, v in zip('xyz', (x, y, z))}, plane_mm=plane,
        declared_dt_s=float(dt), cap_s=cap, max_timesteps=steps, end_db=end, threads=THREADS,
        source_duration_s=excitation.dgauss_duration_s(FREQUENCIES[-1]), solids=solids,
        expected_current_indices=indices, native_copper=False)
    return sim, ports, meta


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
        geometry=meta['required_usage'] == op['required_usage'] and set(meta['required_usage']) == set(meta['solids'])
            and all(v and all(v) for v in meta['required_usage'].values()),
        probes=meta['actual_current_indices'] == meta['expected_current_indices'],
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
        required_usage=usage(setup, meta['solids']), input_sha256=sha(out/'operator-input.xml'))
    save(out/'operator.json', op)
    if (not 0 < meta['declared_dt_s'] <= .8*op['run'].get('timestep_s', 0.)
            or op['run'].get('grid') != meta['grid'] or set(op['required_usage']) != set(meta['solids'])
            or not all(v and all(v) for v in op['required_usage'].values())):
        raise ValueError('operator CFL/material-usage preflight failed')
    if identity() != meta['source'] or runtime_identity() != meta['runtime']:
        raise ValueError('source or runtime changed before fields')
    print(MARKER, flush=True)
    sim.run(str(out/'raw'), threads=THREADS, exact=True, echo=True, dump_statistics=True)
    stats = np.loadtxt(out/'raw/openEMS_stats.txt', comments='%')
    native = dict(cells=int(stats[0]), dt_s=float(stats[1]), timesteps=int(stats[2]),
        numerical_time_s=float(stats[3]), iteration_wall_s=float(stats[4]))
    meta.update(native=native, run=sim.run_stats, operator=op, required_usage=usage(sim, meta['solids']))
    vn, cn = probe_names(); values = []; tails = []; indices = {}; raw_hashes = {}
    for name in vn+cn:
        path = out/'raw'/name; data = np.loadtxt(path, comments='%', ndmin=2)
        trace_clock(data, meta, name in cn)
        values.append(np.asarray(UI_data([name], str(out/'raw'), FREQUENCIES).ui_f_val)[0])
        mask = data[:, 0] >= native['numerical_time_s']-.5e-9
        tails.append(float(10*np.log10(max(np.mean(data[mask, 1]**2), np.finfo(float).tiny)
            / max(np.max(data[:, 1]**2), np.finfo(float).tiny))))
        raw_hashes[name] = sha(path)
        if name in cn:
            header = '\n'.join(path.read_text().splitlines()[:3])
            indices[name] = [list(map(int, v)) for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]', header)]
    native_v, native_i = [], []
    for p in ports:
        p.CalcPort(str(out/'raw'), FREQUENCIES, ref_impedance=Z0)
        native_v.append(p.uf_tot); native_i.append(p.if_tot)
    meta.update(actual_current_indices=indices, terminal_tail_db=max(tails), raw_sha256=raw_hashes,
        source_trace=source_info(out/'raw/et', meta))
    np.savez_compressed(out/'data.npz', frequency_hz=FREQUENCIES,
        v=np.asarray(values[:6]).reshape(2, 3, -1), i=np.asarray(values[6:]).reshape(2, 2, 2, -1),
        native_v=native_v, native_i=native_i)
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
        if name in cn:
            indices = [list(map(int, v)) for v in re.findall(r'-> \[(\d+),(\d+),(\d+)\]', '\n'.join(path.read_text().splitlines()[:3]))]
            if indices != meta['expected_current_indices'][name]: raise ValueError('current indices changed')
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
    result['samples'] = [dict(f_hz=float(FREQUENCIES[k]), native_loss_db=float(-20*np.log10(abs(actual[k, 1, 0]))),
        line_loss_db=float(-20*np.log10(abs(target[k, 1, 0]))), prototype_loss_db=float(10*np.log10(1+(FREQUENCIES[k]/FC)**(2*ORDER))))
        for k in (50, 200, 350, 450)]
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
