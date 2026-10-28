"""Exercise the actual supervisor health functions with a deterministic clock."""
from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/runtime/run-services.sh'
SOURCE = SCRIPT.read_text()
FUNCTIONS = SOURCE[SOURCE.index('probe_health() {'):SOURCE.index('# Web starts immediately')]


class SupervisorHealthTests(unittest.TestCase):
    def run_shell(self, scenario):
        harness = r'''
set -euo pipefail
web_pid=10; compiler_pid=11
web_started_at=1; compiler_started_at=1
web_healthy_at=1; compiler_healthy_at=1
web_unhealthy_since=0; compiler_unhealthy_since=0
web_retry_delay=1; compiler_retry_delay=1
LOCAL_HEALTH_URL=http://web/health; COMPILER_PORT=8011
clock=1000; curl_exit=28; curl_output='000 5.000123'
date() { echo "$clock"; }
curl() {
  [[ "$*" == *'--max-time 5'* && "$*" == *'--output /dev/null'* ]] || exit 98
  [[ "$*" != *'/ready'* ]] || exit 99
  printf '%s' "$curl_output"
  return "$curl_exit"
}
service_log() { printf '%s\n' "$*"; }
stop_web() { echo STOP_WEB; }
stop_compiler() { echo STOP_COMPILER; }
schedule_web_restart() { web_unhealthy_since=0; }
schedule_compiler_restart() { compiler_unhealthy_since=0; }
'''
        result = subprocess.run(['/bin/bash'], input=harness + FUNCTIONS + scenario,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_short_stalls_survive_and_120_seconds_restart_only_failed_component(self):
        for component in ('web', 'compiler'):
            with self.subTest(component=component):
                output = self.run_shell(f'''
check_{component}_health
clock=1018; check_{component}_health
clock=1119; check_{component}_health
echo BEFORE_THRESHOLD
clock=1120; check_{component}_health
''')
                before, after = output.split('BEFORE_THRESHOLD')
                self.assertNotIn('STOP_', before)
                self.assertEqual(after.count('STOP_' + component.upper()), 1)
                self.assertNotIn('STOP_' + ('COMPILER' if component == 'web' else 'WEB'), output)
                self.assertIn('curl_exit=28 http_status=000 elapsed_s=5.000123', output)

    def test_success_resets_continuous_failure_window(self):
        output = self.run_shell('''
check_web_health
clock=1119; curl_exit=0; curl_output='200 0.001000'; check_web_health
clock=1200; curl_exit=28; check_web_health
clock=1319; check_web_health
echo BEFORE_THRESHOLD
clock=1320; check_web_health
''')
        before, after = output.split('BEFORE_THRESHOLD')
        self.assertNotIn('STOP_', before)
        self.assertIn('Web health recovered', before)
        self.assertEqual(after.count('STOP_WEB'), 1)

    def test_compiler_startup_grace_and_liveness_endpoint(self):
        output = self.run_shell('''
compiler_started_at=1000; compiler_healthy_at=0
clock=1239; check_compiler_health
[[ "$compiler_unhealthy_since" == 0 ]]
clock=1240; check_compiler_health
[[ "$compiler_unhealthy_since" == 1240 ]]
clock=1359; check_compiler_health
echo BEFORE_THRESHOLD
clock=1360; check_compiler_health
''')
        before, after = output.split('BEFORE_THRESHOLD')
        self.assertNotIn('STOP_', before)
        self.assertEqual(after.count('STOP_COMPILER'), 1)
        self.assertIn('http://127.0.0.1:${COMPILER_PORT}/health', FUNCTIONS)

    def test_metadata_does_not_log_unexpected_curl_output(self):
        output = self.run_shell('''
curl_output='sensitive-payload unexpected-value'; check_web_health
''')
        self.assertNotIn('sensitive-payload', output)
        self.assertNotIn('unexpected-value', output)
        self.assertIn('http_status=000 elapsed_s=unknown', output)


if __name__ == '__main__':
    unittest.main()
