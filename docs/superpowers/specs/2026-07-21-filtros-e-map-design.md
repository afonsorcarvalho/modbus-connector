# Design — Biblioteca de filtros compartilhada + escala (map) por canal

**Data:** 2026-07-21
**Escopo:** biblioteca genérica de filtros de sinal e escalonamento linear, reusável por qualquer driver, integrada ao driver `n4aib16.py`.

## 1. Motivação

As leituras individuais do N4AIB16 têm ruído medido de ~±8 contagens (s ≈ 0,033 mA por
leitura em 4 mA). Fazer um bloco de N leituras e reduzir (média/mediana) antes de enviar o
valor melhora exatidão e repetibilidade (u = s/√N). Também é comum precisar entregar o valor
já em unidade de engenharia (ex.: 4-20 mA → 0-10 bar). Esta funcionalidade deve ser
**genérica e compartilhada** entre drivers, não específica do N4AIB16.

Decisão de dependências: manter a filosofia do projeto (só `pyserial`). Verificado que o venv
está limpo (só `pip` + `pyserial 3.5`) e que a stdlib `statistics` já cobre mean/median/stdev/
quantiles. Bibliotecas prontas (numpy/scipy/pandas) puxam ≥15-50 MB no Raspberry Pi ARM e
nenhuma oferece EWMA com estado streaming. Portanto: **mini-lib própria sobre `statistics`.**

## 2. Arquitetura

Pacote novo `common/` com dois módulos genéricos, sem dependência de Modbus/serial:

- `common/filters.py` — redutores de bloco, rejeição de outlier, estatística e EWMA.
- `common/scaling.py` — escalonamento linear (map).

O driver `n4aib16.py` consome esses módulos. Pipeline por canal:

```
ler bloco → [rejeitar outliers] → reduzir (mean/median/trimmed) → [EWMA] → [map] → resultado
```

Cada estágio entre colchetes é opcional e configurável.

## 3. `common/filters.py`

Só stdlib (`statistics`, `math`). Usa `fmean` (float, mais rápido) no caminho de leitura.

### 3.1 Redutores de bloco (funções puras sobre lista de amostras)

- `simple_mean(xs) -> float` — `statistics.fmean`.
- `median(xs) -> float` — `statistics.median`.
- `trimmed_mean(xs, trim=0.1) -> float` — descarta a fração `trim` de cada ponta
  (`sorted(xs)[k:-k]`, `k = int(len(xs)*trim)`), depois `fmean`. Se sobrar vazio, cai para
  mediana da lista completa.
- `reduce(xs, method="median", trim=0.1) -> float` — despachante por nome.
  `method ∈ BLOCK_METHODS = ("mean", "median", "trimmed")`.

### 3.2 Rejeição de outlier

- `reject_outliers(xs, k=3.0) -> list` — método **MAD** (mediana dos desvios absolutos, robusto
  quando já há outliers): mantém pontos com `|x - mediana| <= k * 1.4826 * MAD`. Se MAD == 0
  (amostras idênticas), retorna a lista inalterada. Retorna nova lista; nunca esvazia (se tudo
  seria removido, devolve a original).

### 3.3 Estatística

- `block_stats(xs) -> dict` → `{n, mean, median, s, u, min, max}`, com `s = stdev` (amostral,
  n-1; para n<2, `s = 0.0`) e `u = s/√n`.

### 3.4 EWMA (contínuo, com estado)

```python
class EWMA:
    def __init__(self, alpha=0.2, initial=None)   # 0 < alpha <= 1
    def update(self, x) -> float                   # y = alpha*x + (1-alpha)*y_prev
    @property
    def value(self) -> float | None
    def reset(self) -> None
```
Primeira amostra (sem `initial`) inicializa o estado com o próprio `x`. `alpha` fora de
`(0, 1]` → `ValueError`.

## 4. `common/scaling.py`

- `map_range(x, in_min, in_max, out_min, out_max, clamp=False) -> float`
  Linear: `out_min + (x - in_min) * (out_max - out_min) / (in_max - in_min)`.
  - `in_min == in_max` → `ValueError` (divisão por zero).
  - Suporta faixa invertida (ex.: `in` 20→4) e saída invertida.
  - `clamp=True` → limita o resultado a `[min(out_min,out_max), max(out_min,out_max)]`.

- `MapSpec` (dataclass): `channels: set[int]`, `in_min, in_max, out_min, out_max: float`,
  `unit: str = ""`, `clamp: bool = False`.
- `parse_map_arg(s) -> MapSpec` — parseia a string de CLI
  `CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE]` (canais separados por vírgula). Valida
  número de campos e conversões numéricas; erros com mensagem clara.
- `resolve_maps(specs) -> dict[int, MapSpec]` — mapeia canal → spec; **erro se um canal
  aparece em mais de um mapa**.

## 5. Integração em `n4aib16.py`

### 5.1 Construtor

```python
N4AIB16(..., ewma_alpha=None)   # None = EWMA desligado
```
Quando definido, o driver mantém `self._ewma: dict[int, EWMA]` (um por canal, sob demanda) e
expõe `reset_filters()` para zerar o estado.

### 5.2 Método interno de coleta

```python
def _read_block(self, samples, interval) -> list[list[int]]
```
Faz `samples` chamadas `read_raw()` (uma transação Modbus lê os 16 canais de uma vez),
descartando falhas de comunicação (relê até completar N ou estourar um limite de tentativas
= `samples`, então `RuntimeError`). Retorna N listas de 16 brutos. `interval` = espera entre
amostras.

### 5.3 `read_channels(...)` estendido (retrocompatível)

```python
def read_channels(self, samples=1, method="mean", trim=0.1,
                  reject=False, reject_k=3.0, interval=0.0,
                  with_stats=False, maps=None):
```
- `samples=1, method="mean"` (defaults) → uma leitura, **comportamento idêntico ao atual**.
- `samples>1` → coleta bloco, reduz por coluna (canal): opcional `reject_outliers`, depois
  `reduce(method)`. Reduz no domínio **bruto** e converte para mA/V no fim (a conversão é
  linear, então a ordem não altera o valor; reduzir no bruto preserva as contagens inteiras
  para o cálculo de stats).
- EWMA (se `ewma_alpha` definido): aplicado sobre o valor físico reduzido, por canal.
- `maps` (lista de `MapSpec`): se o canal tem mapa, aplica `map_range` sobre o valor físico;
  o dict do canal passa a reportar `value`/`unit` na unidade nova, preservando `raw` e um novo
  campo `mA` (ou `V`) com o valor físico antes do map, para rastreabilidade.
- `with_stats=True` → cada dict de canal ganha `stats: {n, s, u, min, max}` calculado no
  domínio físico (mA/V). Com map ativo, `s`/`u` também são escalados para a unidade de saída.

Formato do dict por canal (campos novos em **negrito**):
`{channel, register, raw, mA|V (físico), value, unit, type, **stats?**}`.

### 5.4 Novos flags de CLI

| Flag | Default | Efeito |
|---|---|---|
| `--samples N` | 1 | nº de leituras por valor (bloco) |
| `--filter {mean,median,trimmed}` | mean | redutor de bloco |
| `--trim FRAC` | 0.1 | fração aparada (só p/ trimmed) |
| `--reject` | off | ativa rejeição de outlier (MAD) antes de reduzir |
| `--reject-k K` | 3.0 | limiar da rejeição, em desvios |
| `--ewma ALPHA` | off | suavização contínua (0<α≤1); útil com `--watch` |
| `--sample-interval S` | 0.0 | espera entre as N amostras (s) |
| `--stats` | off | mostra s, u, n por canal |
| `--map SPEC` | — | repetível: `CANAIS:IN:IN:OUT:OUT[:UNIDADE]` |
| `--map-clamp` | off | limita saída dos maps à faixa de saída |

Exemplos:
```bash
# calibração pontual: 10 amostras, mediana, com incerteza
n4aib16.py -p /dev/ttyUSB0 -a 1 --samples 10 --filter median --stats

# monitoramento contínuo suavizado
n4aib16.py -p /dev/ttyUSB0 -a 1 --watch --ewma 0.2

# escala por canal: 4-20 mA -> 0-10 bar nos canais 1,4,6 ; 0-100 % no canal 2
n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median \
  --map 1,4,6:4:20:0:10:bar --map 2:4:20:0:100:%
```

## 6. Testes

- `common/filters.py` — testes unitários puros (sem hardware): mean/median/trimmed (incl.
  trim que esvazia), `reduce` por nome, `reject_outliers` (com/sem outlier, MAD==0), EWMA
  (init, sequência, alpha inválido, reset), `block_stats` (n=1 e n>1).
- `common/scaling.py` — `map_range` (linear, clamp, extrapolação, faixa invertida, in_min==
  in_max), `parse_map_arg` (válido, campos a menos/mais, não-numérico), `resolve_maps` (canal
  duplicado → erro).
- `n4aib16.py` — `read_channels` filtrado com `read_raw` **mockado** (sem serial): bloco +
  redução, descarte de falha, stats, aplicação de map por canal, EWMA entre chamadas.

Framework: `unittest` da stdlib (sem nova dependência). Rodável via
`.venv-modbus/bin/python -m unittest`.

## 7. Compatibilidade e não-objetivos

- **Retrocompatível:** chamadas atuais de `read_channels()` e a CLI sem flags novas
  funcionam igual. `--samples 1` (default) = uma leitura única.
- **Não-objetivos:** filtros de ordem superior (Kalman, Butterworth), map não-linear
  (polinomial/tabela), persistência de configuração em arquivo. Ficam para depois se
  necessário.
