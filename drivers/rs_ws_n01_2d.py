#!/home/fitadigital/modbus-connector/.venv-modbus/bin/python
"""rs_ws_n01_2d.py — Driver Modbus RTU do sensor RS-WS-N01-2D (temp/umidade).

Registradores (FC03 para ler, FC06 para escrever):
    0x0000 umidade      (real ×10, unsigned)  -> %RH
    0x0001 temperatura  (real ×10, signed)    -> °C
    0x07D0 endereço do dispositivo   (R/W)
    0x07D1 código de baud            (R/W; {2400:0, 4800:1, 9600:2})

Comunicação: 8N1, CRC. Baud de fábrica 4800; suportados 2400/4800/9600.
A tabela de código de baud foi confirmada no hardware real (sensor a 9600 baud
reporta 2 no registrador 0x07D1).

Reaproveita as primitivas Modbus (CRC16, transação, sincronização de frame) de
`modbus_scanner.py` e o pipeline de filtros/escala de `common/`, mantendo a
filosofia do projeto: só depende de pyserial.

Uso como biblioteca:
    from drivers.rs_ws_n01_2d import RSWSN012D
    dev = RSWSN012D(port="/dev/ttyUSB1", baud=9600, address=2)
    for m in dev.read_measurements():
        print(m)
    print(dev.read_config())
    dev.close()

Uso como CLI:
    python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600
    python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --show-config
    python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --json --watch
"""

import argparse
import json
import os
import sys
import time

# Torna as primitivas de modbus_scanner.py (no diretório pai) importáveis.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    from modbus_scanner import open_serial, probe, crc16, transaction  # noqa: E402
except ImportError as e:  # pragma: no cover
    sys.exit(f"Não foi possível importar modbus_scanner.py: {e}")

from common.filters import reduce, reject_outliers, block_stats, EWMA  # noqa: E402
from common.scaling import resolve_maps, parse_map_arg  # noqa: E402


# --------------------------------------------------------------------------- #
# Configuração do módulo
# --------------------------------------------------------------------------- #

NUM_REGISTERS = 2            # umidade + temperatura
BASE_REGISTER = 0x0000       # umidade em 0x0000, temperatura em 0x0001
REG_ADDRESS = 0x07D0         # endereço do dispositivo (R/W)
REG_BAUD = 0x07D1            # código de baud (R/W)
HUM_SCALE = 10.0             # contagens por %RH  (495 -> 49,5 %RH)
TEMP_SCALE = 10.0            # contagens por °C   (243 -> 24,3 °C)
# Tabela confirmada no hardware: o registrador guarda o índice, não o baud.
BAUD_CODES = {2400: 0, 4800: 1, 9600: 2}
BAUD_BY_CODE = {code: baud for baud, code in BAUD_CODES.items()}
MEASUREMENTS = [("humidity", "%RH"), ("temperature", "°C")]


def to_signed16(raw):
    """Reinterpreta um inteiro 16-bit unsigned como signed (complemento de 2)."""
    return raw - 0x10000 if raw >= 0x8000 else raw


def raw_to_humidity(raw):
    """Umidade em %RH (0..100). Valor bruto é real ×10, unsigned."""
    return raw / HUM_SCALE


def raw_to_temperature(raw):
    """Temperatura em °C. Valor bruto é real ×10, signed (permite negativos)."""
    return to_signed16(raw) / TEMP_SCALE
