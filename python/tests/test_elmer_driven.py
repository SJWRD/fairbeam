import copy
import math
import unittest
import numpy as np
from fairbeam.design import blank_design
from fairbeam.elmer_driven import prepare, huygens_pattern, C0, MU0, EPS0


class DrivenTests(unittest.TestCase):
    def test_pec_approximation_is_explicit_and_preserves_conductivity(self):
        d = blank_design('p', 'Patch')
        metal = next(m for m in d['materials'] if m['kind'] == 'metal')
        metal.update(conductivity=5.8e7, thickness=.035)
        original = copy.deepcopy(d)
        with self.assertRaises(ValueError):
            prepare(d)
        mesh = prepare(d, approximate_metals=True)
        reference = prepare(blank_design('p', 'Patch'))
        self.assertEqual(mesh['elements'], reference['elements'])
        self.assertEqual(d, original)

    def test_patch_preserves_input_and_loss(self):
        d=blank_design('p','Patch'); original=copy.deepcopy(d)
        m=prepare(d,cell_mm=15,padding_mm=35,feed_width_mm=1)
        self.assertEqual(d,original)
        self.assertEqual({b[0] for b in m['boundaries']},{1,2,3})
        expected=2*math.pi*2.45e9*EPS0*3.38*.0027
        self.assertTrue(any(math.isclose(mat[2],expected) for mat in m['materials']))
        self.assertTrue(all(len(e[1])==8 for e in m['elements']))

    def test_bad_inputs_and_resource_limits(self):
        for kwargs in ({'cell_mm':1e-12},{'padding_mm':float('nan')},{'feed_width_mm':0}):
            with self.assertRaises(ValueError):prepare(blank_design('p','Patch'),**kwargs)
        d=blank_design('p','Patch');d['ports'].append(copy.deepcopy(d['ports'][0]));d['ports'][1]['number']=2
        with self.assertRaises(ValueError):prepare(d)

    def test_huygens_matches_hertzian_dipole_power_and_pattern(self):
        frequency=2.45e9;k=2*math.pi*frequency/C0;eta=MU0*C0
        for radius in (.6*C0/frequency,1.3*C0/frequency):
            theta=(np.arange(36)+.5)*math.pi/36;phi=np.arange(72)*2*math.pi/72
            tt,pp=np.meshgrid(theta,phi,indexing='ij');tt=tt.ravel();pp=pp.ravel()
            n=np.array([np.sin(tt)*np.cos(pp),np.sin(tt)*np.sin(pp),np.cos(tt)]).T
            et=np.array([np.cos(tt)*np.cos(pp),np.cos(tt)*np.sin(pp),-np.sin(tt)]).T
            ep=np.array([-np.sin(pp),np.cos(pp),np.zeros_like(pp)]).T
            common=-1j*k*np.sin(tt)*np.exp(1j*k*radius)/(4*math.pi*radius)
            e=et*(eta*common*(1+1j/(k*radius)-1/(k*radius)**2))[:,None]
            h=ep*(common*(1+1j/(k*radius)))[:,None]
            areas=radius**2*np.sin(tt)*math.pi/36*2*math.pi/72
            ff=huygens_pattern(radius*n,n,areas,e,h,frequency)
            expected=eta*k*k/(12*math.pi)
            self.assertLess(abs(ff['radiated_power_w']/expected-1),.01)
            self.assertAlmostEqual(ff['peak_directivity_dbi'],10*math.log10(1.5),delta=.02)


if __name__=='__main__':unittest.main()
