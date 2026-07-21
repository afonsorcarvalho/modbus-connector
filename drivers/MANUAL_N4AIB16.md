# Manual de Registradores — Conversor N4AIB16 (eletechsup / Electrosup)

Documentação do protocolo Modbus RTU do módulo **N4AIB16**, coletor analógico →
RS485. Base para o driver [`n4aib16.py`](./n4aib16.py).

> ⚠️ **Aviso de calibração.** O mapa de leitura dos canais (`0x0000+`) e os
> function codes estão confirmados na documentação oficial da série N4AI. Os
> endereços exatos dos registradores de **configuração** (endereço/baud rate)
> variam ligeiramente entre firmwares e não aparecem completos nas páginas
> públicas — confirme no PDF que acompanha o seu módulo. Os valores marcados com
> _(verificar)_ abaixo são a convenção da série, mas devem ser validados no
> equipamento real.

---

## 1. Identificação do dispositivo

| Item | Especificação |
|---|---|
| Modelo | N4AIB16 |
| Família | eletechsup série N4AI (N4AIA04 / N4AIA08 / **N4AIB16** / N4AIC24) |
| Função | Aquisição analógica → RS485 (conversor A/D + Modbus) |
| Canais | **16 canais**, ADC de **12 bits** (leitura já escalonada, ver §5) |
| — CH1 … CH15 | Entradas de **corrente** 0–20 mA / 4–20 mA |
| — CH16 | Entrada de **tensão** 0–30 V |
| Alimentação | DC 7–25 V (típico 12 V / 24 V) |
| Consumo | standby 6–8 mA · operação 10–30 mA |
| Dispositivos em paralelo | até 64 no barramento |

---

## 2. Parâmetros de comunicação

| Parâmetro | Valor |
|---|---|
| Protocolo | Modbus RTU |
| Meio físico | RS485 (2 fios, A/B) |
| Baud rate padrão | **9600** |
| Baud rates suportados | 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200 |
| Data bits | 8 |
| Stop bits | 1 (2 suportado) |
| Paridade | None (padrão) · Even · Odd |
| Endereço padrão | **1** (faixa 1–247) |
| Function codes — leitura | **04** (input) ✅ testado · 03 (holding) → exceção 0x02 |
| Function codes — escrita | **06** (único) · **16** (múltiplos) |
| Ordem de bytes | Big-endian (MSB primeiro) |

---

## 3. Mapa de registradores — leitura de canais (FC 04)

Cada canal ocupa **1 registrador de 16 bits**. Bloco contíguo a partir de `0x0000`.
O valor lido **já vem escalonado** pelo módulo: corrente em centésimos de mA
(100 contagens/mA — ver seção 5), não é o bruto "cru" do ADC.

> ✅ **Confirmado em teste real** (módulo em `/dev/ttyUSB0`, 9600 baud, addr 1):
> a leitura funciona por **FC04 (input registers)**. Usar **FC03 (holding)**
> retorna **exceção Modbus 0x02** (illegal data address) neste módulo.

| Registrador (hex) | Registrador (dec) | Canal | Tipo | Faixa física |
|:---:|:---:|:---:|:---:|:---|
| `0x0000` | 0  | CH1  | Corrente | 0–20 / 4–20 mA |
| `0x0001` | 1  | CH2  | Corrente | 0–20 / 4–20 mA |
| `0x0002` | 2  | CH3  | Corrente | 0–20 / 4–20 mA |
| `0x0003` | 3  | CH4  | Corrente | 0–20 / 4–20 mA |
| `0x0004` | 4  | CH5  | Corrente | 0–20 / 4–20 mA |
| `0x0005` | 5  | CH6  | Corrente | 0–20 / 4–20 mA |
| `0x0006` | 6  | CH7  | Corrente | 0–20 / 4–20 mA |
| `0x0007` | 7  | CH8  | Corrente | 0–20 / 4–20 mA |
| `0x0008` | 8  | CH9  | Corrente | 0–20 / 4–20 mA |
| `0x0009` | 9  | CH10 | Corrente | 0–20 / 4–20 mA |
| `0x000A` | 10 | CH11 | Corrente | 0–20 / 4–20 mA |
| `0x000B` | 11 | CH12 | Corrente | 0–20 / 4–20 mA |
| `0x000C` | 12 | CH13 | Corrente | 0–20 / 4–20 mA |
| `0x000D` | 13 | CH14 | Corrente | 0–20 / 4–20 mA |
| `0x000E` | 14 | CH15 | Corrente | 0–20 / 4–20 mA |
| `0x000F` | 15 | CH16 | **Tensão** | 0–30 V |

Ler os 16 canais de uma vez: FC03 a partir de `0x0000`, quantidade `0x0010` (16).

---

## 4. Registradores de configuração (FC 06 — escrita) _(verificar)_

> Endereços da convenção da série N4AI. **Confirmar no manual físico** antes de
> escrever — gravar em registrador errado pode reconfigurar o dispositivo.

| Registrador | Função | Valores |
|:---:|:---|:---|
| _(verificar)_ | **Endereço Modbus do dispositivo** | 1–247 |
| _(verificar)_ | **Baud rate** | `0`=1200 · `1`=2400 · `2`=4800 · `3`=**9600** · `4`=19200 · `5`=38400 · `6`=57600 · `7`=115200 |
| _(verificar)_ | **Paridade** | `0`=None · `1`=Even · `2`=Odd |
| _(verificar)_ | Reset de fábrica | conforme manual do firmware |

Após alterar endereço/baud rate, o novo valor normalmente só vale após
reinicialização (power cycle) do módulo.

---

## 5. Conversão valor lido → grandeza física

> ✅ **Calibração medida no módulo real (2026-07-21).** Injetando **3,00 mA**
> na entrada, o módulo retornou **300 bruto**. Ou seja, o N4AIB16 **NÃO** entrega
> o bruto "cru" do ADC de 12 bits — ele já escalona internamente em passos fixos:
> **100 contagens por mA, offset zero**. A conversão é linear e direta.

### Corrente (CH1–CH15)

```
mA = raw / 100          # 100 contagens por mA (medido: 3 mA = 300)
```

Vale para os dois tipos de transmissor — o módulo reporta a corrente REAL:
- 0–20 mA → bruto 0…2000
- 4–20 mA → bruto 400…2000 (a mesma fórmula já dá 4,00…20,00 mA)

O parâmetro `current_mode` é mantido só por compatibilidade de API; não altera
o valor em mA.

### Tensão (CH16)

```
V = raw / 100           # assumido 100 contagens/V — NÃO confirmado, recalibrar
```

⚠️ A escala da tensão é **suposta por analogia**. Injete uma tensão conhecida no
CH16 e ajuste `COUNTS_PER_V` se o bruto não bater com `V × 100`.

Referência rápida (corrente, confirmada):

| raw | mA |
|:---:|:---:|
| 0    | 0,00 mA  |
| 300  | **3,00 mA** ← ponto medido |
| 400  | 4,00 mA  |
| 1000 | 10,00 mA |
| 2000 | 20,00 mA |

> As constantes `COUNTS_PER_MA` (100) e `COUNTS_PER_V` (100) estão no topo de
> [`n4aib16.py`](./n4aib16.py) e podem ser reajustadas conforme a calibração real.

---

## 6. Exemplos de frames Modbus RTU

Formato: `[addr] [func] [dados...] [CRC_lo] [CRC_hi]` (CRC16 Modbus, little-endian).

### 6.1 Ler apenas o CH1 (endereço 1, **FC04**, 1 registrador)

```
Envia:  01 04 00 00 00 01 31 CA
Recebe: 01 04 02 01 4B  <CRC>          → 0x014B = 331 (bruto)
                                          331/100 = 3,31 mA
```

### 6.2 Ler todos os 16 canais (endereço 1, **FC04**)

```
Envia:  01 04 00 00 00 10 <CRC>
Recebe: 01 04 20 <32 bytes = 16 registradores> <CRC>
```
`0x20 = 32` bytes de dados = 16 registradores × 2 bytes. Cada par big-endian é
um canal, na ordem CH1…CH16.

### 6.3 Estrutura da resposta de leitura

| Campo | Bytes | Descrição |
|---|:---:|---|
| Endereço | 1 | eco do endereço do escravo |
| Função | 1 | `0x03` ou `0x04` (ou `0x83`/`0x84` em exceção) |
| Byte count | 1 | nº de bytes de dados (2 × nº de registradores) |
| Dados | N | registradores, big-endian |
| CRC | 2 | CRC16 Modbus, little-endian |

### 6.4 Exceção Modbus

Se a função retornar com o bit alto ligado (`0x83`, `0x84`), o byte seguinte é o
código de exceção — o dispositivo **está presente**, mas rejeitou o pedido
(ex.: registrador inexistente). Formato: `[addr] [func|0x80] [código] [CRC]`.

---

## 7. Uso com o driver

```python
from drivers.n4aib16 import N4AIB16

with N4AIB16(port="/dev/ttyUSB0", baud=9600, address=1, current_mode="4-20") as dev:
    for ch in dev.read_channels():
        print(ch)   # {channel, register, raw, value, unit, type}
```

CLI:

```bash
python drivers/n4aib16.py -p /dev/ttyUSB0 -b 9600 -a 1                 # convertido
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --current-mode 4-20     # 4-20 mA
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --raw                   # valores brutos
python drivers/n4aib16.py -p /dev/ttyUSB0 -a 1 --json                  # JSON
```

Para varrer endereço/baud rate desconhecidos, use o `modbus_scanner.py` do projeto:

```bash
python modbus_scanner.py baud -p /dev/ttyUSB0        # varre baud + endereço
python modbus_scanner.py read -p /dev/ttyUSB0 -a 1 --reg 0 --count 16
```

---

## 8. Fontes

- Página oficial N4AIA08/N4AIB16/N4AIC24 — eletechsup.com
- Manual do protocolo Modbus RTU da série N4AIA04 (mesmo protocolo)
- Demo N4AIA04 com Modbus Poll (YouTube)

## Filtros de leitura e escala (map)

O driver reaproveita a biblioteca genérica `common/` (filtros + escala),
compartilhável com outros drivers. Pipeline por canal:

    ler bloco → [rejeitar outliers] → reduzir → [EWMA] → [map]

Cada estágio entre colchetes é opcional. Com `--samples 1` (padrão) o
comportamento é o da leitura única de sempre.

### Filtros de bloco
- `--samples N` — nº de leituras por valor (padrão 1 = leitura única).
- `--filter {mean,median,trimmed}` — redutor (padrão mean). Para picos
  esporádicos, `median` é a escolha robusta.
- `--trim FRAC` — fração aparada por ponta (só para `trimmed`).
- `--reject` / `--reject-k K` — rejeita outliers por MAD antes de reduzir.
  Observação: o MAD não dispara quando mais da metade das amostras é idêntica
  (comum em contagens inteiras estáveis); nesse caso a rejeição é no-op e a
  proteção contra picos fica por conta do `--filter median`.
- `--stats` — mostra desvio-padrão `s`, incerteza `u = s/√n` e `n` por canal.

### EWMA (suavização contínua, com estado)
- `--ewma ALPHA` (0<α≤1) — filtro exponencial `y = α·x + (1-α)·y_ant`, ideal
  com `--watch`. α maior segue o sinal mais rápido; α menor filtra mais ruído.

### Map (escala linear por canal)
- `--map CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE]` — repetível; cada map
  define uma escala e a lista de canais que a seguem. Um canal segue no máximo
  um map; canal sem map continua em mA/V.
- `--map-clamp` — limita a saída à faixa de saída quando a entrada extrapola.

No JSON de saída, o canal mapeado reporta `value`/`unit` na unidade nova e
preserva o valor físico (`mA`/`V`) e o `raw` para rastreabilidade.

Exemplos:

    # 10 amostras, mediana, com incerteza
    python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median --stats

    # monitoramento contínuo suavizado
    python drivers/n4aib16.py -p /dev/ttyUSB0 --watch --ewma 0.2

    # 4-20 mA -> 0-10 bar nos canais 1,4,6 ; 0-100 % no canal 2
    python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median \
        --map 1,4,6:4:20:0:10:bar --map 2:4:20:0:100:%

---

_Documento de referência do projeto `modbus-connector`. Campos marcados
_(verificar)_ devem ser confirmados contra o módulo físico._
