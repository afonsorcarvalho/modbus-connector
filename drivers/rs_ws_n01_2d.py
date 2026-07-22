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


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

class RSWSN012D:
    """Driver do sensor RS-WS-N01-2D sobre uma linha serial Modbus RTU."""

    def __init__(self, port, baud=4800, address=1, function=3,
                 databits=8, parity="N", stopbits=1, timeout=0.3,
                 ewma_alpha=None):
        self.address = address
        self.function = function          # 3 = holding (padrão), 4 = input
        self.ewma_alpha = ewma_alpha      # None = EWMA desligado
        self._ewma = {}                   # índice -> EWMA (sob demanda)
        self._ser = open_serial(port, baud, databits, parity, stopbits, timeout)

    # -- baixo nível --------------------------------------------------------- #

    def read_raw(self):
        """Lê os 2 registradores (umidade, temperatura) e retorna os brutos.

        Lança RuntimeError se o dispositivo não responder ou o frame for inválido.
        """
        ok, msg, values = probe(
            self._ser, self.address, self.function, BASE_REGISTER, NUM_REGISTERS
        )
        if not ok or values is None:
            raise RuntimeError(
                f"Falha ao ler RS-WS-N01-2D @ addr {self.address}: {msg}")
        return values

    # -- alto nível ---------------------------------------------------------- #

    def _read_block(self, samples, interval):
        """Coleta `samples` leituras (2 registradores cada), descartando falhas.

        Relê até completar N ou estourar `samples` falhas (então RuntimeError).
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

    def _physical(self, raw, index):
        """Converte um bruto no valor físico -> (valor, unidade, nome).

        index 0 = umidade, index 1 = temperatura.
        """
        name, unit = MEASUREMENTS[index]
        if index == 0:
            return raw_to_humidity(raw), unit, name
        return raw_to_temperature(raw), unit, name

    def read_measurements(self, samples=1, method="mean", trim=0.1,
                          reject=False, reject_k=3.0, interval=0.0,
                          with_stats=False, maps=None):
        """Lê umidade e temperatura aplicando filtros e (opcional) map.

        Pipeline por medição: bloco -> [rejeita outliers] -> reduz
        (mean/median/trimmed) -> [EWMA] -> [map]. Com samples=1 e method="mean"
        o resultado é idêntico à leitura única. `maps` usa índice 1-based
        (1=umidade, 2=temperatura).

        Cada item: {name, register, raw, value, unit}. Com `maps`, a medição
        mapeada reporta value/unit na unidade nova e ganha o campo físico
        ("%RH"/"°C"). Com with_stats=True, ganha stats={n, s, u, min, max}.
        """
        channel_maps = resolve_maps(maps) if maps else {}
        blocks = self._read_block(samples, interval)
        result = []
        for i in range(NUM_REGISTERS):
            col = [b[i] for b in blocks]
            if reject:
                col = reject_outliers(col, reject_k)
            raw_reduced = reduce(col, method, trim) if len(col) > 1 else col[0]

            phys, phys_unit, name = self._physical(raw_reduced, i)
            if self.ewma_alpha is not None:
                ewma = self._ewma.get(i)
                if ewma is None:
                    ewma = EWMA(self.ewma_alpha)
                    self._ewma[i] = ewma
                phys = ewma.update(phys)

            entry = {
                "name": name,
                "register": BASE_REGISTER + i,
                "raw": round(raw_reduced) if samples > 1 else raw_reduced,
                "value": round(phys, 3),
                "unit": phys_unit,
            }

            spec = channel_maps.get(i + 1)   # índice 1-based no --map
            if spec is not None:
                entry[phys_unit] = round(phys, 4)      # físico preservado
                entry["value"] = round(spec.apply(phys), 4)
                entry["unit"] = spec.unit or "eng"

            if with_stats:
                phys_samples = [self._physical(r, i)[0] for r in col]
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

    # -- configuração (registradores R/W) ------------------------------------ #

    def _read_config_raw(self):
        """Lê os 2 registradores de configuração (endereço, código de baud)."""
        ok, msg, values = probe(
            self._ser, self.address, self.function, REG_ADDRESS, 2
        )
        if not ok or values is None:
            raise RuntimeError(
                f"Falha ao ler config @ addr {self.address}: {msg}")
        return values

    def read_config(self):
        """Retorna {address, baud_code, baud} do sensor.

        `baud` é resolvido pela tabela BAUD_BY_CODE (None se código desconhecido).
        """
        addr, baud_code = self._read_config_raw()
        return {
            "address": addr,
            "baud_code": baud_code,
            "baud": BAUD_BY_CODE.get(baud_code),
        }

    def _write_register(self, reg, value):
        """Escreve um registrador via FC06 e valida o eco da resposta.

        FC06 responde ecoando a requisição (8 bytes). RuntimeError se divergir.
        """
        body = bytes([self.address, 0x06,
                      (reg >> 8) & 0xFF, reg & 0xFF,
                      (value >> 8) & 0xFF, value & 0xFF])
        request = body + crc16(body)
        resp = transaction(self._ser, request, len(request))
        if resp != request:
            raise RuntimeError(
                f"Escrita não confirmada no reg 0x{reg:04X}: "
                f"esperado {request.hex()}, recebido {resp.hex() or '(vazio)'}")

    def set_address(self, new):
        """Grava um novo endereço Modbus (1..247) no reg 0x07D0."""
        if not 1 <= new <= 247:
            raise ValueError(f"endereço fora da faixa 1..247: {new}")
        self._write_register(REG_ADDRESS, new)

    def set_baud(self, baud):
        """Grava um novo baud (2400/4800/9600) como código no reg 0x07D1."""
        if baud not in BAUD_CODES:
            raise ValueError(
                f"baud inválido {baud}; use um de {sorted(BAUD_CODES)}")
        self._write_register(REG_BAUD, BAUD_CODES[baud])

    def reset_filters(self):
        """Zera o estado dos filtros EWMA de todas as medições."""
        self._ewma.clear()

    def close(self):
        self._ser.close()

    # suporte a "with RSWSN012D(...) as dev:"
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        description="Driver do sensor de temperatura/umidade RS-WS-N01-2D "
                    "(Modbus RTU).")
    p.add_argument("-p", "--port", required=True,
                   help="porta serial (ex: /dev/ttyUSB1)")
    p.add_argument("-b", "--baud", type=int, default=4800,
                   help="baud rate (padrão de fábrica 4800)")
    p.add_argument("-a", "--address", type=int, default=1,
                   help="endereço Modbus (padrão 1)")
    p.add_argument("-f", "--function", type=int, choices=(3, 4), default=3,
                   help="função de leitura: 3=holding (padrão), 4=input")
    p.add_argument("--databits", type=int, default=8)
    p.add_argument("--parity", choices=("N", "E", "O"), default="N")
    p.add_argument("--stopbits", type=int, choices=(1, 2), default=1)
    p.add_argument("--timeout", type=float, default=0.3)
    # --- config (executa e sai) ---
    p.add_argument("--show-config", action="store_true",
                   help="lê e mostra endereço e baud atuais do sensor")
    p.add_argument("--set-address", type=int, default=None, metavar="N",
                   help="grava novo endereço Modbus (1..247) e sai")
    p.add_argument("--set-baud", type=int, default=None,
                   choices=sorted(BAUD_CODES),
                   help="grava novo baud (2400/4800/9600) e sai")
    # --- leitura ---
    p.add_argument("--raw", action="store_true",
                   help="mostra apenas valores brutos")
    p.add_argument("--json", action="store_true", help="saída em JSON")
    p.add_argument("--watch", action="store_true",
                   help="leitura contínua (polling); Ctrl+C para parar")
    p.add_argument("--interval", type=float, default=1.0,
                   help="intervalo entre leituras no --watch (s, padrão 1.0)")
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
                   help="mostra desvio-padrão s, incerteza u e n por medição")
    p.add_argument("--map", action="append", default=[], dest="maps",
                   metavar="SPEC",
                   help="escala por medição, repetível (índice 1=umidade, "
                        "2=temperatura): IDX:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNI]")
    p.add_argument("--map-clamp", action="store_true",
                   help="limita a saída dos maps à faixa de saída")
    return p


def run_config(dev, args):
    """Executa uma ação de configuração (--show-config/--set-*) e retorna."""
    if args.set_address is not None:
        dev.set_address(args.set_address)
        print(f"Endereço gravado: {args.set_address}. "
              f"O sensor agora responde nesse novo endereço "
              f"(reabra a conexão com -a {args.set_address}).")
        return
    if args.set_baud is not None:
        dev.set_baud(args.set_baud)
        print(f"Baud gravado: {args.set_baud}. O sensor agora comunica nessa "
              f"nova velocidade (reabra a conexão com -b {args.set_baud}).")
        return
    cfg = dev.read_config()
    baud = cfg["baud"] if cfg["baud"] is not None else f"?(cód {cfg['baud_code']})"
    print(f"RS-WS-N01-2D — endereço {cfg['address']}, baud {baud}")


def render_once(dev, args):
    """Faz uma leitura e imprime conforme --raw / --json."""
    if args.raw:
        raw = dev.read_raw()
        if args.json:
            print(json.dumps(raw))
        else:
            for i, v in enumerate(raw):
                name = MEASUREMENTS[i][0]
                print(f"{name:<12} reg 0x{BASE_REGISTER + i:04X}: "
                      f"{v}  (0x{v:04X})")
        return

    measurements = dev.read_measurements(
        samples=args.samples, method=args.filter, trim=args.trim,
        reject=args.reject, reject_k=args.reject_k,
        interval=args.sample_interval, with_stats=args.stats,
        maps=args._map_specs,
    )
    if args.json:
        print(json.dumps(measurements, ensure_ascii=False))
    else:
        print(f"RS-WS-N01-2D @ endereço {args.address} — {args.baud} baud")
        for m in measurements:
            line = (f"  {m['name']:<12} reg 0x{m['register']:04X}  "
                    f"bruto {int(m['raw']):>4}  = {m['value']:>8} {m['unit']}")
            for phys_key in ("%RH", "°C"):
                if phys_key in m:
                    line += f"  ({m[phys_key]} {phys_key})"
            if "stats" in m:
                st = m["stats"]
                line += f"   [n={st['n']} s={st['s']} u={st['u']}]"
            print(line)


def main():
    args = build_parser().parse_args()
    try:
        args._map_specs = [parse_map_arg(s, clamp=args.map_clamp)
                           for s in args.maps]
        resolve_maps(args._map_specs)   # valida índice duplicado cedo
    except ValueError as e:
        sys.exit(f"Erro no --map: {e}")

    is_config = (args.show_config or args.set_address is not None
                 or args.set_baud is not None)
    try:
        with RSWSN012D(args.port, baud=args.baud, address=args.address,
                       function=args.function, databits=args.databits,
                       parity=args.parity, stopbits=args.stopbits,
                       timeout=args.timeout, ewma_alpha=args.ewma) as dev:
            if is_config:
                run_config(dev, args)
                return
            if not args.watch:
                render_once(dev, args)
                return
            # Modo polling: relê a cada --interval até Ctrl+C.
            try:
                while True:
                    render_once(dev, args)
                    if not args.json:
                        print("-" * 40)
                    sys.stdout.flush()
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nInterrompido.", file=sys.stderr)
    except ValueError as e:
        sys.exit(f"Erro: {e}")
    except RuntimeError as e:
        sys.exit(f"Erro: {e}")
    except Exception as e:  # serial, etc.
        sys.exit(f"Erro serial/comunicação: {e}")


if __name__ == "__main__":
    main()
