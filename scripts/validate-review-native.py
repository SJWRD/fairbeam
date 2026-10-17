"""Bounded native equivalence control; not an antenna accuracy or mesh-convergence study.

Run with the openEMS Python environment: python scripts/validate-review-native.py --out <directory>
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import numpy as np
from fairbeam import Simulation
from fairbeam.dispersion import Dispersion, Lorentz
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out", type=Path, required=True)
root=parser.parse_args().out.resolve()
root.mkdir(parents=True, exist_ok=False)
runs=[]
for kind in ('static','lorentz-zero'):
    sim=Simulation(1e9,3e9,boundaries=['MUR']*6,max_timesteps=20000,end_criteria_db=-50)
    material=(sim.dielectric('slab',16) if kind=='static' else sim.dispersive('slab',Dispersion(eps_inf=16,eps_poles=(Lorentz(0,2e9,1e-9),))))
    material.AddBox(priority=0,start=[-10,-10,0.137],stop=[10,10,1.137])
    sim.lumped_port(1,50,[0,0,-1],[0,0,0.137],'z')
    sim.auto_mesh(cells_per_wavelength=20,pad=10)
    mesh=[np.asarray(sim.mesh.GetLines(a)) for a in 'xyz']
    sim.run(str(root/kind),threads=1,echo=False,engine='cpu',exact=True)
    assert sim.run_stats.get('converged') is True, sim.run_stats
    sim.evaluate(n_freq=31,pattern_freqs=[])
    port=sim._port_objs[0]
    runs.append((sim,mesh,np.asarray(port.uf_ref)/np.asarray(port.uf_inc)))
    (root/(kind+'.json')).write_text(json.dumps({'mesh':[m.tolist() for m in mesh],'run':sim.run_stats,'s11_re':runs[-1][2].real.tolist(),'s11_im':runs[-1][2].imag.tolist()},indent=2),encoding='utf8')
for a,b in zip(runs[0][1],runs[1][1]): np.testing.assert_array_equal(a,b)
diff=float(np.max(np.abs(runs[0][2]-runs[1][2])))
assert np.isfinite(diff) and diff<1e-10,diff
receipt={'kind':'bounded equivalence smoke, not mesh/antenna convergence','cells':int(np.prod([len(x)-1 for x in runs[0][1]])),'max_complex_s11_difference':diff,'run_stats':[r[0].run_stats for r in runs]}
(root/'native-equivalence.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
print(json.dumps(receipt,indent=2))
