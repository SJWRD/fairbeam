"""Export the driven FEM result into the shared designer Results format.

Geometry comes from the admitted design; no openEMS simulation is performed.
FEM completion is deliberately not represented as FDTD energy convergence.
"""
import copy
import numpy as np

from .elmer_driven import settings_mesh, validate_result


def result_bundle(design, result):
    from .preview import build_preview

    validate_result(result)
    settings = result['settings']
    mesh = settings_mesh(design, settings)
    drawing = copy.deepcopy(design)
    if settings.get('approximate_metals'):
        for material in drawing['materials']:
            if material.get('kind') == 'metal':
                material.pop('conductivity', None)
                material.pop('thickness', None)
    bundle = build_preview(None, {}, design=drawing)['bundle']
    bundle.pop('preview', None)
    bundle['name'] += ' · Elmer FEM'
    axes = [np.unique(np.asarray(mesh['nodes'])[:,i])*1000 for i in range(3)]
    steps = np.concatenate([np.diff(axis) for axis in axes])
    bundle['mesh'] = dict(zip('xyz', [axis.tolist() for axis in axes]))
    bundle['mesh'].update(cells=[len(axis)-1 for axis in axes], total_cells=len(mesh['elements']),
                          min_cell=float(steps.min()), max_cell=float(steps.max()))
    bundle['domain'] = dict(min=[float(a[0]) for a in axes], max=[float(a[-1]) for a in axes])
    bundle['nf2ff_box'] = copy.deepcopy(bundle['domain'])
    bundle['nf2ff_center'] = [0,0,0]
    bundle['half_space'] = None
    bundle['solver'] = dict(engine='Elmer', method='Frequency-domain FEM (experimental)',
        excitation=dict(type='Harmonic rectangular terminal', f_min=settings['f_min_hz'],
                        f_max=settings['f_max_hz'], dc_free=True),
        boundaries={k:'First-order absorbing' for k in ('x-','x+','y-','y+','z-','z+')},
        end_criteria_db=0, max_timesteps=0)
    # The shared viewer must not infer qualified gain or an FDTD convergence verdict.
    bundle['run'] = None
    bundle['experimental'] = dict(solver='Elmer', precision_converged=False,
        limitations=result['limitations'], settings=settings,
        input_sha256=result.get('input_sha256'))
    port = bundle['ports'][0]
    width_axis = next(i for i in range(3) if i != 'xyz'.index(design['ports'][0]['direction']))
    port['start'][width_axis] -= settings.get('feed_width_mm',1)/2
    port['stop'][width_axis] += settings.get('feed_width_mm',1)/2
    # Elmer VectorHelmholtz: exp(-i omega t); openEMS/shared RF plots: exp(+i omega t).
    # Keep native raw output intact, conjugate once at the shared-format boundary.
    reflection = np.asarray(result['s11_real']) - 1j*np.asarray(result['s11_imag'])
    bundle['experimental']['phasor_convention'] = 'exp(+i omega t); conjugated from native Elmer'
    denominator = 1-reflection
    if np.any(np.abs(denominator)<1e-12):
        raise ValueError('Cannot export singular input impedance')
    impedance = mesh['impedance']*(1+reflection)/denominator
    ff = result['farfield']
    directivity = 10*np.log10(np.maximum(np.asarray(ff['directivity']),1e-30))
    bundle['results'] = dict(frequency=result['frequency_hz'], ports={str(port['number']):dict(
        s11_re=reflection.real.tolist(), s11_im=reflection.imag.tolist(), zin_re=impedance.real.tolist(),
        zin_im=impedance.imag.tolist(), z_ref=mesh['impedance'])}, bands=[], signals={}, farfield=[dict(
            f=ff['frequency_hz'], theta=ff['theta_deg'], phi=ff['phi_deg'],
            directivity_dbi=directivity.tolist(), dmax_dbi=ff['peak_directivity_dbi'],
            rad_efficiency=None, prad_w=ff['radiated_power_w'], pacc_w=None,
            qa_warnings=result['limitations'], port=port['number'])])
    return bundle
