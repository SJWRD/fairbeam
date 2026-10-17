# Chapter 7: qualified coupled-stripline comparison scopes

This record identifies examples by number and page and reports our own parameters, equations and measured results. It contains no textbook text, figures, tables, solution steps or PDF excerpts. Acquisitions used Fairbeam with bundled openEMS 0.37.0rc3 on Windows, serially with four threads. Raw records and immutable input manifests remain outside Git.

Only the two thin-PEC scopes associated with Example 7.7, p. 355, are qualified here: lossless complex S/relative phase and constant-electric-conductivity complex S/relative phase/attenuation. They are not a certificate for the complete physical example, finite copper, frequency-independent wideband loss tangent, fabricated launches, infinite directivity or arbitrary Designer measurements. Qualification means finite sampled agreement with frozen gates, both prescribed mesh changes and independent controls; no asymptotic convergence order is claimed.

## Own geometry and references

Our centered, homogeneous coupled stripline uses epsilon_r=2.2, full ground separation b=3.2 mm, f0=3 GHz, real 50-ohm power-wave references and 201 frequencies from 1 to 5 GHz. Coupling c=0.1 sets Ze=Z0*sqrt((1+c)/(1-c)) and Zo=Z0*sqrt((1-c)/(1+c)). Our thin-strip conformal model gives width 2.593827725 mm and gap 0.980788279 mm. Length is c0/(4*f0*sqrt(epsilon_r))=16.843336163 mm. The four physical ports are left-A, right-A, right-B and left-B; the intended coupled port for input 1 is port 4.

The lossless reference connects the independent even/odd line networks. For the positive-loss scope, requested electric conductivity is kappa=0.018358725924885 S/m (nominal tan(delta)=0.05 at 3 GHz); native serialization is 0.01835873 S/m. The independent reference uses epsilon_complex=epsilon_r-j*kappa/(omega*epsilon_0), gamma=j*(omega/c0)*sqrt(epsilon_complex), and complex modal impedances scaled by sqrt(epsilon_r/epsilon_complex). Reflection and isolation are not forced to zero. This material has tan(delta)=0.15/0.05/0.03 at 1/3/5 GHz, rather than a frequency-flat loss tangent.

Four separately excited field columns form B*A^-1. Chosen internal reference planes at x=+/-L/2 delimit the length-L uniform section; the same strips continue beyond these planes to the sources at +/-L and PML boundaries at +/-2L. These are electrical section boundaries, not fabricated transitions. Three voltage planes and two closed current contours provide independent recurrence/contour checks. The current-staggering correction comes from the measured end-to-end uniform transfer, not from an inserted analytical propagation constant or an S-parameter fit. No determinant normalization or clipping of measured attenuation is applied.

## Sampled targets and mesh changes

The primary caps are 9 ns / -100 dB, with an independent 13.5 ns / -110 dB time control. Spatial cases share their prospective clock; the time-step control reduces its CFL factor from 0.5 to 0.35. Every accepted source completes its excitation and native energy stop before the cap.

| Material / mesh | Max complex S target error | Max relative output-phase target error (degrees) | Max attenuation target error |
|---|---:|---:|---:|
| Lossless / 6 | 0.003640036 | 0.008974347 | outside this scope |
| Lossless / 8 | 0.002278240 | 0.007900650 | outside this scope |
| Lossless / 10 | 0.001332921 | 0.008442042 | outside this scope |
| Constant kappa / 6 | 0.003506710 | 0.010916421 | 0.0483223% |
| Constant kappa / 8 | 0.002196972 | 0.010339800 | 0.0677532% |
| Constant kappa / 10 | 0.001283649 | 0.006026595 | 0.0857774% |

Attenuation target errors are small but nonmonotonic. The limits establish sampled agreement and successive changes, not monotonic error reduction or an inferred discretization order.

| Material / transition | Max complex S change | Max relative-phase change (degrees) | Max attenuation change |
|---|---:|---:|---:|
| Lossless / 6 to 8 | 0.001362120 | 0.001226626 | outside this scope |
| Lossless / 8 to 10 | 0.000945337 | 0.001015460 | outside this scope |
| Constant kappa / 6 to 8 | 0.001310792 | 0.005574674 | 0.0611978% |
| Constant kappa / 8 to 10 | 0.000914094 | 0.007996442 | 0.0602736% |

Frozen limits are 0.005 / 0.5 degrees for S/phase target error, 0.0025 / 0.2 degrees for each mesh change, and 0.001 / 0.1 degrees for each independent control. Positive-loss attenuation target / mesh / control limits are 1% / 0.5% / 0.2%. Both material scopes pass all these limits and their contour, recurrence, transfer, reciprocity, incident-rank and power checks.

## Independent controls

| Control at mesh 6 | Lossless S / phase change (degrees) | Constant-kappa S / phase change (degrees) | Constant-kappa attenuation change |
|---|---:|---:|---:|
| Outer boundary | 0.000003157 / 0.001492003 | 0.000004785 / 0.001680509 | 0.00923381% |
| Sidewall | 0.000001873 / 0.001314819 | 0.000002172 / 0.002637581 | 0.00345539% |
| Source guard | 0.000029917 / 0.020262436 | 0.000025706 / 0.014635931 | 0.0512012% |
| Time / energy | 0.000009561 / 0.005639918 | 0.000005593 / 0.001099287 | 0.0204767% |
| Time step | 0.000005158 / 0.000711323 | 0.000016184 / 0.005385115 | 0.0517544% |

At 3 GHz the fine lossless input-1 column magnitudes are 0.001332921 / 0.995043805 / 0.000135542 / 0.099471578, with 89.998727-degree relative output phase. The positive-loss column magnitudes are 0.023819997 / 0.957213581 / 0.002287704 / 0.095790139. Its measured modal/direction attenuation values span 2.329418842 to 2.329778618 Np/m, against 2.330752610 Np/m analytically.

## Reproduction and provenance

[PR #153](https://github.com/ismailakdag/fairbeam/pull/153) contains the two opt-in test modules, complete reproduction commands, runtime identities, own geometry/results plots and failed earlier acquisition scopes. Lossless fields were acquired on source `4ec3a7e8f5ad5c7bcc15cb7fba40b15150a667a5`; positive-loss fields on `c3991636f59479a34ed40dcaae33df4b063e99f0`. The current header-only follow-up `934b2365587edb8f91cf0e4938d7dfd5090ba25b` does not relabel those fields as new acquisitions. Each scope contains 32 complete native sources; independent audits replay all raw spectra and frozen input hashes without modifying reports or matrices.

This documentation record references those public source snapshots; it does not require the fixture to be merged into `main`. To reproduce from a separate checkout, fetch the contributor's branch and select its pinned header-only source. Use the openEMS-enabled Python environment documented in the PR. Fresh acquisitions record their own actual source identities; they are not the historical fields listed above.

```powershell
# From a Fairbeam Git checkout; choose an unused worktree directory.
git fetch https://github.com/cgroceny/fairbeam.git cem/300-coupled-stripline-fixture
git worktree add --detach C:/study/coupled-reference 934b2365587edb8f91cf0e4938d7dfd5090ba25b
cd C:/study/coupled-reference/python
python -m unittest tests.test_coupled_stripline_fixture -q
python -m tests.test_coupled_stripline_fixture --preflight --out C:/study/coupled-plan-fresh
python -m tests.test_coupled_stripline_fixture --preflight --loss-tangent 0.05 --out C:/study/coupled-kappa-plan-fresh
```

Use the full acquisition commands and fresh directories in PR #153 for both material cases. Run sources serially, preserve rejected records, and remeasure prospective throughput before admitting a case over the local wall-time budget. A successful command is not itself numerical qualification. The local full Python suite retains eight existing Windows symlink-privilege errors; PR CI passes. These checks are distinct from the numerical gates above.

## Excluded applications

Examples 7.1 and 7.6 are calculation-only for this study. Wilkinson ([7.2, PR #89](https://github.com/ismailakdag/fairbeam/pull/89)), one-/multi-aperture guide couplers ([7.3, PR #91](https://github.com/ismailakdag/fairbeam/pull/91); [7.4, PR #133](https://github.com/ismailakdag/fairbeam/pull/133)), branch-line hybrid ([7.5, PR #151](https://github.com/ismailakdag/fairbeam/pull/151)), multi-section coupled stripline ([7.8, PR #154](https://github.com/ismailakdag/fairbeam/pull/154)) and ring hybrid ([7.9, PR #155](https://github.com/ismailakdag/fairbeam/pull/155)) are excluded from the accepted scopes until their own target, mesh, native stopping and independent controls pass. Their fixture PRs retain failures, pending controls and deferred refinements; no unqualified result is promoted here.

Finite copper for 7.7 is also excluded. Resolving the smallest skin depth produces prospective updates far above the unapproved 30-minute-per-source budget. Constant kappa or a coarse sheet approximation cannot silently replace that finite-volume case.
