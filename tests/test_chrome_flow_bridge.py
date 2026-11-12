import json
import unittest

from chrome_flow_bridge import token_result


class BridgeResultTests(unittest.TestCase):
    def test_raw_and_json_chrome_results_are_accepted(self):
        fixture = "fixture_" + "x" * 120
        self.assertEqual(token_result(fixture), fixture)
        self.assertEqual(token_result(json.dumps(fixture)), fixture)

    def test_browser_error_does_not_become_a_verification_token(self):
        for result in ('{"error":"unavailable"}', '[chrome] evaluate failed', 'null'):
            with self.subTest(result=result), self.assertRaises(ValueError):
                token_result(result)


if __name__ == "__main__":
    unittest.main()
