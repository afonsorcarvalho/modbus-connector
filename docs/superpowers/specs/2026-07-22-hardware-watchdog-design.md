# Hardware Watchdog com monitoramento de conectividade

**Data:** 2026-07-22
**Status:** Aprovado (aguardando plano de implementação)
**Alvo:** Raspberry Pi remoto rodando o `modbus-connector`, acessado via OpenVPN.

## Objetivo

Configurar o watchdog de **hardware** (`/dev/watchdog`) da placa e, além da proteção
contra travamento de sistema, monitorar a **conectividade de rede** com escalonamento:

1. Detecta queda de conectividade.
2. Tenta **recuperar a rede** primeiro (reiniciar WiFi ou OpenVPN).
3. **Reinicia a placa** (reboot por hardware) apenas se a queda persistir além de uma
   janela de graça configurável **por trilha**.

Duas trilhas independentes, cada uma com seu próprio tempo de reboot:

| Trilha | Alvo | Remediação | Reboot após |
|---|---|---|---|
| Internet pública | `8.8.8.8` (fallback `1.1.1.1`) | `nmcli device reconnect wlan0` | **180s (3 min)** |
| VPN | `10.8.0.1` | `systemctl restart openvpn@client.service` | **600s (10 min)** |

## Contexto do dispositivo (levantado)

- **HW watchdog presente**, não usado hoje: `/dev/watchdog`, `/dev/watchdog0` existem;
  nenhum daemon `watchdog` instalado; `/etc/watchdog.conf` inexistente; `RuntimeWatchdog`
  do systemd desligado.
- **Rede**: WiFi `wlan0` (192.168.0.211) gerenciado por **NetworkManager** (`nmcli`
  disponível); túnel **OpenVPN** `tun0` (10.8.0.19) pela unit **`openvpn@client.service`**
  (config `/etc/openvpn/client.conf`, gateway do túnel `10.8.0.1`).

## Abordagem escolhida

Daemon **`watchdog` do Debian** (pacote `watchdog`) — mais simples, consagrado, alimenta o
`/dev/watchdog` nativamente e roda scripts de teste/reparo customizados. Sem código Python
novo: é configuração + shell scripts. Incrementos futuros (ex.: serviço próprio, LED de
status, notificação) ficam para depois.

## Arquitetura

O daemon alimenta o `/dev/watchdog` a cada `interval`. Além dos checks nativos, ele varre
o `test-directory` (`/etc/watchdog.d/`) e executa cada script assim:

- `script test` → `exit 0` saudável, `exit != 0` problema.
- Em caso de problema: `script repair <errno>` → `exit 0` "tratado, siga em frente";
  `exit != 0` → o daemon **para de alimentar o `/dev/watchdog` e reinicia a placa**.

Cada trilha é **um script independente** em `/etc/watchdog.d/` — uma responsabilidade só,
testável isoladamente.

### A pegadinha de timing (restrição central)

O daemon roda os scripts de test/repair **de forma síncrona** no loop principal. **Enquanto
um script roda, o hardware não é alimentado.** Se um `repair` ficar bloqueado esperando o
OpenVPN reconectar (dezenas de segundos), o timer de hardware (15s) dispara **no meio do
reparo** e reinicia antes da hora.

**Consequência de design (obrigatória):** os scripts de repair **disparam** a remediação e
**retornam rápido** (bem abaixo de `watchdog-timeout` e de `test-timeout`). Quem verifica a
recuperação é o **próximo ciclo de `test`**. O escalonamento até o reboot é controlado por
**timestamp da primeira falha** num arquivo de estado — **nunca bloqueando**.

### Máquina de estados por trilha

Arquivo de estado em `/run/watchdog/<trilha>.state` (tmpfs — zera no boot), guardando o
epoch (`date +%s`) da primeira falha da sequência atual e o epoch da última remediação.

```
test:
    if ping <alvo> responde:
        remove o arquivo de estado        # zera o relógio da trilha
        exit 0
    else:
        exit 1

repair <errno>:                            # só é chamado após test falhar
    now = date +%s
    if arquivo de estado não existe:
        grava first_fail=now, last_repair=0
    first_fail = lido do estado
    elapsed = now - first_fail

    if elapsed >= REBOOT_AFTER:
        log "janela esgotada (<elapsed>s) — solicitando reboot por hardware"
        exit 1                             # → watchdog reinicia a placa

    if (now - last_repair) >= REPAIR_EVERY:
        <GUARDA opcional da trilha>        # ver check-vpn
        dispara remediação (não-bloqueante / bounded curto)
        grava last_repair=now
        log "remediação disparada (elapsed=<elapsed>s / REBOOT_AFTER=<...>)"

    exit 0                                 # siga; próximo ciclo re-testa
```

- A **cadência** `REPAIR_EVERY` evita reiniciar o serviço a cada `interval` (10s), o que
  causaria thrashing e nunca daria tempo de reconectar.
- O **reset em `test`** garante que uma falha transitória de um ciclo não acumula: assim que
  o ping volta, o relógio zera.
- `test` deve pingar com timeout curto e poucos pacotes (ex.: `ping -c 2 -W 2`) para não
  segurar o loop.

### Parâmetros por trilha (topo de cada script)

**`check-internet`**
```
TARGETS="8.8.8.8 1.1.1.1"     # sucesso se QUALQUER um responder
REBOOT_AFTER=180              # 3 min
REPAIR_EVERY=60               # re-tenta remediação a cada 60s
# remediação: nmcli device reconnect wlan0  (fallback: systemctl restart NetworkManager)
```

**`check-vpn`**
```
TARGET=10.8.0.1
REBOOT_AFTER=600              # 10 min
REPAIR_EVERY=120             # re-tenta remediação a cada 120s
# remediação: systemctl restart openvpn@client.service
# GUARDA: se 8.8.8.8/1.1.1.1 também não respondem, NÃO reinicia o VPN —
#         apenas conta o tempo. A trilha de internet é dona da recuperação;
#         reiniciar o túnel sobre link morto é inútil.
```

### watchdog.conf (principais chaves)

```
watchdog-device = /dev/watchdog
watchdog-timeout = 15          # HW reinicia se não for alimentado por 15s
interval        = 10           # alimenta + roda os testes a cada 10s
test-directory  = /etc/watchdog.d
test-timeout    = 10           # tempo máx. de um script test/repair
repair-timeout  = 10
realtime        = yes
priority        = 1
log-dir         = /var/log/watchdog
```

> Validar na instalação que `watchdog-timeout=15` é suportado pelo chip do Pi (`wdctl`);
> ajustar se necessário. `test-timeout`/`repair-timeout` mantêm os scripts curtos por
> contrato.

## Comportamento em queda simultânea

Se a internet cai, **as duas trilhas falham** (VPN depende da internet). A trilha de
internet (3 min) estoura primeiro e reinicia a placa — comportamento desejado, pois sem
internet o túnel não voltaria mesmo. A guarda do `check-vpn` evita reiniciar o
`openvpn@client` inutilmente enquanto a internet está caída. A janela de 10 min do VPN só é
soberana quando a internet está de pé e **apenas o túnel** caiu. O reboot é **da placa
inteira** (um único `/dev/watchdog`); quem estourar a janela primeiro reinicia tudo.

## Layout no repositório (fonte da verdade, versionado)

```
ops/watchdog/
  watchdog.conf              # → instalado em /etc/watchdog.conf
  watchdog.d/
    check-internet           # → /etc/watchdog.d/check-internet  (executável)
    check-vpn                # → /etc/watchdog.d/check-vpn        (executável)
  install.sh                 # apt install watchdog; copia arquivos; enable+start service
  uninstall.sh               # para/disable service; remove arquivos instalados
  README.md                  # o que é, como instalar, como ajustar tempos, como testar
```

`install.sh` deve ser idempotente: instalar o pacote, copiar arquivos com permissões
corretas (scripts `0755`, conf `0644`), criar `/var/log/watchdog`, `systemctl enable --now
watchdog`, e imprimir um lembrete de segurança sobre reboot.

## Testes

**Unitários (shell, sem hardware):** shims de `ping`, `nmcli`, `systemctl` e `date`
injetados via `PATH`; `/run/watchdog` redirecionado para um tmpdir. Casos:

- `test` com alvo respondendo → `exit 0` e estado removido.
- `test` com alvo mudo → `exit 1`.
- `repair` na 1ª falha → dispara remediação, grava estado, `exit 0`.
- `repair` dentro da janela e antes de `REPAIR_EVERY` → **não** re-dispara, `exit 0`.
- `repair` dentro da janela e após `REPAIR_EVERY` → re-dispara, `exit 0`.
- `repair` com `elapsed >= REBOOT_AFTER` → `exit 1` (reboot).
- `check-vpn` com internet caída → **não** reinicia o VPN, só conta.
- Tempos diferentes por trilha realmente respeitados (180 vs 600).

**No hardware (deliberado — reboot é disruptivo):**

- Simular queda de internet (`nmcli radio wifi off` ou regra iptables bloqueando os alvos),
  acompanhar `/var/log/watchdog` e `journalctl -u watchdog`, confirmar remediação →
  recuperação sem reboot quando a rede volta antes da janela.
- Simular falha **persistente** (bloqueio mantido) e confirmar reboot ao estourar a janela.
- Confirmar que o HW watchdog realmente reinicia (validar via `wdctl`; teste de reboot em
  janela controlada).

## Segurança / riscos

- Watchdog mal configurado **reinicia a placa**. Testar em momento em que um reboot
  acidental não cause problema.
- Scripts **nunca** podem bloquear além de `test-timeout`/`repair-timeout`.
- `watchdog-timeout` deve ser ≥ ao suportado pelo `/dev/watchdog`; validar com `wdctl`.
- Como é acesso remoto via VPN: garantir que `install.sh`/`uninstall.sh` deixem sempre um
  caminho de recuperação (o próprio watchdog reinicia a placa, mas um erro que impeça o boot
  exigiria acesso físico).

## Fora de escopo (futuro)

- Serviço próprio em Python com máquina de estados mais rica.
- Notificação/telemetria (LED, alerta, log remoto) na remediação/reboot.
- `RuntimeWatchdog` do systemd como camada adicional para travamento de kernel.
- Remediações extras (ex.: renovar DHCP, resetar rádio por `rfkill`).
