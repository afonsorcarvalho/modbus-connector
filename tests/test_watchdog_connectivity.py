import os
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
OPS = REPO / "ops" / "watchdog"


class WatchdogBase(unittest.TestCase):
    """Roda os scripts shell reais com ping/nmcli/systemctl falsos no PATH."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.state_dir = root / "state"
        self.shim_dir = root / "bin"
        self.calls = root / "calls.log"
        self.shim_dir.mkdir()
        self.calls.write_text("")

        # ping: resultado controlado por INTERNET_OK / VPN_OK (o alvo é o
        # ultimo argumento).
        self._shim("ping", '''
target=
for a in "$@"; do target="$a"; done
echo "ping $*" >> "$CALLS"
case "$target" in
  8.8.8.8|1.1.1.1) [ "${INTERNET_OK:-0}" = 1 ] && exit 0 || exit 1 ;;
  10.8.0.1) [ "${VPN_OK:-0}" = 1 ] && exit 0 || exit 1 ;;
  *) exit 1 ;;
esac
''')
        self._shim("nmcli",
                   'echo "nmcli $*" >> "$CALLS"; '
                   '[ "${NMCLI_OK:-1}" = 1 ] && exit 0 || exit 1')
        self._shim("systemctl", 'echo "systemctl $*" >> "$CALLS"; exit 0')
        self._shim("logger", 'exit 0')

        # Harness generico para exercitar a lib sem depender das trilhas reais.
        self.harness = root / "harness.sh"
        self.harness.write_text('''#!/bin/sh
set -u
TRACK="${H_TRACK:-test}"
REBOOT_AFTER="${H_REBOOT_AFTER:-180}"
REPAIR_EVERY="${H_REPAIR_EVERY:-60}"
probe() { [ "${H_PROBE_OK:-0}" = 1 ]; }
remediate() { echo "remediate" >> "$CALLS"; [ "${H_REMEDIATE_OK:-1}" = 1 ]; }
. "$WATCHDOG_LIB"
run_watchdog_check "${1:-test}"
''')
        self.harness.chmod(0o755)

    def _shim(self, name, body):
        p = self.shim_dir / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)

    def run_script(self, script, mode="test", now=None, **env):
        e = dict(os.environ)
        e["PATH"] = f"{self.shim_dir}:{e['PATH']}"
        e["CALLS"] = str(self.calls)
        e["WATCHDOG_STATE_DIR"] = str(self.state_dir)
        e["WATCHDOG_LIB"] = str(OPS / "lib" / "connectivity.sh")
        if now is not None:
            e["WATCHDOG_NOW"] = str(now)
        for k, v in env.items():
            e[k] = str(v)
        return subprocess.run(["/bin/sh", str(script), mode],
                              env=e, capture_output=True, text=True)

    def seed_state(self, track, first_fail, last_repair):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / f"{track}.state").write_text(
            f"{first_fail} {last_repair}\n")

    def calls_text(self):
        return self.calls.read_text()

    def state_text(self, track):
        f = self.state_dir / f"{track}.state"
        return f.read_text().strip() if f.exists() else None


class EngineTests(WatchdogBase):
    def test_probe_ok_resets_state(self):
        self.seed_state("test", 100, 100)
        r = self.run_script(self.harness, "test", H_PROBE_OK=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.state_text("test"))

    def test_probe_fail_returns_1(self):
        r = self.run_script(self.harness, "test", H_PROBE_OK=0)
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_first_repair_fires_remediation(self):
        r = self.run_script(self.harness, "repair", now=1000,
                            H_REBOOT_AFTER=180, H_REPAIR_EVERY=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("remediate", self.calls_text())
        self.assertEqual(self.state_text("test"), "1000 1000")

    def test_repair_within_cadence_does_not_refire(self):
        self.seed_state("test", 1000, 1000)
        r = self.run_script(self.harness, "repair", now=1030,
                            H_REBOOT_AFTER=180, H_REPAIR_EVERY=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("remediate", self.calls_text())
        self.assertEqual(self.state_text("test"), "1000 1000")

    def test_repair_after_cadence_refires(self):
        self.seed_state("test", 1000, 1000)
        r = self.run_script(self.harness, "repair", now=1070,
                            H_REBOOT_AFTER=180, H_REPAIR_EVERY=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("remediate", self.calls_text())
        self.assertEqual(self.state_text("test"), "1000 1070")

    def test_reboot_after_window(self):
        self.seed_state("test", 1000, 1000)
        r = self.run_script(self.harness, "repair", now=1200,
                            H_REBOOT_AFTER=180, H_REPAIR_EVERY=60)
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_remediate_skip_keeps_last_repair(self):
        r = self.run_script(self.harness, "repair", now=1000,
                            H_REBOOT_AFTER=180, H_REPAIR_EVERY=60,
                            H_REMEDIATE_OK=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state_text("test"), "1000 0")


class CheckInternetTests(WatchdogBase):
    SCRIPT = OPS / "watchdog.d" / "check-internet"

    def test_test_ok_when_internet_up(self):
        r = self.run_script(self.SCRIPT, "test", INTERNET_OK=1)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_test_fail_when_internet_down(self):
        r = self.run_script(self.SCRIPT, "test", INTERNET_OK=0)
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_repair_reconnects_wifi(self):
        r = self.run_script(self.SCRIPT, "repair", now=1000, INTERNET_OK=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nmcli device reconnect wlan0", self.calls_text())

    def test_repair_falls_back_to_networkmanager_restart(self):
        r = self.run_script(self.SCRIPT, "repair", now=1000,
                            INTERNET_OK=0, NMCLI_OK=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("systemctl restart NetworkManager", self.calls_text())

    def test_reboot_after_3min(self):
        self.seed_state("internet", 1000, 1000)
        r = self.run_script(self.SCRIPT, "repair", now=1181, INTERNET_OK=0)
        self.assertEqual(r.returncode, 1, r.stderr)
