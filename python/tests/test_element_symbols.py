"""Persisted RLC glyph settings are validated but do not change circuit values."""
import json
import tempfile
import unittest
from pathlib import Path
from fairbeam.design import blank_design, check_design, read_design, DesignError
from fairbeam.preview import build_preview
from fairbeam.design_checks import lint


class ElementSymbols(unittest.TestCase):
    def design(self):
        d = blank_design("symbol_test", "Symbol test")
        d["resistors"] = [dict(name="C1", C=1e-12, start=[0, 0, 0], stop=[0, 0, 1], direction="z")]
        return d

    def test_invalid_symbols_rejected_by_server_schema(self):
        for symbol in ("invalid", None, 1, [], {}):
            with self.subTest(symbol=symbol):
                d = self.design(); d["resistors"][0]["symbol"] = symbol
                with self.assertRaises(DesignError) as error:
                    check_design(d)
                self.assertEqual(error.exception.where, "resistors[0].symbol")
                findings = lint(d, {})
                self.assertTrue(any(x["path"] == "resistors[0].symbol" and x["code"] == "resistor-r" for x in findings))

    def test_legacy_and_explicit_symbols_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"symbol.design.json"
            for symbol in (None, "auto", "resistor", "capacitor", "inductor", "network"):
                with self.subTest(symbol=symbol):
                    d = self.design()
                    if symbol is not None: d["resistors"][0]["symbol"] = symbol
                    path.write_text(json.dumps(d))
                    loaded = read_design(path)
                    self.assertEqual(loaded["resistors"][0]["symbol"], symbol or "auto")
                    bundle = build_preview(None, {}, design=loaded)["bundle"]
                    element = bundle["lumped_elements"][0]
                    self.assertEqual(element["symbol"], symbol or "auto")
                    self.assertEqual(element["C"], 1e-12)
                    self.assertNotIn("L", element)
