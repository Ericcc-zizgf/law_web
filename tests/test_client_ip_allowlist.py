import unittest
from ipaddress import ip_network
from unittest.mock import patch

from legal_tool import web_app


class ClientIpAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.allowed_networks = (
            ip_network("60.250.71.45/32"),
            ip_network("61.222.117.53/32"),
            ip_network("59.125.121.41/32"),
            ip_network("60.250.71.43/32"),
        )

    def test_allowed_forwarded_ip_can_open_site(self):
        with patch.object(web_app, "ALLOWED_CLIENT_NETWORKS", self.allowed_networks):
            response = self.client.get(
                "/api/research/config",
                headers={"X-Forwarded-For": "60.250.71.45, 10.0.0.25"},
            )
        self.assertEqual(response.status_code, 200)

    def test_disallowed_forwarded_ip_is_rejected(self):
        with patch.object(web_app, "ALLOWED_CLIENT_NETWORKS", self.allowed_networks):
            response = self.client.get(
                "/api/research/config",
                headers={"X-Forwarded-For": "60.250.71.45, 1.1.1.1, 10.0.0.25"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "此服務僅允許指定網路 IP 使用")

    def test_health_check_stays_available(self):
        with patch.object(web_app, "ALLOWED_CLIENT_NETWORKS", self.allowed_networks):
            response = self.client.get(
                "/health",
                headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.25"},
            )
        self.assertEqual(response.status_code, 200)

    def test_empty_allowlist_keeps_local_development_open(self):
        with patch.object(web_app, "ALLOWED_CLIENT_NETWORKS", ()):
            response = self.client.get(
                "/api/research/config",
                environ_overrides={"REMOTE_ADDR": "1.1.1.1"},
            )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
