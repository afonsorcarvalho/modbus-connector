# Manual de Registradores — Sensor RS-WS-N01-2D (temperatura/umidade)

Documentação do protocolo Modbus RTU do sensor **RS-WS-N01-2D**, transmissor
industrial de temperatura e umidade com display LCD, montagem em parede, RS-485.
Base para o driver [`rs_ws_n01_2d.py`](./rs_ws_n01_2d.py).

> ✅ **Validado no hardware real.** O mapa de registradores, os function codes e a
> tabela de código de baud abaixo foram confirmados em um sensor físico
> (`/dev/ttyUSB1`, endereço 2, 9600 baud).

---

## 1. Identificação do dispositivo

| Item | Especificação |
|---|---|
| Modelo | RS-WS-N01-2D-LCD |
| Função | Transmissor de temperatura + umidade → RS-485 (Modbus RTU) |
| Grandezas | Umidade relativa (%RH) e temperatura (°C) |
| Interface | RS-485 (2 fios, A/B) |
| Display | LCD |

---

## 2. Parâmetros de comunicação

| Parâmetro | Valor |
|---|---|
| Protocolo | Modbus RTU |
| Meio físico | RS-485 (2 fios, A/B) |
| Baud rate padrão de fábrica | **4800** |
| Baud rates suportados | 2400, 4800, 9600 |
| Data bits | 8 |
| Paridade | Nenhuma (N) |
| Stop bits | 1 |
| Verificação | CRC (Cyclic Redundancy Check) |
| Endereço padrão | 1 |

---

## 3. Mapa de registradores

Leitura via **FC03** (Read Holding Registers); escrita via **FC06** (Write Single
Register).

| Reg (hex) | Reg (dec) | PLC (4x) | Conteúdo | Acesso | Escala |
|---|---|---|---|---|---|
| 0x0000 | 0 | 40001 | Umidade | Leitura | valor real ×10, unsigned |
| 0x0001 | 1 | 40002 | Temperatura | Leitura | valor real ×10, **signed** |
| 0x07D0 | 2000 | 42001 | Endereço do dispositivo | Leitura/Escrita | 1..247 |
| 0x07D1 | 2001 | 42002 | Código de baud | Leitura/Escrita | ver §4 |

### Conversão física

- **Umidade** = `bruto / 10` → `%RH`. Ex.: `495` → `49,5 %RH`.
- **Temperatura** = `signed16(bruto) / 10` → `°C`. Valores `≥ 0x8000` são
  temperaturas negativas (complemento de 2). Ex.: `243` → `24,3 °C`;
  `0xFFEC` (65516) → `-2,0 °C`.

---

## 4. Código de baud (registrador 0x07D1)

O registrador **não** guarda o valor do baud, e sim um índice. Tabela confirmada
no hardware (sensor a 9600 baud reporta `2`):

| Código | Baud |
|---|---|
| 0 | 2400 |
| 1 | 4800 |
| 2 | 9600 |

> ⚠️ Após gravar um novo endereço ou baud, o sensor passa a responder já com a
> nova configuração — a sessão serial atual deixa de conversar com ele.
> Reabra a conexão com os novos parâmetros.

---

## 5. Uso via CLI

Leitura simples (endereço 2, 9600 baud):

```bash
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600
# RS-WS-N01-2D @ endereço 2 — 9600 baud
#   humidity     reg 0x0000  bruto  508  =     50.8 %RH
#   temperature  reg 0x0001  bruto  259  =     25.9 °C
```

Saída JSON e leitura contínua (polling):

```bash
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --json
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --watch --interval 2
```

Filtragem de bloco, rejeição de outliers, EWMA e estatísticas:

```bash
# média de 5 leituras, rejeitando outliers, com desvio/incerteza
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 \
    --samples 5 --filter mean --reject --stats
# suavização contínua (bom com --watch)
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --watch --ewma 0.3
```

Escala/linearização por medição (`--map`, índice **1=umidade, 2=temperatura**):

```bash
# temperatura em °F
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 \
    --map 2:0:50:32:122:degF
#   temperature  reg 0x0001  bruto  259  =    78.62 degF  (25.9 °C)
```

Configuração (executa e sai):

```bash
# mostrar endereço e baud atuais
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --show-config
# RS-WS-N01-2D — endereço 2, baud 9600

# gravar novo endereço (depois reabra com -a 5)
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --set-address 5

# gravar novo baud (depois reabra com -b 4800)
python drivers/rs_ws_n01_2d.py -p /dev/ttyUSB1 -a 2 -b 9600 --set-baud 4800
```

> Se você não souber o endereço/baud atuais do sensor, use o
> [`modbus_scanner.py`](../modbus_scanner.py) (`scan`/`baud`) para descobri-los.

---

## 6. Uso como biblioteca

```python
from drivers.rs_ws_n01_2d import RSWSN012D

with RSWSN012D(port="/dev/ttyUSB1", baud=9600, address=2) as dev:
    for m in dev.read_measurements():
        print(m["name"], m["value"], m["unit"])
    print(dev.read_config())   # {'address': 2, 'baud_code': 2, 'baud': 9600}
```
