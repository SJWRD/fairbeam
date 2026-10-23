"""Synthetic planar-feed import refusals; no solver or private result files."""
import json
from pathlib import Path
import unittest
from fairbeam.cst_import import import_cst


def brick(name, lo, hi, material):
    return '\n'.join(['With Brick','.Reset',f'.Name "{name}"','.Component "c"',f'.Material "{material}"',
        *(f'.{axis}range "{lo[i]}", "{hi[i]}"' for i,axis in enumerate('XYZ')),'.Create','End With'])


def macro(cpw=False, direction=1, outside=False):
    # Rotate the same synthetic cross-section through all three propagation axes.
    order=[(direction+2)%3,direction,(direction+1)%3] # width, propagation, height
    def rot(p):
        q=[0.,0.,0.]
        for i,j in enumerate(order):q[j]=p[i]
        return q
    def box(name,a,b,mat='PEC'):return brick(name,rot(a),rot(b),mat)
    plane=20 if outside else 0
    rows=['With Material','.Reset','.Name "slab"','.Type "Normal"','.Epsilon "3.55"','.Create','End With',
          box('substrate',[-10,-5,0],[10,10,1.5],'slab'),box('strip',[-1,-5,1.5],[1,10,1.535])]
    if cpw:
        rows += [box('left',[-10,-5,1.5],[-1.7,10,1.535]),box('right',[1.7,-5,1.5],[10,10,1.535])]
    else:rows += [box('ground',[-10,-5,-.035],[10,10,0])]
    a,b=rot([-5,plane,-.1]),rot([5,plane,5])
    rows += ['With Port','.Reset','.PortNumber "1"','.Coordinates "Free"',
             f'.Orientation "{"xyz"[direction]}min"','.NumberOfModes "1"',
             *(f'.{ax}range "{a[i]}", "{b[i]}"' for i,ax in enumerate('XYZ')),'.Create','End With']
    return '\n'.join(rows)


class PlanarPortGuard(unittest.TestCase):
    def test_microstrip_and_cpw_are_not_te10_in_any_axis(self):
        for cpw in (False,True):
            for direction in range(3):
                with self.subTest(cpw=cpw,direction=direction):
                    res=import_cst(macro(cpw,direction))
                    self.assertEqual(res['design']['ports'],[])
                    self.assertEqual(res['report']['refused'],1)
                    self.assertGreaterEqual(len(res['design']['parts']),3)
                    self.assertTrue(any('planar transmission-line port' in n['message'] for n in res['report']['notes']))

    def test_geometry_must_actually_cross_the_port_plane(self):
        res=import_cst(macro(outside=True))
        self.assertEqual(res['design']['ports'][0]['type'],'waveguide')

    def test_metal_walls_with_full_aperture_width_are_not_strips(self):
        text=macro().replace('Xrange "-1", "1"','Xrange "-10", "10"')
        res=import_cst(text)
        self.assertEqual(res['design']['ports'][0]['mode'],'TE10')

    def test_message_has_matching_english_and_turkish_ui_entries(self):
        res=import_cst(macro());root=Path(__file__).resolve().parents[2]
        en=json.loads((root/'src/i18n/en.json').read_text(encoding='utf-8'))
        tr=json.loads((root/'src/i18n/tr.json').read_text(encoding='utf-8'))
        msg=en['home.importCst.planarPortRefused']
        self.assertTrue(any(n['message']=='not imported: '+msg for n in res['report']['notes']))
        self.assertTrue(tr['home.importCst.planarPortRefused'])

if __name__=='__main__':unittest.main()
