"""Own thin-PEC ring hybrid for Example 7.9, p. 367; opt-in research only.

The ideal circuit has ideal nodes. The field model retains circular bends and
four physical T-junctions. Native cylindrical coordinates never become a
Cartesian design/bundle. No gallery model is changed.
"""
from datetime import datetime, timezone
import math
import json
import os
from pathlib import Path
import sys

import numpy as np
import CSXCAD
from CSXCAD import ContinuousStructure
from openEMS import ports as native_ports
from openEMS import physical_constants as constants

from fairbeam import Simulation, multiport
from tests.coax_resonator_fixture import save, sha, runtime_identity as base_runtime
from tests.stripline_fixture import impedance

ROOT = Path(__file__).resolve().parents[2]
C0, B, ER, F0, Z0 = constants.C0, .8, 2.2, 3e9, 50.
FREQUENCIES = np.linspace(.5*F0, 1.5*F0, 201)
MESHES, PILOT_MESHES, CONTROL_MESH, THREADS = (4, 6, 8), (2,), 4, 4
CONTROLS = ('ordinary', 'boundary', 'inner', 'guard', 'seam', 'angular', 'time', 'dt')
SPATIAL_CONTROLS = CONTROLS[:6]
CAP_S, END_DB, DT_FACTOR = 9e-9, -100., .5
PLANE_OFFSETS = (3., 4., 6., 8.)
CALIBRATION_PAIRS = ((0, 2), (1, 3))
PROTOCOL = 'ring-hybrid-v4'
NATIVE_STEP_RATIO_MAX = .97
PROPAGATION_MARKER = 'FAIRBEAM-RING-HYBRID-PROPAGATION-BEGIN'
LIMITS = dict(s_target_abs=.005, phase_target_deg=.5, s_mesh_abs=.0025,
    phase_mesh_deg=.2, s_control_abs=.001, phase_control_deg=.1,
    contour_s_abs=.001, plane_s_abs=.001, recurrence_residual=.001,
    incident_condition=30., incident_floor=.02, transfer_condition=30.,
    transfer_floor=.02, transfer_det_abs=.001, transfer_symmetry_abs=.001,
    transfer_fit_rel=.001, reciprocity_abs=.001, singular_power_max=1.005)
SCOPE = ('Own homogeneous thin-PEC stripline ring and physical T-junctions, '
         'with measured uniform-feed reference-plane shifts. No finite copper, '
         'dielectric loss, fabricated launch or Designer/bundle certificate.')


def identity():
    names = ('tests/ring_hybrid_fixture.py', 'tests/test_ring_hybrid_fixture.py',
        'tests/stripline_fixture.py', 'tests/coax_resonator_fixture.py',
        'tests/test_coax_resonator.py', 'fairbeam/simulation.py',
        'fairbeam/excitation.py', 'fairbeam/multiport.py', 'fairbeam/network.py',
        'fairbeam/procutil.py')
    return {name: sha(ROOT/'python'/name) for name in names}


def runtime_identity():
    value = base_runtime()
    value.update(python=sys.version, executable=sys.executable, numpy=np.__version__,
        port_helpers_sha256=sha(native_ports.__file__), constants_sha256=sha(constants.__file__))
    folder = Path(CSXCAD.__file__).parent
    value['csxcad_bindings'] = {str(p.resolve()): sha(p) for p in
        sorted(folder.glob('*.pyd'))+sorted(folder.glob('*.so'))+[Path(CSXCAD.__file__)]}
    if os.name == 'nt':
        prefix = os.environ.get('OPENEMS_INSTALL_PATH')
        if not prefix:
            raise ValueError('explicit bundled runtime required')
        value['dependencies'] = {name: sha(Path(prefix)/name) for name in ('fparser.dll', 'nf2ff.dll')}
    return value


def width_for(z):
    if isinstance(z, bool) or not np.isfinite(z) or not 25 <= z <= 100:
        raise ValueError('declared positive stripline impedance required')
    lo, hi = .1, 5.
    for _ in range(64):
        mid = (lo+hi)/2
        if impedance(mid, ER) > z:
            lo = mid
        else:
            hi = mid
    return B*(lo+hi)/2


def geometry():
    length = C0/(4*F0*np.sqrt(ER))*1e3
    radius = 3*length/np.pi
    return dict(f0_hz=F0, epsilon_r=ER, b_mm=B, reference_ohm=Z0,
        ring_impedance_ohm=float(np.sqrt(2)*Z0), ring_radius_mm=float(radius),
        ring_width_mm=width_for(np.sqrt(2)*Z0), feed_width_mm=width_for(Z0),
        port_angles_rad=np.deg2rad([30., 90., 150., 210.]).tolist(),
        arc_quarter_lengths=[1, 1, 1, 3],
        plane_offsets_b=list(PLANE_OFFSETS), calibration_pairs=[list(p) for p in CALIBRATION_PAIRS],
        metal='zero-thickness PEC', junction='overlapping ring and constant-width straight feeds', scope=SCOPE)


def pulse():
    """Odd sine-modulated Gaussian: zero DC and suppressed outer band.

    The envelope spectrum is -24 dB at both comparison edges. Unlike cosine
    modulation, this odd signal has exactly zero DC; unlike differentiation,
    it does not reduce the lower edge by another f/f0 factor. This changes
    excitation, not the DUT or target.
    """
    tau = np.sqrt(24*np.log(10)/20)/(np.pi*(FREQUENCIES[-1]-F0))
    omega, t0 = 2*np.pi*F0, 5*tau
    u = f'((t-{t0:.17g})/{tau:.17g})'
    argument = f'({omega:.17g}*(t-{t0:.17g}))'
    expression = f'sin{argument}*exp(-{u}^2)'
    return dict(type='ring-odd-gaussian', f0_hz=F0, tau_s=float(tau), dc_free=True,
                duration_s=float(10*tau), envelope_edge_db=-24., expression=expression)


def reference(freq=FREQUENCIES):
    """Connect lossless three-way junctions with traveling-wave phase factors.

    This independent wave calculation contains no cotangent admittance pole.
    Port 1 drives outputs 2/4, with 3 isolated at the center frequency.
    """
    if np.iscomplexobj(freq):
        raise ValueError('real comparison frequencies required')
    freq = np.asarray(freq, float)
    if (freq.ndim != 1 or not freq.size or not np.isfinite(freq).all()
            or np.any((freq < FREQUENCIES[0]) | (freq > FREQUENCIES[-1]))):
        raise ValueError('finite frequencies in the declared comparison band required')
    admittance = 1/np.array([Z0, np.sqrt(2)*Z0, np.sqrt(2)*Z0])
    junction = 2*np.sqrt(admittance[:, None]*admittance[None, :])/admittance.sum()-np.eye(3)
    ee, ei, ii = np.zeros((4, 4)), np.zeros((4, 8)), np.zeros((8, 8))
    for node in range(4):
        ee[node, node] = junction[0, 0]
        ei[node, 2*node:2*node+2] = junction[0, 1:]
        ii[2*node:2*node+2, 2*node:2*node+2] = junction[1:, 1:]
    out = []
    for value in freq:
        connections = np.zeros((8, 8), complex)
        for node, multiple in enumerate((1, 1, 1, 3)):
            a, b = 2*node+1, 2*((node+1) % 4)
            connections[a, b] = connections[b, a] = np.exp(-1j*np.pi/2*value/F0*multiple)
        incoming = np.linalg.solve(np.eye(8)-connections@ii, connections@ei.T)
        out.append(ee+ei@incoming)
    return np.array(out)


def graded(span, first):
    values, step = [0.], first
    while values[-1] < span:
        values.append(min(span, values[-1]+step))
        step = min(B/2, step*1.25)
    if len(values) > 2 and values[-1]-values[-2] < first/2:
        values[-2] = (values[-3]+values[-1])/2
    return np.array(values)


def meshes(n, control):
    g = geometry();radius, width = g['ring_radius_mm'], g['ring_width_mm']
    delta, dr = width/n, B/n
    anchors = radius+np.array([-B, -width/2-2*delta/3, -width/2+delta/3,
                                0., width/2-delta/3, width/2+2*delta/3, B])
    middle = np.concatenate([np.linspace(a, b, max(1, math.ceil((b-a)/delta-1e-12))+1)[:-1]
                             for a, b in zip(anchors, anchors[1:])])
    inner = 8 if control == 'inner' else 6
    outer = 24 if control == 'boundary' else 20
    inward = graded((inner-1)*B, delta)
    radial = np.r_[radius-B-inward[:0:-1], middle,
                   radius+np.arange(n, outer*n+1)*dr]
    for offset in (*PLANE_OFFSETS, 12, 16, outer):
        j = int(np.argmin(abs(radial-(radius+offset*B))))
        radial[j] = radius+offset*B
    origin = np.pi/12 if control == 'seam' else 0.
    window = (g['feed_width_mm']/2+B/4)/(radius+3*B)
    angular = [origin, origin+2*np.pi]
    for angle in g['port_angles_rad']:
        angular.extend((angle-window, angle, angle+window))
    angular = np.unique(angular)
    fine = delta/(radius+8*B)/(2 if control == 'angular' else 1)
    coarse = 2*np.pi/(48*n)/(2 if control == 'angular' else 1)
    parts = []
    for a, b in zip(angular, angular[1:]):
        close = any(abs((a+b)/2-angle) <= window for angle in g['port_angles_rad'])
        step = fine if close else coarse
        parts.append(np.linspace(a, b, max(1, math.ceil((b-a)/step-1e-12))+1)[:-1])
    phi = np.r_[np.concatenate(parts), origin+2*np.pi]
    positive = graded(B/2, delta/3)
    z = np.r_[-positive[:0:-1], positive]
    if radial[0] <= 0 or any(np.any(np.diff(a) <= 0) for a in (radial, phi, z)):
        raise ValueError('positive annular radii and strictly increasing meshes required')
    return radial, phi, z


def cfl_step(axes):
    radial, phi, z = axes
    chord = 2*radial[0]*np.sin(np.diff(phi).min()/2)
    return 1e-3/(C0*np.sqrt(np.diff(radial).min()**-2+chord**-2+np.diff(z).min()**-2))


class RingSimulation(Simulation):
    def to_bundle(self, *args, **kwargs):
        raise ValueError('raw cylindrical research geometry has no Cartesian bundle')


def build(n, port, *, control='ordinary'):
    if (isinstance(n, bool) or not isinstance(n, int) or n not in MESHES+PILOT_MESHES
            or isinstance(port, bool) or not isinstance(port, int) or port not in range(1, 5)
            or control not in CONTROLS):
        raise ValueError('declared integer mesh, source and control required')
    g = geometry();radius = g['ring_radius_mm'];width = g['feed_width_mm']
    radial, phi, z = meshes(n, control)
    sim = RingSimulation(FREQUENCIES[0], FREQUENCIES[-1], excitation='dgauss',
        end_criteria_db=END_DB-(10 if control == 'time' else 0),
        boundaries=['PEC', 'PML_8', 'PEC', 'PEC', 'PEC', 'PEC'])
    stimulus = pulse()
    sim.fdtd.SetCustomExcite(stimulus['expression'], FREQUENCIES[-1], FREQUENCIES[-1])
    sim.excitation = {**stimulus, 'f_min': FREQUENCIES[0], 'f_max': FREQUENCIES[-1]}
    csx = ContinuousStructure(CoordSystem=1)
    sim.fdtd.SetCSX(csx);sim.csx, sim.mesh = csx, csx.GetGrid()
    sim.mesh.SetDeltaUnit(sim.unit);sim.mesh.SetMeshType(1);sim.fdtd.SetCoordSystem(1)
    for axis, lines in zip('xyz', (radial, phi, z)):
        sim.mesh.AddLine(axis, lines)
    sim.dielectric('homogeneous', ER).AddBox([radial[0], phi[0], z[0]], [radial[-1], phi[-1], z[-1]], priority=0)
    ground = sim.metal('grounds')
    for height in (-B/2, B/2):
        ground.AddBox([radial[0], phi[0], height], [radial[-1], phi[-1], height], priority=10)
    metal = sim.metal('ring_and_feeds')
    ring = metal.AddBox([radius-g['ring_width_mm']/2, phi[0], 0.],
                       [radius+g['ring_width_mm']/2, phi[-1], 0.], priority=10)
    ring.SetCoordinateSystem(1)
    for angle in g['port_angles_rad']:
        unit, normal = np.array([np.cos(angle), np.sin(angle)]), np.array([-np.sin(angle), np.cos(angle)])
        a, b = radius-g['ring_width_mm']/2, radial[-1]
        points = np.array([a*unit-width/2*normal, b*unit-width/2*normal,
                           b*unit+width/2*normal, a*unit+width/2*normal]).T
        metal.AddPolygon(points, 2, 0., priority=10).SetCoordinateSystem(0)
    heights = []
    for desired in (B/4, 3*B/8):
        j = int(np.searchsorted(z, desired));heights.append(float((z[j-1]+z[j])/2))
    if heights[0] == heights[1]:
        raise ValueError('independent current contours collapsed')
    source_radius = radius+(16 if control == 'guard' else 12)*B
    half_width = float(np.arcsin(width/(2*source_radius)))
    for number, angle in enumerate(g['port_angles_rad'], 1):
        lo, hi = angle-half_width, angle+half_width
        sim.lumped_port(number, Z0, [source_radius, lo, -B/2], [source_radius, hi, 0.], 'z',
            excite=number == port, group={'connection': 'parallel', 'members': [{
                'start': [source_radius, lo, 0.], 'stop': [source_radius, hi, B/2],
                'direction': 'z', 'polarity': -1}]})
        for k, offset in enumerate(PLANE_OFFSETS):
            plane = radius+offset*B
            index = int(np.argmin(abs(radial-plane)))
            if radial[index] != plane or not np.allclose(np.diff(radial[index-1:index+2]), B/n, rtol=0, atol=1e-13):
                raise ValueError('exact equally spaced local calibration planes required')
            for j, r in enumerate(radial[index-1:index+2]):
                for half, a, b, weight in ((0, -B/2, 0., -.5), (1, 0., B/2, .5)):
                    csx.AddProbe(f'v{number}_{k}_{j}_{half}', p_type=0, weight=weight).AddBox([r, angle, a], [r, angle, b])
            for j, r in enumerate((radial[index-1:index+1]+radial[index:index+2])/2):
                extent = float(np.arcsin((width/2+B/6)/r))
                for loop, height in enumerate(heights):
                    csx.AddProbe(f'i{number}_{k}_{j}_{loop}', p_type=1, norm_dir=0).AddBox(
                        [r, angle-extent, -height], [r, angle+extent, height])
    # Spatial controls share the worst prospective CFL; only the dt control changes it.
    bound = min(cfl_step(meshes(n, name)) for name in SPATIAL_CONTROLS)
    factor = .35 if control == 'dt' else DT_FACTOR
    dt = factor*bound;cap = CAP_S*(2 if control == 'time' else 1)
    steps = math.ceil(cap/dt);sim.max_timesteps = steps
    sim.fdtd.SetTimeStep(float(dt));sim.fdtd.SetNumberOfTimeSteps(steps);sim.fdtd.SetOverSampling(4)
    # Refresh coordinate input on primitives added after the cylindrical CSX
    # constructor. Update alone leaves new Cartesian polygons reading r/phi as
    # x/y in the bundled CSXCAD; the resulting feeds are absent from the mesh.
    csx.SetMeshType(1)
    if csx.Update():
        raise ValueError('invalid native geometry')
    native_lines = [len(radial), len(phi)+1, len(z)]
    meta = dict(protocol=PROTOCOL, n=n, excited_port=port, control=control, geometry=g, ports=sim.ports,
        mesh_mm_rad=dict(r=radial.tolist(), phi=phi.tolist(), z=z.tolist()),
        input_lines=[len(radial), len(phi), len(z)], native_lines=native_lines,
        reported_grid_lines=[len(radial), len(phi)-1, len(z)], native_cells=math.prod(native_lines),
        declared_dt_s=float(dt), max_timesteps=steps, cap_s=cap, end_db=sim.end_criteria_db,
        dt_factor=factor, local_dr_mm=B/n, source_radius_mm=source_radius,
        plane_radii_mm=[radius+x*B for x in PLANE_OFFSETS], ampere_heights_mm=heights,
        source_duration_s=stimulus['duration_s'], excitation=stimulus, threads=THREADS, scope=SCOPE)
    return sim, meta


def assemble(v, i):
    a, b = (v+Z0*i)/(2*np.sqrt(Z0)), (v-Z0*i)/(2*np.sqrt(Z0))
    incoming = a.transpose(2, 1, 0);singular = np.linalg.svd(incoming, compute_uv=False)
    floor = float(singular[:, -1].min()/max(singular[:, 0].max(), np.finfo(float).tiny))
    condition = float(np.max(singular[:, 0]/np.maximum(singular[:, -1], np.finfo(float).tiny)))
    if condition > LIMITS['incident_condition'] or floor < LIMITS['incident_floor']:
        raise ValueError('unexcited or ill-conditioned incoming wave matrix')
    return multiport.assemble_s(a, b, [1, 2, 3, 4], 4), dict(incident_condition=condition, incident_floor=floor)


def extract(v, i):
    """Local measured staggering, then inverse measured uniform-feed transfer.

    No reference gamma/Z or fitted phase is used. Each near/far separation
    equals near-to-node distance. The second pair independently checks the
    extrapolation; native junction reactance remains inside the DUT.
    """
    v, i = np.asarray(v, complex), np.asarray(i, complex)
    nf = len(FREQUENCIES)
    if (v.shape != (4, 4, 4, 3, nf) or i.shape != (4, 4, 4, 2, 2, nf)
            or not np.isfinite(v).all() or not np.isfinite(i).all()):
        raise ValueError('complete finite four-source, four-plane waves required')
    node_v, node_i = np.zeros((2, 2, 4, 4, nf), complex), np.zeros((2, 2, 4, 4, nf), complex)
    transfer = np.empty((2, 2, 4, nf, 2, 2), complex)
    halves = [];recurrence = 0.
    diagnostic = dict(transfer_det_abs=0., transfer_symmetry_abs=0., transfer_fit_rel=0.,
                      transfer_condition=0., transfer_floor=1.)
    for p in range(4):
        field = v[:, p];center = field[:, :, 1]
        denom = 2*np.sum(abs(center)**2, axis=0)
        scale = np.max(np.sum(abs(field)**2, axis=0), axis=1)
        if np.any(scale <= 0) or np.any(denom < .01*scale):
            raise ValueError('standing-wave node or missing feed excitation')
        r = np.sum(np.conj(center)*(field[:, :, 0]+field[:, :, 2]), axis=0)/denom
        half = np.sqrt((r+1)/2)
        if not np.isfinite(half).all() or np.any((abs(half) < .5) | (abs(half) > 1.5)):
            raise ValueError('invalid measured current interpolation factor')
        halves.append(half)
        recurrence = max(recurrence, float(abs(field[:, :, 0]+field[:, :, 2]-2*r*center).max()/abs(field).max()))
        for loop in (0, 1):
            current = i[:, p, :, :, loop].mean(axis=2)/half
            for pair, (near, far) in enumerate(CALIBRATION_PAIRS):
                x = np.stack((center[:, near], Z0*current[:, near])).transpose(2, 0, 1)
                y = np.stack((center[:, far], Z0*current[:, far])).transpose(2, 0, 1)
                singular = np.linalg.svd(x, compute_uv=False)
                condition = float(np.max(singular[:, 0]/np.maximum(singular[:, -1], np.finfo(float).tiny)))
                floor = float(singular[:, -1].min()/max(singular[:, 0].max(), np.finfo(float).tiny))
                if condition > LIMITS['transfer_condition'] or floor < LIMITS['transfer_floor']:
                    raise ValueError('ill-conditioned measured feed transfer')
                t = y@np.linalg.pinv(x);transfer[loop, pair, p] = t
                diagnostic['transfer_condition'] = max(diagnostic['transfer_condition'], condition)
                diagnostic['transfer_floor'] = min(diagnostic['transfer_floor'], floor)
                diagnostic['transfer_det_abs'] = max(diagnostic['transfer_det_abs'], float(abs(np.linalg.det(t)-1).max()))
                diagnostic['transfer_symmetry_abs'] = max(diagnostic['transfer_symmetry_abs'], float(abs(t[:, 0, 0]-t[:, 1, 1]).max()))
                diagnostic['transfer_fit_rel'] = max(diagnostic['transfer_fit_rel'], float(abs(y-t@x).max()/abs(y).max()))
                node = np.linalg.solve(t, x)
                node_v[loop, pair, :, p] = node[:, 0].T
                node_i[loop, pair, :, p] = -node[:, 1].T/Z0
    spectra = np.empty((2, 2, nf, 4, 4), complex);incoming = []
    for loop in (0, 1):
        for pair in (0, 1):
            spectra[loop, pair], row = assemble(node_v[loop, pair], node_i[loop, pair]);incoming.append(row)
    return spectra[0, 0], dict(contour_s_abs=float(abs(spectra[0, 0]-spectra[1, 0]).max()),
        plane_s_abs=float(abs(spectra[0, 0]-spectra[0, 1]).max()), recurrence_residual=recurrence,
        incident_condition=max(row['incident_condition'] for row in incoming),
        incident_floor=min(row['incident_floor'] for row in incoming), **diagnostic,
        local_current_half_factor=np.array(halves), measured_transfer=transfer, calibration_s=spectra)


def phase(s):
    s = np.asarray(s, complex)
    if s.shape != (len(FREQUENCIES), 4, 4) or not np.isfinite(s).all() or np.any(abs(s[:, (1, 3), 0]) < .01):
        raise ValueError('finite non-null output waves required')
    return np.angle(s[:, 3, 0]*np.conj(s[:, 1, 0]), deg=True)


def phase_change(a, b):
    return float(np.max(abs((phase(a)-phase(b)+180) % 360-180)))


def native_bound_ok(meta):
    record = meta.get('operator_preflight', {});run = record.get('run', {})
    bound = run.get('timestep_s', 0.)
    return bool(isinstance(bound, (float, int)) and np.isfinite(bound) and bound > 0
        and record.get('setup_only') is True and record.get('ratio_limit') == NATIVE_STEP_RATIO_MAX
        and record.get('planned_dt_s') == meta['declared_dt_s']
        and meta['declared_dt_s']/bound <= NATIVE_STEP_RATIO_MAX
        and run.get('grid') == meta['reported_grid_lines'] and run.get('threads') == THREADS
        and record.get('required_conductors_used') == [True]*5
        and run.get('engine') == 'cpu' and run.get('exact_endcriteria') is True
        and run.get('converged') is False and run.get('energy_trace') == [] and 'timesteps' not in run)


def conductor_usage(sim):
    metal = next(p for p in sim.csx.GetAllProperties() if p.GetName() == 'ring_and_feeds')
    primitives = metal.GetAllPrimitives()
    if len(primitives) != 5:
        raise ValueError('one ring and four feed primitives required')
    return [bool(p.GetPrimitiveUsed()) for p in primitives]


def operator_preflight(out, n, port, control='ordinary'):
    out = Path(out);sim, meta = build(n, port, control=control)
    sim.fdtd.SetTimeStep(0.);sim.fdtd.Write2XML(str(out/'operator-input.xml'))
    run = sim.run(str(out/'operator'), threads=THREADS, exact=True, echo=True, setup_only=True)
    record = dict(setup_only=True, ratio_limit=NATIVE_STEP_RATIO_MAX, planned_dt_s=meta['declared_dt_s'],
                  required_conductors_used=conductor_usage(sim),
                  input_sha256=sha(out/'operator-input.xml'), run=run)
    record['passes'] = native_bound_ok({**meta, 'operator_preflight': record})
    save(out/'operator.json', record)
    if not record['passes']:
        raise ValueError('native stability or required conductor admission failed')
    return record


def trace_clock(data, meta):
    data = np.asarray(data, float);native = meta['native']
    stride = int(1/(2*FREQUENCIES[-1]*native['dt_s']*4))
    if (data.ndim != 2 or len(data) < 3 or data.shape[1] < 2 or not np.isfinite(data).all() or stride < 1
            or np.any(np.diff(data[:, 0]) <= 0)
            or not np.allclose(np.diff(data[:, 0]), stride*native['dt_s'], rtol=2e-5, atol=native['dt_s']*1e-5)
            or data[-1, 0] > native['numerical_time_s']+stride*native['dt_s']):
        raise ValueError('finite native traces on the declared sampling clock required')
    return dict(samples=len(data), first_s=float(data[0, 0]), last_s=float(data[-1, 0]), stride_steps=stride)


def source_trace_info(path, meta):
    data = np.loadtxt(path, comments='%', ndmin=2);dt = meta['native']['dt_s']
    if (data.ndim != 2 or data.shape[1] != 2 or not np.isfinite(data).all()
            or len(data) not in (meta['max_timesteps'], meta['max_timesteps']+1)):
        raise ValueError('complete finite allocated source trace required')
    if not np.allclose(data[:, 0], np.arange(len(data))*dt, rtol=6e-6, atol=dt*1e-6):
        raise ValueError('source timestamps differ beyond native text precision')
    p = meta['excitation'];time = np.arange(len(data))*dt
    offset = time-5*p['tau_s']
    expected = np.sin(2*np.pi*p['f0_hz']*offset)*np.exp(-(offset/p['tau_s'])**2)
    error = float(abs(data[:, 1]-expected).max()/max(abs(expected).max(), np.finfo(float).tiny))
    if not np.isfinite(error) or error > 6e-6:
        raise ValueError('native source differs from the declared odd Gaussian')
    return dict(sha256=sha(path), samples=len(data), last_s=float(data[-1, 0]),
                timestamp_print_rtol=6e-6, waveform_relative_error_max=error, waveform_limit=6e-6)


def quality(meta):
    run, native, header = meta['run'], meta['native'], meta.get('native_header', {})
    return {k: bool(v) for k, v in dict(
        grid=native['cells'] == meta['native_cells'] and run.get('grid') == meta['reported_grid_lines'],
        clock=abs(native['dt_s']/meta['declared_dt_s']-1) < 1e-8,
        operator=native_bound_ok(meta),
        geometry=meta.get('required_conductors_used') == [True]*5,
        time=native['timesteps'] > 0 and abs(native['numerical_time_s']/(native['dt_s']*native['timesteps'])-1) < 1e-8,
        header=header.get('version') == 'v0.37.0-rc3' and header.get('threads') == THREADS
            and header.get('nyquist_interval') == int(1/(2*FREQUENCIES[-1]*native['dt_s']))
            and header.get('excitation_timesteps') == meta['max_timesteps'],
        source=native['numerical_time_s'] > meta['source_duration_s'],
        energy=run.get('converged') and run.get('exact_endcriteria') and not run.get('hit_timestep_limit')
            and run.get('threads') == THREADS and native['timesteps'] < meta['max_timesteps']).items()}


def probe_names():
    return ([f'v{p}_{k}_{j}_{h}' for p in range(1, 5) for k in range(4) for j in range(3) for h in range(2)],
            [f'i{p}_{k}_{j}_{loop}' for p in range(1, 5) for k in range(4) for j in range(2) for loop in range(2)])


def probe_spectra(raw):
    vn, cn = probe_names()
    v = np.asarray(native_ports.UI_data(vn, str(raw), FREQUENCIES).ui_f_val).reshape(4, 4, 3, 2, -1).sum(axis=3)
    i = np.asarray(native_ports.UI_data(cn, str(raw), FREQUENCIES).ui_f_val).reshape(4, 4, 2, 2, -1)
    return v, i


def acquire(out, n, port, control='ordinary'):
    out = Path(out);out.mkdir(parents=True, exist_ok=False)
    sim, meta = build(n, port, control=control);source, runtime = identity(), runtime_identity()
    sim.fdtd.Write2XML(str(out/'input.xml'))
    meta.update(source_sha256=source, runtime=runtime, limits=LIMITS,
                input_sha256=sha(out/'input.xml'), started_utc=datetime.now(timezone.utc).isoformat())
    save(out/'declared.json', meta)
    meta['operator_preflight'] = operator_preflight(out, n, port, control)
    meta['operator_sha256'] = sha(out/'operator.json')
    if source != identity() or runtime != runtime_identity():
        raise RuntimeError('source/runtime changed during native setup')
    save(out/'declared.json', meta);print(PROPAGATION_MARKER, flush=True)
    sim.run(str(out/'raw'), threads=THREADS, exact=True, echo=True, dump_statistics=True)
    stats = np.loadtxt(out/'raw/openEMS_stats.txt', comments='%')
    meta.update(required_conductors_used=conductor_usage(sim),
                native=dict(cells=int(stats[0]), dt_s=float(stats[1]), timesteps=int(stats[2]),
                numerical_time_s=float(stats[3]), iteration_wall_s=float(stats[4])), run=sim.run_stats)
    vn, cn = probe_names();v, current = probe_spectra(out/'raw')
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
    out = Path(out);meta = json.loads((out/'report.json').read_text(encoding='utf-8'))
    if meta['source_sha256'] != identity() or meta['runtime'] != runtime_identity() or meta['limits'] != LIMITS:
        raise ValueError('source/runtime/limits changed; retain separate actual acquisition epochs')
    for name, key in (('input.xml', 'input_sha256'), ('waves.npz', 'waves_sha256'), ('operator.json', 'operator_sha256')):
        if sha(out/name) != meta[key]:
            raise ValueError('changed native input, operator receipt or wave data')
    operator = json.loads((out/'operator.json').read_text())
    if operator != meta['operator_preflight'] or sha(out/'operator-input.xml') != operator['input_sha256'] or not native_bound_ok(meta):
        raise ValueError('invalid native stability admission')
    _, expected = build(meta['n'], meta['excited_port'], control=meta['control'])
    if any(meta[k] != value for k, value in json.loads(json.dumps(expected)).items()):
        raise ValueError('changed geometry, planes or acquisition parameters')
    vn, cn = probe_names()
    if set(meta['raw_traces']) != set(vn+cn) or source_trace_info(out/'raw/et', meta) != meta['source_trace']:
        raise ValueError('changed or missing source/probe traces')
    for name, record in meta['raw_traces'].items():
        path = out/'raw'/name
        if dict(sha256=sha(path), **trace_clock(np.loadtxt(path, comments='%', ndmin=2), meta)) != record:
            raise ValueError('changed raw trace or sampling clock')
    with np.load(out/'waves.npz') as data:
        np.testing.assert_array_equal(data['frequency_hz'], FREQUENCIES)
        v, i = data['v'].copy(), data['i'].copy()
    raw_v, raw_i = probe_spectra(out/'raw')
    np.testing.assert_array_equal(v, raw_v);np.testing.assert_array_equal(i, raw_i)
    return meta, v, i, quality(meta)


def matrix(root):
    root = Path(root);records = [read(root/f'port{p}') for p in range(1, 5)];first = records[0][0]
    keys = ('n', 'control', 'geometry', 'mesh_mm_rad', 'cap_s', 'end_db', 'dt_factor', 'source_radius_mm')
    if any(r[0]['excited_port'] != p or any(r[0][k] != first[k] for k in keys) for p, r in enumerate(records, 1)):
        raise ValueError('mixed acquisition cohort')
    s, diagnostic = extract(np.array([r[1] for r in records]), np.array([r[2] for r in records]))
    target = reference()
    result = {k: first[k] for k in keys if k != 'mesh_mm_rad'}
    metrics = dict(s_target_abs=float(abs(s-target).max()), phase_target_deg=phase_change(s, target),
        reciprocity_abs=float(abs(s-s.transpose(0, 2, 1)).max()),
        singular_power_max=float((np.linalg.svd(s, compute_uv=False)[:, 0]**2).max()),
        **{k: value for k, value in diagnostic.items() if np.isscalar(value)})
    lower = ('incident_floor', 'transfer_floor')
    result.update(all_native_complete=all(all(r[3].values()) for r in records),
                  native_quality=[r[3] for r in records], qualified=False, **metrics)
    result['target_passes'] = bool(result['all_native_complete']
        and all(value <= LIMITS[k] for k, value in metrics.items() if k not in lower)
        and all(metrics[k] >= LIMITS[k] for k in lower))
    np.savez_compressed(root/'matrix.npz', frequency_hz=FREQUENCIES, s=s, reference=target, **diagnostic)
    save(root/'comparison.json', result)
    return s, result


def study(root):
    root = Path(root);records, spectra, rejected = {}, {}, []
    required = [(f'n{n}', n, 'ordinary') for n in MESHES]
    required += [(f'n{CONTROL_MESH}_{control}', CONTROL_MESH, control) for control in CONTROLS[1:]]
    for name, n, control in required:
        try:
            s, record = matrix(root/name)
            if (record['n'], record['control']) != (n, control):
                raise ValueError('cohort does not match the declared mesh/control path')
            records[name], spectra[name] = record, s
        except (FileNotFoundError, ValueError, AssertionError, np.linalg.LinAlgError) as error:
            rejected.append(dict(name=name, reason=str(error)))
    changes = []
    for a, b in zip(MESHES, MESHES[1:]):
        left, right = f'n{a}', f'n{b}'
        if left in spectra and right in spectra:
            ds, dp = float(abs(spectra[right]-spectra[left]).max()), phase_change(spectra[right], spectra[left])
            changes.append(dict(meshes=[a, b], s_abs=ds, phase_deg=dp,
                passes=ds <= LIMITS['s_mesh_abs'] and dp <= LIMITS['phase_mesh_deg']))
    controls = {};nominal = f'n{CONTROL_MESH}'
    for control in CONTROLS[1:]:
        name = f'n{CONTROL_MESH}_{control}'
        if nominal in spectra and name in spectra:
            ds, dp = float(abs(spectra[name]-spectra[nominal]).max()), phase_change(spectra[name], spectra[nominal])
            controls[control] = dict(s_abs=ds, phase_deg=dp, native=records[name],
                passes=records[name]['target_passes'] and ds <= LIMITS['s_control_abs'] and dp <= LIMITS['phase_control_deg'])
    qualified = (not rejected and len(records) == len(required) and len(changes) == 2
        and len(controls) == len(CONTROLS)-1 and all(r['target_passes'] for r in records.values())
        and all(r['passes'] for r in changes) and all(r['passes'] for r in controls.values()))
    result = dict(protocol=PROTOCOL, source=identity(), runtime=runtime_identity(), scope=SCOPE, limits=LIMITS,
        cohorts=records, missing_or_rejected=rejected, mesh_changes=changes, controls=controls,
        qualified=bool(qualified), finite_copper_qualified=False, dielectric_loss_qualified=False,
        fabricated_launch_qualified=False, designer_bundle_qualified=False)
    save(root/'comparison.json', result)
    return result
