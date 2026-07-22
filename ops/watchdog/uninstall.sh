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
