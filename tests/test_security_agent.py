import unittest

from security_agent import analyze


class SecurityAgentTests(unittest.TestCase):
    def test_detects_repeated_failures_once_per_time_window(self):
        logs = [
            {"created_at": f"2026-11-23T10:{minute:02d}:00Z", "actor_alias": "a1b2c3d4", "action": "login", "outcome": "failure", "provider_calls": 0}
            for minute in range(10, 15)
        ]
        findings = analyze(logs)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "repeated_failures")
        self.assertEqual(len(findings[0]["fingerprint"]), 64)

    def test_detects_expensive_failed_search(self):
        finding = analyze([{"created_at": "2026-11-23T10:00:00Z", "actor_alias": "guest", "action": "search", "outcome": "failure", "provider_calls": 10}])[0]
        self.assertEqual(finding["severity"], "high")


if __name__ == "__main__":
    unittest.main()
