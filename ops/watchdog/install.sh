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

if ! systemctl is-active --quiet watchdog; then
    echo "ERRO: o servico watchdog nao ficou ativo. Verifique 'journalctl -u watchdog'." >&2
    exit 1
fi
echo ">> Dica: valide o timeout do chip com 'wdctl' antes de confiar no reboot por hardware."

cat <<'EOF'
>> Pronto.
SEGURANCA: a partir de agora a placa REINICIA se:
  - o kernel/daemon parar de alimentar o /dev/watchdog por 15s, ou
  - a internet publica (8.8.8.8/1.1.1.1) ficar caida por 3 min, ou
  - a VPN (10.8.0.1) ficar caida por 10 min com a internet de pe.
Acompanhe: journalctl -u watchdog -f   e   tail -f /var/log/watchdog/*
EOF
