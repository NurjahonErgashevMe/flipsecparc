from __future__ import annotations

import unittest

from .cookies import cookies_list_to_header


class CookiesHeaderTests(unittest.TestCase):
    def test_list_to_header(self) -> None:
        data = [
            {"name": "_CIAN_GK", "value": "abc-123"},
            {"name": "DMIR_AUTH", "value": "token%2Fvalue"},
        ]
        header = cookies_list_to_header(data)
        self.assertEqual(header, "_CIAN_GK=abc-123; DMIR_AUTH=token%2Fvalue")

    def test_skips_invalid_entries(self) -> None:
        self.assertEqual(cookies_list_to_header([{}, {"name": "x", "value": "1"}]), "x=1")
        self.assertEqual(cookies_list_to_header([]), "")


if __name__ == "__main__":
    unittest.main()
