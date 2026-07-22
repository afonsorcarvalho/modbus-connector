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
