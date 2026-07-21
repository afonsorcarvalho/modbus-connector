#!/home/fitadigital/modbus-connector/.venv-modbus/bin/python
"""
modbus_scanner.py — Leitor e scanner Modbus RTU via serial.

Recursos:
  read     Lê registradores de um dispositivo (função 03/04).
  scan     Modo de pesquisa de dispositivo: varre endereços 1..247.
  baud     Modo de pesquisa de velocidade: varre baud rates comuns
           (opcionalmente combinado com a varredura de endereços).

Implementa Modbus RTU diretamente sobre pyserial (CRC16 próprio), sem
dependências pesadas. Requer apenas: pyserial.

Exemplos:
    python modbus_scanner.py read  -p /dev/ttyUSB0 -b 9600 -a 1 --reg 0 --count 4
    python modbus_scanner.py scan  -p /dev/ttyUSB0 -b 9600
    python modbus_scanner.py baud  -p /dev/ttyUSB0 -a 1
    python modbus_scanner.py baud  -p /dev/ttyUSB0            # varre baud E endereço
"""

import argparse
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("Falta a biblioteca pyserial. Instale com: pip install pyserial")


# Baud rates mais comuns em equipamentos Modbus RTU
COMMON_BAUDS = [9600, 19200, 38400, 57600, 115200, 4800, 2400, 1200]

# Combinações de bits de dados / paridade / stop bits mais usadas
COMMON_FRAMES = [
    (8, "N", 1),
    (8, "E", 1),
    (8, "O", 1),
    (8, "N", 2),
]


def crc16(data: bytes) -> bytes:
    """CRC16 Modbus, retornado em little-endian (ordem do fio)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "little")


def build_read_frame(slave: int, function: int, start: int, count: int) -> bytes:
    """Monta um frame de leitura (função 03=holding, 04=input)."""
    body = bytes([slave, function]) + start.to_bytes(2, "big") + count.to_bytes(2, "big")
    return body + crc16(body)


def char_time(baud: int, databits: int, parity: str, stopbits: int) -> float:
    """Tempo de um caractere no barramento (segundos)."""
    bits = 1 + databits + (0 if parity == "N" else 1) + stopbits
    return bits / baud


def transaction(ser: serial.Serial, request: bytes, expected: int) -> bytes:
    """Envia um request e lê a resposta, drenando a linha até o silêncio.

    Lê `expected` bytes e depois continua lendo enquanto houver dados, para
    capturar o frame completo mesmo com lixo (0xFF/0x00) na frente — típico
    de conversores RS-485 com controle de direção automático.
    """
    ser.reset_input_buffer()
    ser.write(request)
    ser.flush()
    resp = bytearray(ser.read(expected))
    if resp:
        # drena o restante enquanto ainda houver bytes chegando
        while True:
            extra = ser.read(expected)
            if not extra:
                break
            resp += extra
    return bytes(resp)


def find_frame(resp: bytes, slave: int, function: int):
    """Re-sincroniza: acha um frame válido (CRC ok) dentro do buffer,
    ignorando lixo de enquadramento no início. Retorna o frame ou None."""
    for i in range(len(resp)):
        if resp[i] != slave:
            continue
        rest = resp[i:]
        if len(rest) < 5:
            break
        func = rest[1]
        if func == (function | 0x80):          # exceção: frame de 5 bytes
            cand = rest[:5]
        elif func == function:                 # resposta normal: 3 + N + 2
            cand = rest[:3 + rest[2] + 2]
        else:
            continue
        if len(cand) >= 5 and crc16(cand[:-2]) == cand[-2:]:
            return cand
    return None


def parse_response(resp: bytes, slave: int, function: int):
    """Valida a resposta. Retorna (ok, mensagem, payload_bytes)."""
    if not resp:
        return False, "sem resposta (timeout)", None
    frame = find_frame(resp, slave, function)
    if frame is None:
        if crc16(resp[:-2]) != resp[-2:]:
            return False, f"CRC inválido / sem frame: {resp.hex()}", None
        return False, f"resposta inesperada: {resp.hex()}", None
    # Exceção Modbus: função com bit alto ligado
    if frame[1] == (function | 0x80):
        code = frame[2]
        # Uma exceção ainda prova que HÁ um dispositivo respondendo nesse endereço.
        return True, f"exceção Modbus 0x{code:02X} (dispositivo presente)", None
    byte_count = frame[2]
    payload = frame[3:3 + byte_count]
    return True, "ok", payload


def open_serial(port, baud, databits, parity, stopbits, timeout) -> serial.Serial:
    parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
    stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=databits,
        parity=parity_map[parity],
        stopbits=stop_map[stopbits],
        timeout=timeout,
    )


def probe(ser: serial.Serial, slave: int, function: int, start: int, count: int,
          retries: int = 2):
    """Testa um dispositivo. Retorna (ok, mensagem, valores|None).

    Retenta em caso de falha de frame — conversores RS-485 com direção
    automática ocasionalmente cortam bytes na virada TX→RX.
    """
    request = build_read_frame(slave, function, start, count)
    # resposta normal = 5 + 2*count bytes; exceção = 5 bytes. Lê o maior.
    expected = 5 + 2 * count
    ok = False
    msg = "sem resposta (timeout)"
    payload = None
    for _ in range(retries + 1):
        resp = transaction(ser, request, expected)
        ok, msg, payload = parse_response(resp, slave, function)
        if ok:
            break
        if "timeout" in msg:      # nada respondeu: não adianta insistir
            break
    values = None
    if payload:
        values = [int.from_bytes(payload[i:i + 2], "big") for i in range(0, len(payload), 2)]
    return ok, msg, values


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #

def cmd_read(args):
    ser = open_serial(args.port, args.baud, args.databits, args.parity,
                      args.stopbits, args.timeout)
    with ser:
        ok, msg, values = probe(ser, args.address, args.function, args.reg, args.count)
        if ok and values is not None:
            print(f"Endereço {args.address} @ {args.baud} baud — {len(values)} registrador(es):")
            for i, v in enumerate(values):
                print(f"  reg {args.reg + i}: {v}  (0x{v:04X})")
        elif ok:
            print(f"Endereço {args.address}: {msg}")
        else:
            print(f"Falha: {msg}")
            sys.exit(1)


def cmd_scan(args):
    print(f"Pesquisando dispositivos em {args.port} @ {args.baud} baud "
          f"({args.databits}{args.parity}{args.stopbits}) — endereços "
          f"{args.start_addr}..{args.end_addr}\n")
    found = []
    ser = open_serial(args.port, args.baud, args.databits, args.parity,
                      args.stopbits, args.timeout)
    with ser:
        for addr in range(args.start_addr, args.end_addr + 1):
            ok, msg, values = probe(ser, addr, args.function, args.reg, args.count)
            if ok:
                extra = f" valores={values}" if values else ""
                print(f"  [+] endereço {addr:3d}: {msg}{extra}")
                found.append(addr)
            else:
                print(f"\r  ... testando {addr:3d} ", end="", flush=True)
            time.sleep(args.gap)
    print("\n")
    if found:
        print(f"Encontrado(s) {len(found)} dispositivo(s): {found}")
    else:
        print("Nenhum dispositivo respondeu. Tente outro baud/paridade (modo 'baud').")


def cmd_baud(args):
    """Varre baud rates (e paridades). Se -a não for dado, varre também endereços."""
    addresses = [args.address] if args.address else range(args.start_addr, args.end_addr + 1)
    scan_all_addr = args.address is None
    print(f"Pesquisando velocidade em {args.port} "
          f"({'varrendo endereços' if scan_all_addr else f'endereço {args.address}'})\n")

    hits = []
    for baud in COMMON_BAUDS:
        for (databits, parity, stopbits) in COMMON_FRAMES:
            try:
                ser = open_serial(args.port, baud, databits, parity, stopbits, args.timeout)
            except serial.SerialException as e:
                print(f"  erro abrindo {baud} {databits}{parity}{stopbits}: {e}")
                continue
            frame_label = f"{baud:>6} baud {databits}{parity}{stopbits}"
            with ser:
                for addr in addresses:
                    ok, msg, values = probe(ser, addr, args.function, args.reg, args.count)
                    if ok:
                        print(f"  [+] {frame_label}  endereço {addr}: {msg}")
                        hits.append((baud, databits, parity, stopbits, addr))
                        if not scan_all_addr:
                            break
                    time.sleep(args.gap)
            print(f"\r  ... {frame_label} testado           ", flush=True)

    print()
    if hits:
        print("Configuração(ões) detectada(s):")
        for baud, db, par, sb, addr in hits:
            print(f"  baud={baud}  frame={db}{par}{sb}  endereço={addr}")
    else:
        print("Nada respondeu em nenhuma combinação. Verifique fiação A/B, "
              "GND, terminação e se o adaptador RS485 está no modo correto.")


EPILOG = """\
Exemplos:
  # Ler 4 registradores holding a partir do reg 0, endereço 1, a 9600 baud
  python modbus_scanner.py read  -p /dev/ttyUSB0 -b 9600 -a 1 --reg 0 --count 4

  # Pesquisar dispositivos varrendo os endereços 1..247 a 9600 baud
  python modbus_scanner.py scan  -p /dev/ttyUSB0 -b 9600

  # Descobrir a velocidade de um dispositivo de endereço conhecido
  python modbus_scanner.py baud  -p /dev/ttyUSB0 -a 1

  # Descobrir velocidade E endereço (varre tudo, mais lento)
  python modbus_scanner.py baud  -p /dev/ttyUSB0

Dica: rode 'python modbus_scanner.py <comando> --help' para ver as opções
de cada comando (read, scan, baud).
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="modbus_scanner.py",
        description="Leitor/scanner Modbus RTU via serial (pyserial, CRC16 próprio).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(
        dest="cmd", required=True, metavar="{read,scan,baud}",
        title="comandos",
        description="use 'python modbus_scanner.py <comando> --help' para detalhes",
    )

    def common(sp, need_addr=False):
        sp.add_argument("-p", "--port", required=True, help="porta serial (ex.: /dev/ttyUSB0)")
        sp.add_argument("-f", "--function", type=int, default=3, choices=[3, 4],
                        help="função Modbus: 3=holding, 4=input (padrão 3)")
        sp.add_argument("--reg", type=int, default=0, help="registrador inicial (padrão 0)")
        sp.add_argument("--count", type=int, default=1, help="quantidade de registradores (padrão 1)")
        sp.add_argument("--databits", type=int, default=8, choices=[7, 8])
        sp.add_argument("--parity", default="N", choices=["N", "E", "O"])
        sp.add_argument("--stopbits", type=int, default=1, choices=[1, 2])
        sp.add_argument("--timeout", type=float, default=0.3, help="timeout de leitura em s (padrão 0.3)")
        sp.add_argument("--gap", type=float, default=0.05, help="intervalo entre tentativas em s")

    # read
    sp = sub.add_parser(
        "read", help="lê registradores de um dispositivo",
        description="Lê registradores de um dispositivo com endereço e baud conhecidos.",
        epilog="Exemplo:\n  python modbus_scanner.py read -p /dev/ttyUSB0 -b 9600 -a 1 --reg 0 --count 4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common(sp)
    sp.add_argument("-b", "--baud", type=int, default=9600, help="velocidade em baud (padrão 9600)")
    sp.add_argument("-a", "--address", type=int, required=True, help="endereço do dispositivo (1..247)")
    sp.set_defaults(func=cmd_read)

    # scan
    sp = sub.add_parser(
        "scan", help="pesquisa dispositivos (varre endereços)",
        description="Varre endereços Modbus para descobrir dispositivos num baud conhecido.",
        epilog="Exemplo:\n  python modbus_scanner.py scan -p /dev/ttyUSB0 -b 9600 --start-addr 1 --end-addr 247",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common(sp)
    sp.add_argument("-b", "--baud", type=int, default=9600, help="velocidade em baud (padrão 9600)")
    sp.add_argument("--start-addr", type=int, default=1, help="endereço inicial da varredura (padrão 1)")
    sp.add_argument("--end-addr", type=int, default=247, help="endereço final da varredura (padrão 247)")
    sp.set_defaults(func=cmd_scan)

    # baud
    sp = sub.add_parser(
        "baud", help="pesquisa velocidade (varre baud/paridade)",
        description="Varre baud rates e paridades comuns para descobrir a configuração "
                    "do barramento. Se -a for omitido, varre também os endereços.",
        epilog="Exemplos:\n"
               "  python modbus_scanner.py baud -p /dev/ttyUSB0 -a 1   # endereço conhecido\n"
               "  python modbus_scanner.py baud -p /dev/ttyUSB0        # varre baud E endereço",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common(sp)
    sp.add_argument("-a", "--address", type=int, default=None,
                    help="endereço conhecido; se omitido, varre 1..247")
    sp.add_argument("--start-addr", type=int, default=1)
    sp.add_argument("--end-addr", type=int, default=247)
    sp.set_defaults(func=cmd_baud)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except serial.SerialException as e:
        sys.exit(f"Erro serial: {e}")
    except KeyboardInterrupt:
        print("\nInterrompido.")


if __name__ == "__main__":
    main()
