#!/home/fitadigital/modbus-connector/.venv-modbus/bin/python
"""
n4aib16.py — Driver Modbus RTU para o conversor eletechsup/Electrosup N4AIB16.

O N4AIB16 é um coletor analógico -> RS485 (Modbus RTU) com ADC de 12 bits:
    CH1 .. CH15  -> entradas de CORRENTE  (0-20mA / 4-20mA)
    CH16         -> entrada de TENSÃO      (0-30V)

Cada canal ocupa 1 registrador (16 bits), começando em 0x0000:
    reg 0x0000 = CH1 ... reg 0x000E = CH15 ... reg 0x000F = CH16
Leitura via função Modbus **04 (input registers)** — padrão do N4AIB16 real
testado. FC03 (holding) retorna exceção 0x02 neste módulo. Escrita 06/16.

CALIBRAÇÃO (medida no módulo real): as entradas de corrente já vêm com o
escalonamento feito pelo próprio N4AIB16 em CENTÉSIMOS de mA — não é o bruto
"cru" do ADC. Ex.: 3,00 mA -> 300 bruto, 20,00 mA -> 2000 bruto. Portanto a
conversão é linear e direta: mA = bruto / COUNTS_PER_MA (100), offset zero.
Ajuste COUNTS_PER_MA / COUNTS_PER_V se a sua calibração diferir.

Reaproveita as primitivas Modbus (CRC16, transação, sincronização de frame) de
`modbus_scanner.py`, mantendo a filosofia do projeto: só depende de pyserial.

Uso como biblioteca:
    from drivers.n4aib16 import N4AIB16
    dev = N4AIB16(port="/dev/ttyUSB0", baud=9600, address=1)
    for ch in dev.read_channels():
        print(ch)
    dev.close()

Uso como CLI:
    python drivers/n4aib16.py -p /dev/ttyUSB0 -b 9600 -a 1
    python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --current-mode 4-20 --json
    python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --raw            # só valores brutos
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
    from modbus_scanner import open_serial, probe  # noqa: E402
except ImportError as e:  # pragma: no cover
    sys.exit(f"Não foi possível importar modbus_scanner.py: {e}")


# --------------------------------------------------------------------------- #
# Configuração do módulo
# --------------------------------------------------------------------------- #

NUM_CHANNELS = 16            # N4AIB16 = 16 canais
BASE_REGISTER = 0x0000       # CH1 começa em 0x0000
CURRENT_CHANNELS = 15        # CH1..CH15 = corrente; CH16 = tensão

# Calibração medida no módulo real: o valor lido já é a grandeza física
# escalonada pelo N4AIB16 em passos fixos, offset zero.
#   corrente: 100 contagens por mA  (3,00 mA -> 300 ; 20,00 mA -> 2000)
COUNTS_PER_MA = 100.0        # contagens por mA (medido: 3 mA = 300 bruto)
#   tensão CH16: assumido 100 contagens por V (0-30 V -> 0-3000). NÃO
#   confirmado com padrão — recalibrar injetando uma tensão conhecida.
COUNTS_PER_V = 100.0


def raw_to_current(raw: int, mode: str = "0-20") -> float:
    """Converte o valor lido em mA.

    O módulo já entrega a corrente em centésimos de mA (100 contagens/mA),
    então a leitura é a corrente REAL independentemente do transmissor:
        0-20 mA  -> 0..2000 bruto
        4-20 mA  -> 400..2000 bruto  (a mesma fórmula já dá 4,00..20,00 mA)

    'mode' é mantido por compatibilidade de API; não altera o valor em mA,
    pois o próprio módulo reporta a corrente verdadeira.
    """
    return raw / COUNTS_PER_MA


def raw_to_voltage(raw: int) -> float:
    """Converte o valor lido em Volts (canal 0-30V).

    Assume 100 contagens/V (mesma lógica da corrente). Recalibrar se necessário.
    """
    return raw / COUNTS_PER_V


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

class N4AIB16:
    """Driver do conversor N4AIB16 sobre uma linha serial Modbus RTU."""

    def __init__(self, port, baud=9600, address=1, function=4,
                 databits=8, parity="N", stopbits=1, timeout=0.3,
                 current_mode="0-20"):
        self.address = address
        # NOTA: o N4AIB16 real testado responde por FC04 (input registers).
        # FC03 (holding) retorna exceção 0x02 (illegal data address).
        self.function = function        # 4 = input (padrão), 3 = holding
        self.current_mode = current_mode
        self._ser = open_serial(port, baud, databits, parity, stopbits, timeout)

    # -- baixo nível --------------------------------------------------------- #

    def read_raw(self):
        """Lê os 16 registradores de canal e retorna a lista de valores brutos.

        Lança RuntimeError se o dispositivo não responder ou o frame for inválido.
        """
        ok, msg, values = probe(
            self._ser, self.address, self.function, BASE_REGISTER, NUM_CHANNELS
        )
        if not ok or values is None:
            raise RuntimeError(f"Falha ao ler N4AIB16 @ addr {self.address}: {msg}")
        return values

    # -- alto nível ---------------------------------------------------------- #

    def read_channels(self):
        """Retorna uma lista de dicts, um por canal, com valor convertido.

        Cada item: {channel, register, raw, value, unit, type}
        """
        raw = self.read_raw()
        result = []
        for i, value in enumerate(raw):
            channel = i + 1
            if channel <= CURRENT_CHANNELS:
                result.append({
                    "channel": channel,
                    "register": BASE_REGISTER + i,
                    "raw": value,
                    "value": round(raw_to_current(value, self.current_mode), 3),
                    "unit": "mA",
                    "type": "current",
                })
            else:
                result.append({
                    "channel": channel,
                    "register": BASE_REGISTER + i,
                    "raw": value,
                    "value": round(raw_to_voltage(value), 3),
                    "unit": "V",
                    "type": "voltage",
                })
        return result

    def close(self):
        self._ser.close()

    # suporte a "with N4AIB16(...) as dev:"
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        description="Driver de leitura do conversor analógico N4AIB16 (Modbus RTU)."
    )
    p.add_argument("-p", "--port", required=True, help="porta serial (ex: /dev/ttyUSB0)")
    p.add_argument("-b", "--baud", type=int, default=9600, help="baud rate (padrão 9600)")
    p.add_argument("-a", "--address", type=int, default=1, help="endereço Modbus (padrão 1)")
    p.add_argument("-f", "--function", type=int, choices=(3, 4), default=4,
                   help="função de leitura: 4=input (padrão, usado pelo N4AIB16), 3=holding")
    p.add_argument("--current-mode", choices=("0-20", "4-20"), default="0-20",
                   help="escala das entradas de corrente (padrão 0-20 mA)")
    p.add_argument("--databits", type=int, default=8)
    p.add_argument("--parity", choices=("N", "E", "O"), default="N")
    p.add_argument("--stopbits", type=int, choices=(1, 2), default=1)
    p.add_argument("--timeout", type=float, default=0.3)
    p.add_argument("--raw", action="store_true", help="mostra apenas valores brutos")
    p.add_argument("--json", action="store_true", help="saída em JSON")
    p.add_argument("--watch", action="store_true",
                   help="leitura contínua (polling); Ctrl+C para parar")
    p.add_argument("--interval", type=float, default=1.0,
                   help="intervalo entre leituras no modo --watch (s, padrão 1.0)")
    return p


def render_once(dev, args):
    """Faz uma leitura e imprime conforme as flags --raw / --json."""
    if args.raw:
        raw = dev.read_raw()
        if args.json:
            print(json.dumps(raw))
        else:
            for i, v in enumerate(raw):
                print(f"CH{i + 1:<2} reg 0x{BASE_REGISTER + i:04X}: "
                      f"{v}  (0x{v:04X})")
        return

    channels = dev.read_channels()
    if args.json:
        print(json.dumps(channels, ensure_ascii=False))
    else:
        print(f"N4AIB16 @ endereço {args.address} — {args.baud} baud "
              f"(corrente {args.current_mode} mA)")
        for ch in channels:
            print(f"  CH{ch['channel']:<2} "
                  f"reg 0x{ch['register']:04X}  "
                  f"bruto {ch['raw']:>4} (0x{ch['raw']:04X})  "
                  f"= {ch['value']:>8} {ch['unit']}  [{ch['type']}]")


def main():
    args = build_parser().parse_args()
    try:
        with N4AIB16(args.port, baud=args.baud, address=args.address,
                     function=args.function, databits=args.databits,
                     parity=args.parity, stopbits=args.stopbits,
                     timeout=args.timeout, current_mode=args.current_mode) as dev:
            if not args.watch:
                render_once(dev, args)
                return
            # Modo polling: relê a cada --interval até Ctrl+C.
            try:
                while True:
                    render_once(dev, args)
                    if not args.json:
                        print("-" * 40)
                    sys.stdout.flush()   # mostra em tempo real mesmo se redirecionado
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nInterrompido.", file=sys.stderr)
    except RuntimeError as e:
        sys.exit(f"Erro: {e}")
    except Exception as e:  # serial, etc.
        sys.exit(f"Erro serial/comunicação: {e}")


if __name__ == "__main__":
    main()
