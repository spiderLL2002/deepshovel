import importlib.util
from pathlib import Path
import socket
import unittest
from unittest.mock import patch


path = Path(__file__).resolve().parents[1] / "deploy/runtime/start-tunnel.py"
spec = importlib.util.spec_from_file_location("tunnel_launcher", path)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def resolved(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 7844))]


class TunnelLauncherTests(unittest.TestCase):
    def test_recognizes_current_and_legacy_fake_ranges_in_either_region(self):
        for address in ("28.0.0.171", "28.0.0.172", "198.18.0.1", "198.19.255.254"):
            for fake_host in launcher.REGIONS:
                with self.subTest(address=address, host=fake_host), patch.object(
                    launcher.socket, "getaddrinfo",
                    side_effect=lambda host, *_: resolved(address if host == fake_host else "198.41.192.7"),
                ):
                    self.assertTrue(launcher.has_fake_dns())

    def test_real_edge_dns_preserves_standard_discovery(self):
        with patch.object(launcher.socket, "getaddrinfo", return_value=resolved("198.41.192.7")), patch.object(
            launcher, "edge_addresses"
        ) as doh:
            self.assertEqual(launcher.command("cloudflared", "config.yml"), ["cloudflared", "tunnel", "--config", "config.yml", "run"])
            doh.assert_not_called()

    def test_new_fake_range_uses_real_endpoints_over_http2_without_forcing_a_bind(self):
        with patch.object(launcher.socket, "getaddrinfo", return_value=resolved("28.0.0.171")), patch.object(
            launcher, "edge_addresses", side_effect=[["198.41.192.7"], ["198.41.200.53"]]
        ) as doh, patch.object(launcher, "reachable_physical_address", return_value=None):
            args = launcher.command("cloudflared", "config.yml")
        self.assertEqual(args, [
            "cloudflared", "tunnel", "--config", "config.yml", "--protocol", "http2",
            "--edge", "198.41.192.7:7844", "--edge", "198.41.200.53:7844", "run",
        ])
        self.assertEqual([call.args[0] for call in doh.call_args_list], list(launcher.REGIONS))


if __name__ == "__main__":
    unittest.main()
