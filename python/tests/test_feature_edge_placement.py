"""Final local refinement must not destroy a requested free-edge thirds cell."""
import unittest

import numpy as np
from fairbeam import Simulation


class FeatureEdgePlacementTests(unittest.TestCase):
    @staticmethod
    def fixture():
        sim=Simulation(1e9,2e9,boundaries=["MUR"]*6)
        metal=sim.metal("sheets")
        for lo,hi in ((-4.,-.3),(.3,4.)):
            points=np.array([[lo,-4.],[hi,-4.],[hi,4.],[lo,4.]])
            metal.AddPolygon(points.T,norm_dir="z",elevation=0,priority=10)
        return sim

    def test_final_refined_gap_keeps_its_free_edge_fraction(self):
        for count in (6,9,12):
            with self.subTest(count=count):
                sim=self.fixture()
                sim.auto_mesh(refine_features=True,feature_cells=count,edge_res=.6,min_cell=.001,pad=3)
                x=np.asarray(sim.mesh.GetLines(0))
                for c,wanted in ((-.3,1/3),(.3,2/3)):
                    below=x[x<c][-1];above=x[x>c][0]
                    self.assertFalse(np.any(abs(x-c)<1e-10))
                    self.assertAlmostEqual((c-below)/(above-below),wanted,places=7)
                self.assertTrue(all(f["resolved"] for f in sim.mesh_report["fine_features"]))

    def test_an_explicit_user_line_stays_exact_instead_of_becoming_thirds(self):
        sim=self.fixture()
        sim.mesh.AddLine("x",[-.3,.3])
        sim.auto_mesh(refine_features=True,feature_cells=9,edge_res=.6,min_cell=.001,pad=3)
        x=np.asarray(sim.mesh.GetLines(0))
        for c in (-.3,.3):self.assertTrue(np.any(abs(x-c)<1e-12))


if __name__=="__main__":unittest.main()
