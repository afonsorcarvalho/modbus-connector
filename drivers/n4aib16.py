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

from common.filters import reduce, reject_outliers, block_stats, EWMA  # noqa: E402
from common.scaling import resolve_maps, parse_map_arg  # noqa: E402


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
                 current_mode="0-20", ewma_alpha=None):
        self.address = address
        # NOTA: o N4AIB16 real testado responde por FC04 (input registers).
        # FC03 (holding) retorna exceção 0x02 (illegal data address).
        self.function = function        # 4 = input (padrão), 3 = holding
        self.current_mode = current_mode
        self.ewma_alpha = ewma_alpha     # None = EWMA desligado
        self._ewma = {}                  # canal -> EWMA (sob demanda)
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

    def _read_block(self, samples, interval):
        """Coleta `samples` leituras completas (16 canais cada).

        Uma transação Modbus já lê os 16 canais, então N transações dão N
        amostras para todos os canais. Descarta falhas de comunicação, relendo
        até completar N ou estourar `samples` falhas (então RuntimeError).
        """
        blocks = []
        failures = 0
        while len(blocks) < samples:
            try:
                blocks.append(self.read_raw())
            except RuntimeError:
                failures += 1
                if failures > samples:
                    raise
                continue
            if interval and len(blocks) < samples:
                time.sleep(interval)
        return blocks

    def _physical(self, raw, channel):
        """Converte um bruto no valor físico e devolve (valor, unidade, tipo)."""
        if channel <= CURRENT_CHANNELS:
            return raw_to_current(raw, self.current_mode), "mA", "current"
        return raw_to_voltage(raw), "V", "voltage"

    def read_channels(self, samples=1, method="mean", trim=0.1,
                      reject=False, reject_k=3.0, interval=0.0,
                      with_stats=False, maps=None):
        """Lê os canais aplicando o pipeline de filtros e (opcional) map.

        Pipeline por canal: coleta bloco -> [rejeita outliers] -> reduz
        (mean/median/trimmed) -> [EWMA] -> [map]. Com samples=1 e method="mean"
        o resultado é idêntico ao comportamento de leitura única.

        Cada item: {channel, register, raw, value, unit, type}. Com `maps`, o
        canal mapeado reporta value/unit na unidade nova e ganha o campo físico
        ("mA"/"V"). Com with_stats=True, ganha stats={n, s, u, min, max}.
        """
        channel_maps = resolve_maps(maps) if maps else {}
        blocks = self._read_block(samples, interval)
        result = []
        for i in range(NUM_CHANNELS):
            channel = i + 1
            col = [b[i] for b in blocks]
            if reject:
                col = reject_outliers(col, reject_k)
            raw_reduced = reduce(col, method, trim) if len(col) > 1 else col[0]

            phys, phys_unit, ptype = self._physical(raw_reduced, channel)
            if self.ewma_alpha is not None:
                ewma = self._ewma.get(channel)
                if ewma is None:
                    ewma = EWMA(self.ewma_alpha)
                    self._ewma[channel] = ewma
                phys = ewma.update(phys)

            entry = {
                "channel": channel,
                "register": BASE_REGISTER + i,
                "raw": round(raw_reduced) if samples > 1 else raw_reduced,
                "value": round(phys, 3),
                "unit": phys_unit,
                "type": ptype,
            }

            spec = channel_maps.get(channel)
            if spec is not None:
                entry[phys_unit] = round(phys, 4)      # físico preservado
                entry["value"] = round(spec.apply(phys), 4)
                entry["unit"] = spec.unit or "eng"

            if with_stats:
                phys_samples = [self._physical(r, channel)[0] for r in col]
                st = block_stats(phys_samples)
                if spec is not None:
                    slope = abs((spec.out_max - spec.out_min) /
                                (spec.in_max - spec.in_min))
                    st = {**st, "s": st["s"] * slope, "u": st["u"] * slope,
                          "min": spec.apply(st["min"]), "max": spec.apply(st["max"])}
                entry["stats"] = {
                    key: (round(v, 5) if isinstance(v, float) else v)
                    for key, v in st.items() if key in ("n", "s", "u", "min", "max")
                }

            result.append(entry)
        return result

    def reset_filters(self):
        """Zera o estado dos filtros EWMA de todos os canais."""
        self._ewma.clear()

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
    # --- filtros de leitura e escala (biblioteca common/) ---
    p.add_argument("--samples", type=int, default=1,
                   help="nº de leituras por valor (bloco); >1 ativa filtro")
    p.add_argument("--filter", choices=("mean", "median", "trimmed"),
                   default="mean", help="redutor de bloco (padrão mean)")
    p.add_argument("--trim", type=float, default=0.1,
                   help="fração aparada em cada ponta (só p/ --filter trimmed)")
    p.add_argument("--reject", action="store_true",
                   help="rejeita outliers (MAD) antes de reduzir")
    p.add_argument("--reject-k", type=float, default=3.0,
                   help="limiar da rejeição de outlier, em desvios (padrão 3.0)")
    p.add_argument("--ewma", type=float, default=None, metavar="ALPHA",
                   help="suavização contínua EWMA (0<ALPHA<=1); bom com --watch")
    p.add_argument("--sample-interval", type=float, default=0.0,
                   help="espera entre as N amostras do bloco (s)")
    p.add_argument("--stats", action="store_true",
                   help="mostra desvio-padrão s, incerteza u e n por canal")
    p.add_argument("--map", action="append", default=[], dest="maps",
                   metavar="SPEC",
                   help="escala por canal, repetível: "
                        "CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE] "
                        "(ex.: 1,4,6:4:20:0:10:bar)")
    p.add_argument("--map-clamp", action="store_true",
                   help="limita a saída dos maps à faixa de saída")
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

    channels = dev.read_channels(
        samples=args.samples, method=args.filter, trim=args.trim,
        reject=args.reject, reject_k=args.reject_k,
        interval=args.sample_interval, with_stats=args.stats,
        maps=args._map_specs,
    )
    if args.json:
        print(json.dumps(channels, ensure_ascii=False))
    else:
        print(f"N4AIB16 @ endereço {args.address} — {args.baud} baud "
              f"(corrente {args.current_mode} mA)")
        for ch in channels:
            line = (f"  CH{ch['channel']:<2} "
                    f"reg 0x{ch['register']:04X}  "
                    f"bruto {int(ch['raw']):>4}  "
                    f"= {ch['value']:>8} {ch['unit']}  [{ch['type']}]")
            # se houve map, mostra também o valor físico (mA/V) preservado
            phys_key = "mA" if ch["type"] == "current" else "V"
            if phys_key in ch:
                line += f"  ({ch[phys_key]} {phys_key})"
            if "stats" in ch:
                st = ch["stats"]
                line += f"   [n={st['n']} s={st['s']} u={st['u']}]"
            print(line)


def main():
    args = build_parser().parse_args()
    try:
        args._map_specs = [parse_map_arg(s, clamp=args.map_clamp)
                           for s in args.maps]
        resolve_maps(args._map_specs)   # valida canal duplicado cedo
    except ValueError as e:
        sys.exit(f"Erro no --map: {e}")
    try:
        with N4AIB16(args.port, baud=args.baud, address=args.address,
                     function=args.function, databits=args.databits,
                     parity=args.parity, stopbits=args.stopbits,
                     timeout=args.timeout, current_mode=args.current_mode,
                     ewma_alpha=args.ewma) as dev:
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
