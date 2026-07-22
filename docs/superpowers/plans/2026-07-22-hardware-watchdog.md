# Hardware Watchdog com Monitoramento de Conectividade — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configurar o daemon `watchdog` do Debian para alimentar o `/dev/watchdog` e, via dois scripts independentes em `/etc/watchdog.d/`, monitorar internet e VPN, remediando a rede primeiro e reiniciando a placa por hardware só se a queda persistir além da janela de cada trilha (internet 3 min, VPN 10 min).

**Architecture:** Um motor de estados compartilhado em shell (`connectivity.sh`, instalado em `/usr/local/lib/`) é *sourced* por dois scripts finos (`check-internet`, `check-vpn`) que definem alvo, tempos e as funções `probe()`/`remediate()`. O daemon chama cada script como `test` (0 = saudável) e, na falha, `repair <errno>` (0 = tratado/segue, ≠0 = reboot). O escalonamento usa o timestamp da primeira falha num arquivo de estado em tmpfs — nunca bloqueando.

**Tech Stack:** POSIX shell (`/bin/sh`), daemon `watchdog` do Debian, NetworkManager (`nmcli`), systemd (`openvpn@client.service`). Testes em Python `unittest` via `subprocess` com shims no `PATH` (sem dependências novas).

## Global Constraints

- **Runner de testes:** `python -m unittest discover -s tests -v` (é o que a CI usa). Sem pytest, sem libs novas. Precisa passar em Python 3.11/3.12/3.13 no Ubuntu.
- **Contrato de não-bloqueio:** todo script test/repair termina bem abaixo de `test-timeout=10s` e de `watchdog-timeout=15s`. Nunca esperar reconexão bloqueando; a verificação de recuperação é no próximo ciclo.
- **Códigos de saída:** `exit 0` = saudável/tratado (mantém a placa viva); `exit 1` = falha → o daemon reinicia a placa.
- **Overrides para teste (lidos pelos scripts):** `WATCHDOG_STATE_DIR` (default `/run/watchdog`), `WATCHDOG_LIB` (default `/usr/local/lib/watchdog-connectivity.sh`), `WATCHDOG_NOW` (default `$(date +%s)`).
- **PATH:** os scripts fazem `PATH="$PATH:/usr/sbin:/sbin:/usr/bin:/bin"` (append) para achar as ferramentas em produção sem sobrepor shims de teste prependados.
- **Parâmetros por trilha (fixos):** internet → alvos `8.8.8.8 1.1.1.1`, `REBOOT_AFTER=180`, `REPAIR_EVERY=60`; vpn → alvo `10.8.0.1`, `REBOOT_AFTER=600`, `REPAIR_EVERY=120`.
- **watchdog.conf:** `watchdog-device=/dev/watchdog`, `watchdog-timeout=15`, `interval=10`, `test-directory=/etc/watchdog.d`, `test-timeout=10`, `repair-timeout=10`.
- **Local no repo:** `ops/watchdog/`. Testes em `tests/test_watchdog_connectivity.py`.

## File Structure

- `ops/watchdog/lib/connectivity.sh` — motor de estados compartilhado (função `run_watchdog_check`). Instalado em `/usr/local/lib/watchdog-connectivity.sh` (fora do `test-directory` para o daemon não tentar executá-lo).
- `ops/watchdog/watchdog.d/check-internet` — trilha internet: config + `probe`/`remediate`, faz *source* da lib.
- `ops/watchdog/watchdog.d/check-vpn` — trilha VPN: config + `probe`/`remediate` com guarda de internet.
- `ops/watchdog/watchdog.conf` — config do daemon.
- `ops/watchdog/install.sh` / `uninstall.sh` — instalação/remoção idempotente no dispositivo.
- `ops/watchdog/README.md` — o que é, instalar, ajustar tempos, testar, segurança.
- `tests/test_watchdog_connectivity.py` — base de teste (shims + harness) e as classes de teste de cada trilha e do empacotamento.

---

### Task 1: Motor de estados compartilhado (`connectivity.sh`) + base de testes

**Files:**
- Create: `ops/watchdog/lib/connectivity.sh`
- Create/Test: `tests/test_watchdog_connectivity.py`

**Interfaces:**
- Consumes: nada (primeira task).
- Produces:
  - Lib shell que espera, do script que a faz *source*, as variáveis `TRACK` (str), `REBOOT_AFTER` (int seg), `REPAIR_EVERY` (int seg) e as funções `probe()` (0 = conectado) e `remediate()` (0 = remediação disparada, ≠0 = pulada). Exporta a função `run_watchdog_check "$mode"` (`mode` ∈ {`test`, `repair`}).
  - Arquivo de estado `"$WATCHDOG_STATE_DIR/$TRACK.state"` com uma linha `"<first_fail> <last_repair>"` (epochs).
  - Base de teste `WatchdogBase` (unittest.TestCase) com: `run_script(script, mode="test", now=None, **env)` → `subprocess.CompletedProcess`; `calls_text()` → str; `state_text(track)` → str|None. Cria shims (`ping`, `nmcli`, `systemctl`, `logger`) e um `harness.sh` genérico em tmpdir no `setUp`.

- [ ] **Step 1: Escrever o teste que falha (base + testes do motor)**

Create `tests/test_watchdog_connectivity.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m unittest tests.test_watchdog_connectivity -v`
Expected: FAIL/ERROR — a lib `ops/watchdog/lib/connectivity.sh` não existe, então o `. "$WATCHDOG_LIB"` falha e os testes do motor não passam.

- [ ] **Step 3: Implementar a lib**

Create `ops/watchdog/lib/connectivity.sh`:

```sh
#!/bin/sh
# Motor compartilhado do connectivity-watchdog, sourced pelos scripts
# /etc/watchdog.d/check-*.
#
# O script que faz source DEVE definir antes de chamar run_watchdog_check:
#   TRACK         - nome curto, usado no arquivo de estado (ex.: "internet")
#   REBOOT_AFTER  - segundos de falha continua ate pedir reboot
#   REPAIR_EVERY  - segundos minimos entre remediacoes
#   probe()       - retorna 0 se conectado, !=0 caso contrario
#   remediate()   - dispara remediacao; retorna 0 se disparou, !=0 se pulou
#
# O daemon watchdog(8) chama cada script como:
#   <script> test            -> exit 0 saudavel, exit 1 falha
#   <script> repair <errno>  -> exit 0 tratado/keep-alive, exit 1 => reboot
#
# CONTRATO: nunca bloquear. Comandos usam timeouts curtos; um ciclo termina
# bem abaixo do test-timeout/repair-timeout do watchdog.conf.

# Garante os diretorios de ferramentas do sistema, mas deixa shims de teste
# (prependados no PATH) vencerem.
PATH="$PATH:/usr/sbin:/sbin:/usr/bin:/bin"

STATE_DIR="${WATCHDOG_STATE_DIR:-/run/watchdog}"

_now() { echo "${WATCHDOG_NOW:-$(date +%s)}"; }

_log() {
    logger -t "watchdog-conn" "$1" 2>/dev/null || true
    echo "watchdog-conn: $1" >&2
}

run_watchdog_check() {
    mode="$1"
    state_file="$STATE_DIR/$TRACK.state"

    case "$mode" in
        repair)
            mkdir -p "$STATE_DIR" 2>/dev/null || true
            now=$(_now)
            if [ -f "$state_file" ]; then
                read first_fail last_repair < "$state_file"
            else
                first_fail="$now"
                last_repair=0
            fi
            : "${first_fail:=$now}"
            : "${last_repair:=0}"

            elapsed=$(( now - first_fail ))
            if [ "$elapsed" -ge "$REBOOT_AFTER" ]; then
                _log "$TRACK caido ha ${elapsed}s (>= ${REBOOT_AFTER}s) - pedindo reboot por hardware"
                exit 1
            fi

            if [ $(( now - last_repair )) -ge "$REPAIR_EVERY" ]; then
                if remediate; then
                    last_repair="$now"
                    _log "$TRACK remediacao disparada (caido ${elapsed}s/${REBOOT_AFTER}s)"
                else
                    _log "$TRACK remediacao pulada (caido ${elapsed}s/${REBOOT_AFTER}s)"
                fi
            fi

            echo "$first_fail $last_repair" > "$state_file"
            exit 0
            ;;
        *)
            if probe; then
                rm -f "$state_file" 2>/dev/null || true
                exit 0
            fi
            exit 1
            ;;
    esac
}
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m unittest tests.test_watchdog_connectivity -v`
Expected: PASS — os 7 testes de `EngineTests` passam.

- [ ] **Step 5: Commit**

```bash
git add ops/watchdog/lib/connectivity.sh tests/test_watchdog_connectivity.py
git commit -m "feat(watchdog): motor de estados compartilhado + base de testes"
```

---

### Task 2: Trilha de internet (`check-internet`)

**Files:**
- Create: `ops/watchdog/watchdog.d/check-internet`
- Modify: `tests/test_watchdog_connectivity.py` (append `CheckInternetTests`)

**Interfaces:**
- Consumes: `run_watchdog_check` da lib (Task 1); `WatchdogBase` do arquivo de teste.
- Produces: script executável que, chamado como `test`/`repair`, monitora `8.8.8.8`/`1.1.1.1`, remedia com `nmcli device reconnect wlan0` (fallback `systemctl restart NetworkManager`) e pede reboot após 180s.

- [ ] **Step 1: Escrever o teste que falha (append ao arquivo de teste)**

Append em `tests/test_watchdog_connectivity.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m unittest tests.test_watchdog_connectivity.CheckInternetTests -v`
Expected: FAIL/ERROR — `ops/watchdog/watchdog.d/check-internet` não existe.

- [ ] **Step 3: Implementar o script**

Create `ops/watchdog/watchdog.d/check-internet`:

```sh
#!/bin/sh
# watchdog test/repair da conectividade de internet publica.
# Pede reboot da placa apos REBOOT_AFTER segundos de falha continua.
set -u

TRACK="internet"
TARGETS="8.8.8.8 1.1.1.1"   # sucesso se QUALQUER um responder
REBOOT_AFTER=180            # 3 minutos
REPAIR_EVERY=60             # re-tenta remediacao no maximo a cada 60s

probe() {
    for t in $TARGETS; do
        if ping -c 2 -W 2 "$t" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

remediate() {
    if nmcli device reconnect wlan0 >/dev/null 2>&1; then
        return 0
    fi
    systemctl restart NetworkManager >/dev/null 2>&1
    return 0
}

. "${WATCHDOG_LIB:-/usr/local/lib/watchdog-connectivity.sh}"
run_watchdog_check "${1:-test}"
```

Torne executável:

```bash
chmod +x ops/watchdog/watchdog.d/check-internet
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m unittest tests.test_watchdog_connectivity.CheckInternetTests -v`
Expected: PASS — os 5 testes passam.

- [ ] **Step 5: Commit**

```bash
git add ops/watchdog/watchdog.d/check-internet tests/test_watchdog_connectivity.py
git commit -m "feat(watchdog): trilha de internet (check-internet) com reboot em 3min"
```

---

### Task 3: Trilha de VPN (`check-vpn`) com guarda de internet

**Files:**
- Create: `ops/watchdog/watchdog.d/check-vpn`
- Modify: `tests/test_watchdog_connectivity.py` (append `CheckVpnTests`)

**Interfaces:**
- Consumes: `run_watchdog_check` da lib (Task 1); `WatchdogBase` do arquivo de teste.
- Produces: script executável que monitora `10.8.0.1`, remedia com `systemctl restart openvpn@client.service` **apenas se a internet estiver de pé** (guarda), e pede reboot após 600s.

- [ ] **Step 1: Escrever o teste que falha (append ao arquivo de teste)**

Append em `tests/test_watchdog_connectivity.py`:

```python
class CheckVpnTests(WatchdogBase):
    SCRIPT = OPS / "watchdog.d" / "check-vpn"

    def test_test_ok_when_vpn_up(self):
        r = self.run_script(self.SCRIPT, "test", VPN_OK=1)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_test_fail_when_vpn_down(self):
        r = self.run_script(self.SCRIPT, "test", VPN_OK=0)
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_repair_restarts_openvpn_when_internet_up(self):
        r = self.run_script(self.SCRIPT, "repair", now=1000,
                            VPN_OK=0, INTERNET_OK=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("systemctl restart openvpn@client.service",
                      self.calls_text())

    def test_guard_skips_openvpn_when_internet_down(self):
        r = self.run_script(self.SCRIPT, "repair", now=1000,
                            VPN_OK=0, INTERNET_OK=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("openvpn", self.calls_text())
        # remediacao pulada -> last_repair permanece 0
        self.assertEqual(self.state_text("vpn"), "1000 0")

    def test_reboot_after_10min(self):
        self.seed_state("vpn", 1000, 1000)
        r = self.run_script(self.SCRIPT, "repair", now=1601,
                            VPN_OK=0, INTERNET_OK=1)
        self.assertEqual(r.returncode, 1, r.stderr)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m unittest tests.test_watchdog_connectivity.CheckVpnTests -v`
Expected: FAIL/ERROR — `ops/watchdog/watchdog.d/check-vpn` não existe.

- [ ] **Step 3: Implementar o script**

Create `ops/watchdog/watchdog.d/check-vpn`:

```sh
#!/bin/sh
# watchdog test/repair da conectividade do tunel OpenVPN.
# Pede reboot da placa apos REBOOT_AFTER segundos de falha continua.
set -u

TRACK="vpn"
TARGET="10.8.0.1"
REBOOT_AFTER=600            # 10 minutos
REPAIR_EVERY=120           # re-tenta remediacao no maximo a cada 120s
INTERNET_TARGETS="8.8.8.8 1.1.1.1"

probe() {
    ping -c 2 -W 2 "$TARGET" >/dev/null 2>&1
}

_internet_up() {
    for t in $INTERNET_TARGETS; do
        if ping -c 1 -W 2 "$t" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

remediate() {
    # Guarda: se a internet tambem caiu, a trilha de internet e dona da
    # recuperacao. Reiniciar o tunel sobre link morto e inutil - so contar.
    if ! _internet_up; then
        return 1
    fi
    systemctl restart openvpn@client.service >/dev/null 2>&1
    return 0
}

. "${WATCHDOG_LIB:-/usr/local/lib/watchdog-connectivity.sh}"
run_watchdog_check "${1:-test}"
```

Torne executável:

```bash
chmod +x ops/watchdog/watchdog.d/check-vpn
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m unittest tests.test_watchdog_connectivity.CheckVpnTests -v`
Expected: PASS — os 5 testes passam.

- [ ] **Step 5: Commit**

```bash
git add ops/watchdog/watchdog.d/check-vpn tests/test_watchdog_connectivity.py
git commit -m "feat(watchdog): trilha de VPN (check-vpn) com guarda de internet e reboot em 10min"
```

---

### Task 4: Empacotamento — `watchdog.conf`, `install.sh`, `uninstall.sh`, `README.md`

**Files:**
- Create: `ops/watchdog/watchdog.conf`
- Create: `ops/watchdog/install.sh`
- Create: `ops/watchdog/uninstall.sh`
- Create: `ops/watchdog/README.md`
- Modify: `tests/test_watchdog_connectivity.py` (append `PackagingTests`)

**Interfaces:**
- Consumes: os artefatos das Tasks 1–3 (a lib e os dois scripts) como origem da instalação.
- Produces: bundle instalável no dispositivo. `install.sh` copia lib → `/usr/local/lib/watchdog-connectivity.sh`, scripts → `/etc/watchdog.d/`, conf → `/etc/watchdog.conf`, e faz `systemctl enable --now watchdog`.

- [ ] **Step 1: Escrever o teste que falha (append ao arquivo de teste)**

Append em `tests/test_watchdog_connectivity.py`:

```python
class PackagingTests(unittest.TestCase):
    def test_shell_syntax_valid(self):
        for rel in ["lib/connectivity.sh", "watchdog.d/check-internet",
                    "watchdog.d/check-vpn", "install.sh", "uninstall.sh"]:
            p = OPS / rel
            r = subprocess.run(["/bin/sh", "-n", str(p)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{rel}: {r.stderr}")

    def test_watchdog_conf_has_required_keys(self):
        conf = (OPS / "watchdog.conf").read_text()
        for needle in ["watchdog-device", "watchdog-timeout", "interval",
                       "test-directory", "/etc/watchdog.d",
                       "test-timeout", "repair-timeout"]:
            self.assertIn(needle, conf, f"faltou {needle!r} no watchdog.conf")

    def test_check_scripts_are_executable_in_repo(self):
        for rel in ["watchdog.d/check-internet", "watchdog.d/check-vpn"]:
            p = OPS / rel
            self.assertTrue(os.access(p, os.X_OK), f"{rel} nao e executavel")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m unittest tests.test_watchdog_connectivity.PackagingTests -v`
Expected: FAIL/ERROR — `watchdog.conf`, `install.sh` e `uninstall.sh` ainda não existem.

- [ ] **Step 3: Criar os artefatos de empacotamento**

Create `ops/watchdog/watchdog.conf`:

```
# Config do daemon watchdog do Debian para o connectivity-watchdog.
# Alimenta o /dev/watchdog e roda os scripts de /etc/watchdog.d/.

watchdog-device = /dev/watchdog
watchdog-timeout = 15          # HW reinicia se nao alimentado por 15s
interval        = 10           # alimenta + roda os testes a cada 10s

test-directory  = /etc/watchdog.d
test-timeout    = 10           # tempo max. de um script test/repair
repair-timeout  = 10

realtime        = yes
priority        = 1
log-dir         = /var/log/watchdog
```

Create `ops/watchdog/install.sh`:

```sh
#!/bin/sh
# Instala o connectivity-watchdog: pacote, config, scripts e servico.
# Rode como root no Raspberry Pi alvo.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"

echo ">> Instalando o pacote watchdog..."
apt-get update
apt-get install -y watchdog

echo ">> Instalando a biblioteca compartilhada..."
install -D -m 0644 "$SRC/lib/connectivity.sh" \
    /usr/local/lib/watchdog-connectivity.sh

echo ">> Instalando os scripts de check..."
install -D -m 0755 "$SRC/watchdog.d/check-internet" \
    /etc/watchdog.d/check-internet
install -D -m 0755 "$SRC/watchdog.d/check-vpn" \
    /etc/watchdog.d/check-vpn

echo ">> Instalando o watchdog.conf..."
install -D -m 0644 "$SRC/watchdog.conf" /etc/watchdog.conf

echo ">> Preparando o diretorio de log..."
mkdir -p /var/log/watchdog

echo ">> Habilitando o servico..."
systemctl enable --now watchdog

cat <<'EOF'
>> Pronto.
SEGURANCA: a partir de agora a placa REINICIA se:
  - o kernel/daemon parar de alimentar o /dev/watchdog por 15s, ou
  - a internet publica (8.8.8.8/1.1.1.1) ficar caida por 3 min, ou
  - a VPN (10.8.0.1) ficar caida por 10 min com a internet de pe.
Acompanhe: journalctl -u watchdog -f   e   tail -f /var/log/watchdog/*
EOF
```

Create `ops/watchdog/uninstall.sh`:

```sh
#!/bin/sh
# Remove o connectivity-watchdog. Rode como root no dispositivo.
set -eu

echo ">> Desabilitando o servico..."
systemctl disable --now watchdog || true

echo ">> Removendo os arquivos instalados..."
rm -f /etc/watchdog.d/check-internet /etc/watchdog.d/check-vpn
rm -f /usr/local/lib/watchdog-connectivity.sh
rm -f /etc/watchdog.conf

echo ">> O pacote apt 'watchdog' foi mantido. Para remover:"
echo "     apt-get remove --purge watchdog"
```

Create `ops/watchdog/README.md`:

```markdown
# connectivity-watchdog

Watchdog de hardware (`/dev/watchdog`) via daemon `watchdog` do Debian, com
monitoramento de conectividade e escalonamento: recupera a rede primeiro,
reinicia a placa só se a queda persistir.

## O que faz

- Alimenta o `/dev/watchdog` a cada 10s; se o sistema travar por 15s, a placa
  reinicia por hardware.
- `check-internet`: pinga `8.8.8.8`/`1.1.1.1`. Na queda, `nmcli device
  reconnect wlan0` (fallback `systemctl restart NetworkManager`). Reboot após
  **3 min** de queda contínua.
- `check-vpn`: pinga `10.8.0.1`. Na queda, `systemctl restart
  openvpn@client.service` **só se a internet estiver de pé**. Reboot após
  **10 min**.

## Instalação

```sh
sudo ./install.sh
```

## Remoção

```sh
sudo ./uninstall.sh
```

## Ajustar os tempos

Edite o topo de `/etc/watchdog.d/check-internet` e `check-vpn`:
`REBOOT_AFTER` (segundos até o reboot) e `REPAIR_EVERY` (intervalo entre
remediações). Depois: `sudo systemctl restart watchdog`.

## Testar sem quebrar nada

- Simular queda de internet: `nmcli radio wifi off` (ou regra iptables
  bloqueando os alvos). Acompanhe `journalctl -u watchdog -f`. Reative antes
  dos 3 min para ver a recuperação **sem** reboot.
- Testar o reboot é disruptivo: faça em janela controlada, mantendo a queda
  além da janela.
- Requisitos: daemon `watchdog` com suporte à convenção `test`/`repair` do
  `test-directory` (v5.14+); validar `watchdog-timeout=15` contra o chip com
  `wdctl`.

## Segurança

Watchdog mal configurado reinicia a placa. Como o acesso é remoto via VPN,
teste em momento em que um reboot acidental não seja problema e garanta um
caminho de recuperação (ex.: acesso físico ou alternativo).
```

Torne os instaladores executáveis:

```bash
chmod +x ops/watchdog/install.sh ops/watchdog/uninstall.sh
```

- [ ] **Step 4: Rodar e confirmar que passa (a suíte inteira)**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — `PackagingTests` passa e nenhuma outra suíte quebra.

- [ ] **Step 5: Commit**

```bash
git add ops/watchdog/watchdog.conf ops/watchdog/install.sh \
        ops/watchdog/uninstall.sh ops/watchdog/README.md \
        tests/test_watchdog_connectivity.py
git commit -m "feat(watchdog): empacotamento (conf, install/uninstall, README) + testes de packaging"
```

---

## Validação no hardware (fora do CI — fazer no Pi, deliberadamente)

Depois das Tasks 1–4 (tudo verde no CI), validar no dispositivo real. **Reboot é disruptivo — faça em janela controlada.**

1. `sudo ops/watchdog/install.sh` e confirmar `systemctl status watchdog` ativo.
2. `wdctl` — confirmar que o timeout de 15s é suportado pelo chip; ajustar `watchdog.conf` se necessário.
3. Queda de internet transitória (`nmcli radio wifi off`, reativar em ~1 min): acompanhar `journalctl -u watchdog -f`, ver a remediação e a recuperação **sem** reboot.
4. Queda de VPN só (parar `openvpn@client` com a internet de pé): ver `check-vpn` reiniciar o túnel.
5. Queda persistente (bloqueio mantido além da janela): confirmar o reboot por hardware.
