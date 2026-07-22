# Driver RS-WS-N01-2D — Design

Sensor de umidade/temperatura industrial (LCD, montagem em parede, RS-485
Modbus RTU). Este driver adiciona suporte de leitura e configuração ao projeto
`modbus-connector`, seguindo o padrão já estabelecido em `drivers/n4aib16.py`.

## Contexto do hardware (confirmado no dispositivo real)

Testado em `/dev/ttyUSB1`, endereço 2, 9600 baud (FC03):

| Reg (hex) | Reg (dec) | Conteúdo | Acesso | Leitura real |
|---|---|---|---|---|
| 0x0000 | 0 | Umidade (real ×10) | R | 495 → 49,5 %RH |
| 0x0001 | 1 | Temperatura (real ×10) | R | 243 → 24,3 °C |
| 0x07D0 | 2000 | Endereço do dispositivo | R/W | 2 |
| 0x07D1 | 2001 | Código de baud | R/W | 2 (= 9600) |

Comunicação: 8 bits de dados, sem paridade, 1 stop, CRC. Baud de fábrica 4800;
suportados 2400 / 4800 / 9600.

**Função de leitura:** FC03 (Read Holding Registers) — confirmada no hardware,
consistente com o endereçamento PLC 4xxxx da tabela. Fica configurável por flag.

**Tabela de código de baud (confirmada empiricamente):** o registrador 0x07D1
guarda um índice, não o valor do baud. Sensor a 9600 reporta `2`, portanto:

    {2400: 0, 4800: 1, 9600: 2}

## Objetivos

1. Ler temperatura e umidade com conversão física correta.
2. Reaproveitar o pipeline de filtros/EWMA/stats/map de `common/`.
3. Ler e escrever os registradores de configuração (endereço e baud) via FC06.
4. CLI no mesmo estilo flat do `n4aib16.py` (leitura + config), sem hardware
   para os testes automatizados.

Fora de escopo: descoberta automática de endereço/baud (já coberta por
`modbus_scanner.py baud`).

## Conversão física

- **Umidade** (reg 0x0000): `raw / 10` → `%RH`, unsigned (0–100).
- **Temperatura** (reg 0x0001): `raw / 10` → `°C`, **signed** (complemento de 2,
  16 bits). `probe()` devolve o valor unsigned; o driver reinterpreta valores
  `>= 0x8000` como negativos (`raw - 0x10000`) antes de dividir por 10.
- Uma única transação FC03 lê os dois registradores (`start=0x0000, count=2`).

## Componentes

### `drivers/rs_ws_n01_2d.py`

Reutiliza `open_serial`, `probe`, `crc16`, `transaction` de `modbus_scanner.py`
e `reduce`, `reject_outliers`, `block_stats`, `EWMA` de `common/filters.py` mais
`resolve_maps`, `parse_map_arg` de `common/scaling.py`. Só depende de pyserial.

Constantes:

```
NUM_REGISTERS   = 2
BASE_REGISTER   = 0x0000
REG_ADDRESS     = 0x07D0
REG_BAUD        = 0x07D1
HUM_SCALE       = 10.0     # contagens por %RH
TEMP_SCALE      = 10.0     # contagens por °C
BAUD_CODES      = {2400: 0, 4800: 1, 9600: 2}
MEASUREMENTS    = [("humidity", "%RH"), ("temperature", "°C")]
```

Funções puras (testáveis sem serial):

- `to_signed16(raw)` → int com sinal.
- `raw_to_humidity(raw)` → `raw / 10.0`.
- `raw_to_temperature(raw)` → `to_signed16(raw) / 10.0`.

### Classe `RSWSN012D`

```
__init__(port, baud=4800, address=1, function=3,
         databits=8, parity="N", stopbits=1, timeout=0.3, ewma_alpha=None)
```

Métodos:

- `read_raw()` → `[hum_raw, temp_raw]`; `RuntimeError` em falha/timeout.
- `_read_block(samples, interval)` → coleta N leituras, descarta falhas de
  comunicação (mesma lógica do N4AIB16, até `samples` falhas então `RuntimeError`).
- `_physical(raw, index)` → `(valor, unidade, nome)`; index 0=umidade, 1=temp.
- `read_measurements(samples=1, method="mean", trim=0.1, reject=False,
   reject_k=3.0, interval=0.0, with_stats=False, maps=None)` → lista de 2 dicts
  `{name, register, raw, value, unit}`, com o mesmo pipeline por-medição do
  N4AIB16 (rejeição de outliers → redução → EWMA → map; stats opcional).
  `maps` usa índice 1-based: `1`=umidade, `2`=temperatura.
- `reset_filters()` — zera EWMA.
- `read_config()` → `{"address": int, "baud_code": int, "baud": int|None}`
  (`baud` resolvido pela tabela inversa, `None` se código desconhecido).
- `set_address(new)` — valida `1 <= new <= 247`, escreve reg 0x07D0 via FC06.
- `set_baud(baud)` — valida `baud in BAUD_CODES`, escreve o código no reg 0x07D1
  via FC06.
- `_write_register(reg, value)` — monta frame FC06 (`addr, 0x06, reg_hi, reg_lo,
  val_hi, val_lo, crc`) com `crc16`, envia por `transaction`, valida o eco
  (resposta FC06 = 8 bytes idênticos à requisição); `RuntimeError` se divergir.
- `close`, `__enter__`, `__exit__`.

Nota: alterar endereço/baud muda como o próprio sensor responde; após um
`set_*` a sessão serial atual pode não conversar mais com o dispositivo. O CLI
avisa o usuário disso.

### CLI (flat, como `n4aib16.py`)

Comum: `-p/--port` (obrigatório), `-b/--baud` (default 4800), `-a/--address`
(default 1), `-f/--function` (3|4, default 3), `--databits/--parity/--stopbits/
--timeout`.

Modo config (mutuamente exclusivo com leitura; se qualquer um for dado, executa
e sai):

- `--show-config` — imprime endereço e baud atuais.
- `--set-address N` — grava novo endereço; avisa que a sessão muda.
- `--set-baud {2400,4800,9600}` — grava novo baud; avisa que a sessão muda.

Modo leitura (default):

- `--raw`, `--json`, `--watch`, `--interval`.
- Filtros: `--samples`, `--filter {mean,median,trimmed}`, `--trim`, `--reject`,
  `--reject-k`, `--ewma ALPHA`, `--sample-interval`, `--stats`.
- `--map SPEC` (repetível), `--map-clamp` — índice 1=umidade, 2=temperatura.

Saída texto exemplo:

```
RS-WS-N01-2D @ endereço 2 — 9600 baud
  umidade      reg 0x0000  bruto  495  =    49.5 %RH
  temperatura  reg 0x0001  bruto  243  =    24.3 °C
```

## Tratamento de erros

- Falha de leitura/timeout → `RuntimeError` com endereço e mensagem do `probe`.
- Eco FC06 divergente → `RuntimeError` (escrita não confirmada).
- Validação de faixa em `set_address`/`set_baud` → `ValueError` antes de tocar
  a serial.
- CLI converte tudo em `sys.exit("Erro: ...")` amigável.

## Testes (`tests/test_rs_ws_n01_2d.py`, unittest, sem hardware)

Serial mockado no estilo `make_dev` do `test_n4aib16_filtered.py`.

1. `to_signed16` / conversão: `243 → 24.3 °C`; `0xFFEC (65516) → -2.0 °C`;
   umidade `495 → 49.5 %RH`.
2. Leitura única compatível: `read_measurements()` retorna 2 medições com
   nome/unidade/register corretos.
3. Pipeline: mediana rejeita spike; `--reject` remove outlier; `with_stats`
   popula `n/s/u`; EWMA suaviza entre chamadas; map por índice converte unidade.
4. Config: `read_config` faz parse de `[2, 2]` → `{address:2, baud_code:2,
   baud:9600}`; `set_baud(9600)` monta frame FC06 correto (reg 0x07D1, valor 2)
   e valida o eco; `set_baud(1200)` levanta `ValueError`; `set_address(300)`
   levanta `ValueError`; eco divergente levanta `RuntimeError`.
5. Frame FC06: CRC correto e bytes esperados para um caso conhecido.

## Validação final (hardware real)

Após os testes passarem, rodar contra `/dev/ttyUSB1 -a 2 -b 9600 -f 3`:
`--show-config` deve reportar endereço 2 / baud 9600; leitura deve bater com os
valores atuais do sensor. (Não alterar endereço/baud do sensor em produção.)

## Documentação

- `drivers/MANUAL_RS_WS_N01_2D.md` com registradores, tabela de baud e exemplos
  de CLI.
- Seção no `README.md` referenciando o novo driver.
