import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'tunnel_start', Path(__file__).resolve().parents[1] / 'deploy/runtime/start-tunnel.py'
)
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


class TunnelStartTests(unittest.TestCase):
    def test_normal_dns_preserves_standard_cloudflared_discovery(self):
        with patch.object(tunnel, 'has_fake_dns', return_value=False):
            self.assertEqual(tunnel.command('/cloudflared', '/config.yml'),
                             ['/cloudflared', 'tunnel', '--config', '/config.yml', 'run'])

    def test_fake_dns_uses_resolved_endpoints_and_only_binds_this_process(self):
        with patch.object(tunnel, 'has_fake_dns', return_value=True), \
             patch.object(tunnel, 'physical_address', return_value='10.0.0.2'), \
             patch.object(tunnel.socket, 'create_connection'), \
             patch.object(tunnel, 'edge_addresses', side_effect=[['198.41.192.7'], ['198.41.200.7']]):
            args = tunnel.command('/cloudflared', '/config.yml')
        self.assertIn('--edge-bind-address', args)
        self.assertIn('10.0.0.2', args)
        self.assertIn('http2', args)
        self.assertIn('198.41.192.7:7844', args)
        self.assertIn('198.41.200.7:7844', args)
        self.assertEqual(args[-1], 'run')

    def test_failed_discovery_does_not_prevent_standard_tunnel_start(self):
        with patch.object(tunnel, 'has_fake_dns', return_value=True), \
             patch.object(tunnel, 'edge_addresses', side_effect=ValueError('DNS unavailable')):
            self.assertEqual(tunnel.command('/cloudflared', '/config.yml'),
                             ['/cloudflared', 'tunnel', '--config', '/config.yml', 'run'])

    def test_unreachable_physical_network_uses_current_route_with_real_edge_ips(self):
        for physical_error in (False, True):
            with self.subTest(physical_error=physical_error), \
                 patch.object(tunnel, 'has_fake_dns', return_value=True), \
                 patch.object(tunnel, 'physical_address', side_effect=ValueError('VPN route') if physical_error else None, return_value='10.0.0.2'), \
                 patch.object(tunnel.socket, 'create_connection', side_effect=TimeoutError), \
                 patch.object(tunnel, 'edge_addresses', return_value=['198.41.192.7']):
                args = tunnel.command('/cloudflared', '/config.yml')
            self.assertNotIn('--edge-bind-address', args)
            self.assertIn('http2', args)
            self.assertIn('198.41.192.7:7844', args)


if __name__ == '__main__':
    unittest.main()
