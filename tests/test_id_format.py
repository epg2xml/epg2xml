import unittest

from epg2xml.id_format import render_id_format


class TestIDFormat(unittest.TestCase):
    def setUp(self):
        self.values = {
            "ServiceId": "AB-12",
            "Source": "KT",
            "No": " 101 ",
        }

    def test_basic_interpolation(self):
        rendered = render_id_format("{ServiceId}.{Source.lower()}", self.values)
        self.assertEqual(rendered, "AB-12.kt")

    def test_string_methods(self):
        rendered = render_id_format('{No.strip().replace(" ", "")}-{Source.lower()}', self.values)
        self.assertEqual(rendered, "101-kt")

    def test_blocks_addition_operator(self):
        with self.assertRaises(Exception):
            render_id_format('{No.strip().replace(" ", "") + "-" + Source.lower()}', self.values)

    def test_blocks_dangerous_calls(self):
        with self.assertRaises(Exception):
            render_id_format("{__import__('os').system('echo pwned')}", self.values)

    def test_blocks_non_whitelisted_attribute_access(self):
        with self.assertRaises(Exception):
            render_id_format("{ServiceId.__class__}", self.values)


if __name__ == "__main__":
    unittest.main()
