# modbus-connector

[![tests](https://github.com/afonsorcarvalho/modbus-connector/actions/workflows/tests.yml/badge.svg)](https://github.com/afonsorcarvalho/modbus-connector/actions/workflows/tests.yml)

Ferramentas em Python para comunicação **Modbus RTU** sobre RS485/serial, focadas
em coletores analógicos da linha eletechsup/Electrosup (ex.: **N4AIB16**).

O projeto tem duas partes:

| Componente | Para quê serve |
|---|---|
| [`modbus_scanner.py`](./modbus_scanner.py) | **Descobrir e diagnosticar** dispositivos: achar endereço, baud rate e ler registradores crus de qualquer dispositivo Modbus RTU. |
| [`drivers/`](./drivers/) | **Drivers de alto nível** por dispositivo, que já sabem o mapa de registradores e entregam valores convertidos (mA, V). Inclui o driver do **N4AIB16**. |

Filosofia: dependência única — **apenas `pyserial`**. O Modbus RTU (CRC16,
enquadramento, re-sincronização) é implementado direto sobre a serial.

---

## Sumário

- [Requisitos e instalação](#requisitos-e-instalação)
- [Descobrindo a porta serial](#descobrindo-a-porta-serial)
- [Parte 1 — o scanner (`modbus_scanner.py`)](#parte-1--o-scanner-modbus_scannerpy)
  - [`baud` — descobrir velocidade/endereço](#baud--descobrir-velocidadeendereço)
  - [`scan` — varrer endereços](#scan--varrer-endereços)
  - [`read` — ler registradores](#read--ler-registradores)
- [Parte 2 — os drivers (`drivers/`)](#parte-2--os-drivers-drivers)
  - [Driver N4AIB16 — CLI](#driver-n4aib16--cli)
  - [Driver N4AIB16 — como biblioteca](#driver-n4aib16--como-biblioteca)
  - [Driver RS-WS-N01-2D (temperatura/umidade)](#driver-rs-ws-n01-2d-temperaturaumidade)
- [Fluxo recomendado](#fluxo-recomendado-do-zero-ao-valor-lido)
- [Solução de problemas](#solução-de-problemas)
- [Licença](#licença)

---

## Requisitos e instalação

- Python 3.9+
- `pyserial`
- Um adaptador USB↔RS485 (ex.: chip CH340/CH341, CP2102, FT232)
- Usuário no grupo `dialout` (Linux) para acessar `/dev/ttyUSB*`

O projeto já traz um ambiente virtual pronto em `.venv-modbus/`:

```bash
# usando o venv do projeto
.venv-modbus/bin/python modbus_scanner.py --help
```

Ou crie o seu:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyserial
```

> Nos exemplos abaixo usamos `python` por brevidade. Se não ativar um venv, troque
> por `.venv-modbus/bin/python`.

---

## Descobrindo a porta serial

Antes de tudo, descubra em qual porta o adaptador RS485 apareceu:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
dmesg | grep -iE "ttyUSB|ch341|cp210|ft232" | tail
```

Tipicamente é **`/dev/ttyUSB0`**. Confirme que você está no grupo `dialout`:

```bash
groups | grep dialout   # se não aparecer: sudo usermod -aG dialout $USER  (e relogar)
```

---

## Parte 1 — o scanner (`modbus_scanner.py`)

Use o scanner quando **não souber** o endereço, a velocidade, ou quiser ler
registradores crus para diagnóstico. Ele tem três comandos: `baud`, `scan` e `read`.

Opções comuns a todos os comandos:

| Opção | Padrão | Descrição |
|---|---|---|
| `-p, --port` | (obrigatório) | porta serial, ex. `/dev/ttyUSB0` |
| `-f, --function` | `3` | função Modbus: `3`=holding, `4`=input |
| `--reg` | `0` | registrador inicial |
| `--count` | `1` | quantidade de registradores |
| `--databits` | `8` | 7 ou 8 |
| `--parity` | `N` | `N`one, `E`ven, `O`dd |
| `--stopbits` | `1` | 1 ou 2 |
| `--timeout` | `0.3` | timeout de leitura (s) |
| `--gap` | `0.05` | intervalo entre tentativas (s) |

### `baud` — descobrir velocidade/endereço

Varre baud rates e paridades comuns até algo responder. É o **primeiro comando**
a usar num dispositivo desconhecido.

```bash
# Endereço conhecido (mais rápido)
python modbus_scanner.py baud -p /dev/ttyUSB0 -a 1

# Não sei nem endereço nem velocidade (varre tudo, mais lento)
python modbus_scanner.py baud -p /dev/ttyUSB0
```

Saída típica:
```
  [+]   9600 baud 8N1  endereço 1: ok
Configuração(ões) detectada(s):
  baud=9600  frame=8N1  endereço=1
```

> Dica: o N4AIB16 usa **função 04**. Se a varredura não achar nada com o padrão
> (FC03), tente `-f 4`:
> ```bash
> python modbus_scanner.py baud -p /dev/ttyUSB0 -a 1 -f 4
> ```

### `scan` — varrer endereços

Com a velocidade já conhecida, descobre **quais endereços** têm dispositivo.

```bash
python modbus_scanner.py scan -p /dev/ttyUSB0 -b 9600
python modbus_scanner.py scan -p /dev/ttyUSB0 -b 9600 --start-addr 1 --end-addr 32
```

### `read` — ler registradores

Lê registradores crus de um dispositivo com endereço e baud **conhecidos**.
Ótimo para inspecionar o mapa antes de usar um driver.

```bash
# Ler os 16 canais do N4AIB16 (função 04!), endereço 1, 9600 baud
python modbus_scanner.py read -p /dev/ttyUSB0 -b 9600 -a 1 -f 4 --reg 0 --count 16

# Ler 4 registradores holding a partir do reg 0
python modbus_scanner.py read -p /dev/ttyUSB0 -b 9600 -a 1 --reg 0 --count 4
```

Saída:
```
Endereço 1 @ 9600 baud — 16 registrador(es):
  reg 0: 0  (0x0000)
  reg 1: 0  (0x0000)
  ...
```

> **Exceção Modbus 0x02** = "endereço de dado ilegal": o dispositivo está lá, mas
> aquela combinação de função/registrador não é válida. No N4AIB16, isso acontece
> ao usar FC03 — troque para `-f 4`.

---

## Parte 2 — os drivers (`drivers/`)

Enquanto o scanner é genérico, os **drivers** conhecem o dispositivo específico:
mapa de registradores, tipo de cada canal e conversão para grandeza física.

```
drivers/
├── __init__.py               # expõe os drivers como pacote
├── n4aib16.py                # driver do conversor N4AIB16
├── MANUAL_N4AIB16.md         # manual dos registradores do N4AIB16
├── rs_ws_n01_2d.py           # driver do sensor de temp/umidade RS-WS-N01-2D
└── MANUAL_RS_WS_N01_2D.md    # manual dos registradores do RS-WS-N01-2D
```

📖 O mapa completo de registradores do N4AIB16 está em
[`drivers/MANUAL_N4AIB16.md`](./drivers/MANUAL_N4AIB16.md).

**Resumo do N4AIB16:** 16 canais, ADC 12 bits, leitura por **FC04** a partir de
`0x0000`. CH1–CH15 = corrente (0–20 / 4–20 mA), CH16 = tensão (0–30 V).

### Driver N4AIB16 — CLI

```bash
# Leitura convertida (padrão: FC04, 9600 baud, corrente 0–20 mA)
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1

# Para transmissores 4–20 mA
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --current-mode 4-20

# Somente valores brutos do ADC
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --raw

# Saída em JSON (para integrar com outro programa)
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --json

# Leitura contínua (polling) a cada 1 s — Ctrl+C para parar
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --watch --interval 1

# Polling em JSON (uma linha por leitura, ótimo para logar em arquivo)
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --watch --json >> leituras.jsonl
```

Opções principais:

| Opção | Padrão | Descrição |
|---|---|---|
| `-p, --port` | (obrigatório) | porta serial |
| `-b, --baud` | `9600` | velocidade |
| `-a, --address` | `1` | endereço Modbus |
| `-f, --function` | `4` | `4`=input (padrão do N4AIB16), `3`=holding |
| `--current-mode` | `0-20` | escala de corrente: `0-20` ou `4-20` |
| `--raw` | — | mostra só valores brutos |
| `--json` | — | saída JSON |
| `--watch` | — | leitura contínua (polling) até Ctrl+C |
| `--interval` | `1.0` | intervalo entre leituras no `--watch` (s) |

Saída padrão:
```
N4AIB16 @ endereço 1 — 9600 baud (corrente 0-20 mA)
  CH1  reg 0x0000  bruto    0 (0x0000)  =      0.0 mA  [current]
  ...
  CH16 reg 0x000F  bruto    0 (0x0000)  =      0.0 V   [voltage]
```

### Filtros de leitura e escala (map)

O driver aceita filtros de leitura (bloco de N amostras com média/mediana/média
aparada, rejeição de outlier, EWMA) e escala por canal (map 4–20 mA → unidade de
engenharia), reaproveitando a biblioteca genérica `common/`. Veja
`drivers/MANUAL_N4AIB16.md` §"Filtros de leitura e escala".

| Opção | Padrão | Descrição |
|---|---|---|
| `--samples` | `1` | nº de leituras por valor (bloco); `>1` ativa filtro |
| `--filter` | `mean` | redutor de bloco: `mean`, `median` ou `trimmed` |
| `--trim` | `0.1` | fração aparada por ponta (só p/ `trimmed`) |
| `--reject` / `--reject-k` | — / `3.0` | rejeita outliers (MAD) antes de reduzir |
| `--ewma ALPHA` | — | suavização contínua (0<α≤1); ótimo com `--watch` |
| `--sample-interval` | `0.0` | espera entre as N amostras (s) |
| `--stats` | — | mostra `s`, `u = s/√n` e `n` por canal |
| `--map SPEC` | — | escala por canal, repetível (veja abaixo) |
| `--map-clamp` | — | limita a saída dos maps à faixa de saída |

```bash
# 10 amostras, mediana, com incerteza
python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median --stats

# monitoramento contínuo suavizado
python drivers/n4aib16.py -p /dev/ttyUSB0 --watch --ewma 0.2

# 4–20 mA -> 0–10 bar nos canais 1,4,6 ; 0–100 % no canal 2
python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median \
    --map 1,4,6:4:20:0:10:bar --map 2:4:20:0:100:%
```

### Driver N4AIB16 — como biblioteca

```python
from drivers.n4aib16 import N4AIB16

# 'with' fecha a serial automaticamente
with N4AIB16(port="/dev/ttyUSB0", baud=9600, address=1, current_mode="4-20") as dev:
    # Lista de dicts, um por canal, já convertida
    for ch in dev.read_channels():
        print(f"CH{ch['channel']}: {ch['value']} {ch['unit']}  (bruto {ch['raw']})")

    # Ou só os valores brutos do ADC (lista de 16 ints)
    brutos = dev.read_raw()
    print(brutos)
```

Cada item de `read_channels()` tem a forma:
```python
{"channel": 1, "register": 0, "raw": 0, "value": 0.0, "unit": "mA", "type": "current"}
```

**Polling (leitura contínua) na sua própria aplicação:**
```python
import time
from drivers.n4aib16 import N4AIB16

with N4AIB16(port="/dev/ttyUSB0", baud=9600, address=1) as dev:
    try:
        while True:
            leitura = dev.read_channels()
            ch1 = leitura[0]
            print(f"CH1 = {ch1['value']} {ch1['unit']}")
            # aqui você gravaria em banco, MQTT, arquivo, etc.
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("parado")
```

**Calibração:** as constantes `ADC_FULL_SCALE`, `CURRENT_FS_MA` e `VOLTAGE_FS_V`
estão no topo de [`drivers/n4aib16.py`](./drivers/n4aib16.py). Ajuste-as se a
leitura real do seu módulo divergir da conversão padrão.

---

## Driver RS-WS-N01-2D (temperatura/umidade)

Sensor industrial de **temperatura + umidade** com LCD, RS-485/Modbus RTU.
Leitura por **FC03** (2 registradores: umidade `0x0000`, temperatura `0x0001`,
ambos valor real ×10; a temperatura é *signed*, permitindo negativos). Também lê
e grava os registradores de **configuração** de endereço (`0x07D0`) e baud
(`0x07D1`, código `{2400:0, 4800:1, 9600:2}`) via **FC06**.

📖 Mapa completo e mais exemplos em
[`drivers/MANUAL_RS_WS_N01_2D.md`](./drivers/MANUAL_RS_WS_N01_2D.md).

**CLI:**
```bash
# leitura (padrão de fábrica: 4800 baud, endereço 1)
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600
# RS-WS-N01-2D @ endereço 2 — 9600 baud
#   humidity     reg 0x0000  bruto  508  =     50.8 %RH
#   temperature  reg 0x0001  bruto  259  =     25.9 °C

# leitura contínua com suavização EWMA e saída JSON
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --watch --ewma 0.3
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --json

# configuração: mostrar / trocar endereço / trocar baud (executa e sai)
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --show-config
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --set-baud 4800
```

As flags de filtro/escala (`--samples`, `--filter`, `--reject`, `--ewma`,
`--stats`, `--map`) são as mesmas do N4AIB16; no `--map` o índice **1=umidade,
2=temperatura**.

**Como biblioteca:**
```python
from drivers.rs_ws_n01_2d import RSWSN012D

with RSWSN012D(port="/dev/ttyUSB1", baud=9600, address=2) as dev:
    for m in dev.read_measurements():
        print(f"{m['name']}: {m['value']} {m['unit']}")
    print(dev.read_config())   # {'address': 2, 'baud_code': 2, 'baud': 9600}
```

---

## Fluxo recomendado (do zero ao valor lido)

1. **Descobrir a porta:** `ls -l /dev/ttyUSB*`
2. **Descobrir baud/endereço:**
   `python modbus_scanner.py baud -p /dev/ttyUSB0 -f 4`
3. **Confirmar leitura crua:**
   `python modbus_scanner.py read -p /dev/ttyUSB0 -b 9600 -a 1 -f 4 --reg 0 --count 16`
4. **Usar o driver** para valores convertidos:
   `python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1`

---

## Solução de problemas

| Sintoma | Causa provável / solução |
|---|---|
| `Permission denied` na porta | usuário fora do grupo `dialout` → `sudo usermod -aG dialout $USER` e relogar |
| `sem resposta (timeout)` | baud/paridade errados (rode `baud`); fiação A/B invertida; falta GND comum; sem terminação de 120 Ω |
| `exceção Modbus 0x02` | dispositivo presente, mas função/registrador inválidos → no N4AIB16 use `-f 4` |
| `CRC inválido` esporádico | adaptador RS485 com direção automática cortando bytes; o código já retenta, mas cheque a qualidade do cabo |
| Todos os canais em `0` | normal se não há sinal aplicado nas entradas (entrada em aberto = 0 mA/0 V) |
| Vários dispositivos no barramento | cada um precisa de **endereço único**; use `scan` para listar |

---

## Referências

- Driver e manual do N4AIB16: [`drivers/`](./drivers/)
- Documentação da série eletechsup N4AI (N4AIA04/A08/**B16**/C24)
- Protocolo Modbus RTU sobre RS485

---

## Licença

Distribuído sob a licença **MIT** — veja [`LICENSE`](./LICENSE).
Edite o titular do copyright no arquivo conforme desejar.
