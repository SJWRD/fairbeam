"""Explicit local targets must refine features, not just increase total cells."""
import unittest

import numpy as np

from fairbeam import Simulation
from fairbeam.mesh_refinement import measure_features, refinement_caps, required_cells


class FeatureResolutionTargetTests(unittest.TestCase):
    def test_caps_and_measurements_use_the_same_requested_target(self):
        feature = dict(kind="gap", width=.6, lo=[0,0,0], hi=[.6,5,0], normal=[1,0,0])
        default = refinement_caps([feature])
        for count in (6,9,12):
            with self.subTest(count=count):
                requested = {**feature, "target_cells":count}
                cap = refinement_caps([requested])
                self.assertAlmostEqual(cap[0][0][2], .6/(1.1*count))
                self.assertLess(cap[0][0][2], default[0][0][2])
                measured = measure_features([requested], [np.linspace(0,.6,7),np.arange(6),np.array([-1.,0.,1.])])[0]
                self.assertEqual(measured["required_cells"],count)
                self.assertEqual(measured["resolved"],count==6)

    def test_none_preserves_slanted_connectivity_default(self):
        feature=dict(kind="strip",normal=[.6,.8,0],width=.4,lo=[0,0,0],hi=[6,8,0])
        self.assertEqual(required_cells(feature),1)
        self.assertEqual(required_cells({**feature,"target_cells":6}),6)

    @staticmethod
    def fixture():
        sim=Simulation(1e9,2e9,boundaries=["MUR"]*6)
        metal=sim.metal("metal")
        metal.AddBox([-4,-4,0],[-.3,4,0],priority=10)
        metal.AddBox([.3,-4,0],[4,4,0],priority=10)
        return sim

    def test_explicit_targets_are_honored_in_an_actual_mesh(self):
        counts=[]
        for target in (3,6,9):
            sim=self.fixture()
            report=sim.auto_mesh(refine_features=True,feature_cells=target,pad=3)
            features=[f for f in report["fine_features"] if f["kind"]=="gap"]
            self.assertTrue(features)
            self.assertTrue(all(f["resolved"] and f["required_cells"]==target for f in features))
            self.assertEqual(report["settings"]["feature_cells"],target)
            counts.append(report["total_cells"])
        self.assertTrue(all(a<b for a,b in zip(counts,counts[1:])))

    def test_default_mesh_is_unchanged_with_explicit_none(self):
        a,b=self.fixture(),self.fixture()
        a.auto_mesh(refine_features=True,pad=3)
        report=b.auto_mesh(refine_features=True,feature_cells=None,pad=3)
        for axis in range(3):
            np.testing.assert_array_equal(a.mesh.GetLines(axis),b.mesh.GetLines(axis))
        self.assertNotIn("feature_cells",report["settings"])

    def test_disabled_refinement_reports_the_target_without_changing_grid(self):
        a,b=self.fixture(),self.fixture()
        a.auto_mesh(refine_features=False,pad=3)
        report=b.auto_mesh(refine_features=False,feature_cells=12,pad=3)
        for axis in range(3):
            np.testing.assert_array_equal(a.mesh.GetLines(axis),b.mesh.GetLines(axis))
        self.assertTrue(all(f["required_cells"]==12 for f in report["fine_features"]))
        self.assertFalse(report["fine_feature_refinement"]["enabled"])

    def test_minimum_cell_does_not_turn_an_unfulfilled_target_into_success(self):
        report=self.fixture().auto_mesh(refine_features=True,feature_cells=12,min_cell=.2,pad=3)
        gaps=[f for f in report["fine_features"] if f["kind"]=="gap"]
        self.assertTrue(gaps)
        self.assertTrue(all(f["required_cells"]==12 and not f["resolved"] for f in gaps))

    def test_invalid_targets_are_rejected_before_meshing(self):
        for value in (True,False,2,0,-1,3.0,"6",float("nan"),float("inf")):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,"feature_cells"):
                self.fixture().auto_mesh(feature_cells=value)


if __name__=="__main__":unittest.main()
