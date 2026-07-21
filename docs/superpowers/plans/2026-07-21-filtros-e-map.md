# Filtros compartilhados + map por canal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar uma biblioteca genérica de filtros de sinal (`common/filters.py`) e escala linear (`common/scaling.py`), reusável por qualquer driver, e integrá-la ao driver `n4aib16.py` com novos flags de CLI.

**Architecture:** Dois módulos puros em `common/` (só stdlib `statistics`/`math`), sem dependência de Modbus/serial. O driver consome esses módulos num pipeline por canal: `ler bloco → [rejeitar outliers] → reduzir → [EWMA] → [map] → resultado`. Testes com `unittest` da stdlib, drivers testados com `read_raw` mockado (sem hardware).

**Tech Stack:** Python 3.13, stdlib apenas (`statistics`, `math`, `dataclasses`, `unittest`, `unittest.mock`). Dependência de runtime do projeto continua só `pyserial`.

## Global Constraints

- **Sem novas dependências.** Só stdlib + `pyserial` (já presente). Nada de numpy/scipy/pandas.
- **Interpretador:** `.venv-modbus/bin/python` (Python 3.13.5).
- **Retrocompatibilidade:** chamadas atuais de `read_channels()` e a CLI sem flags novas devem funcionar idênticas. `samples=1, method="mean"` = uma leitura única, saída igual à de hoje.
- **Diretório de trabalho:** raiz do projeto `/home/fitadigital/modbus-connector`. Rodar testes da raiz.
- **Usar `fmean`** (não `mean`) no caminho de leitura.
- **Idioma:** docstrings/comentários em português, seguindo o estilo do código existente.

---

## File Structure

- `common/__init__.py` — marca o pacote (vazio).
- `common/filters.py` — redutores de bloco, `reject_outliers`, `block_stats`, classe `EWMA`.
- `common/scaling.py` — `map_range`, `MapSpec`, `parse_map_arg`, `resolve_maps`.
- `drivers/n4aib16.py` — MODIFICAR: construtor com `ewma_alpha`, `_read_block`, `read_channels` estendido, `reset_filters`, novos flags de CLI e renderização.
- `tests/__init__.py` — marca o pacote de testes (vazio).
- `tests/test_filters.py` — testes de `common/filters.py`.
- `tests/test_scaling.py` — testes de `common/scaling.py`.
- `tests/test_n4aib16_filtered.py` — testes do driver com `read_raw` mockado.

---

### Task 1: Pacote `common/` e redutores de bloco

**Files:**
- Create: `common/__init__.py`
- Create: `common/filters.py`
- Test: `tests/__init__.py`, `tests/test_filters.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `BLOCK_METHODS = ("mean", "median", "trimmed")`
  - `simple_mean(xs: list[float]) -> float`
  - `median(xs: list[float]) -> float`
  - `trimmed_mean(xs: list[float], trim: float = 0.1) -> float`
  - `reduce(xs: list[float], method: str = "median", trim: float = 0.1) -> float`

- [ ] **Step 1: Criar os pacotes vazios**

```bash
mkdir -p common tests
touch common/__init__.py tests/__init__.py
```

- [ ] **Step 2: Escrever os testes que falham** — `tests/test_filters.py`

```python
import unittest

from common.filters import (
    BLOCK_METHODS, simple_mean, median, trimmed_mean, reduce,
)


class TestBlockReducers(unittest.TestCase):
    def test_simple_mean(self):
        self.assertAlmostEqual(simple_mean([2, 4, 6]), 4.0)

    def test_median_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_median_even(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_trimmed_mean_drops_extremes(self):
        # 0 e 100 são descartados (trim=0.1 de 10 -> k=1); média de 1..8
        xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 100]
        self.assertAlmostEqual(trimmed_mean(xs, trim=0.1), 4.5)

    def test_trimmed_mean_zero_trim_is_mean(self):
        self.assertAlmostEqual(trimmed_mean([1, 2, 3], trim=0.0), 2.0)

    def test_trimmed_mean_small_list_falls_back_to_median(self):
        # trim grande esvaziaria; cai para mediana
        self.assertEqual(trimmed_mean([5, 5, 5], trim=0.4), 5)

    def test_trimmed_mean_rejects_bad_trim(self):
        with self.assertRaises(ValueError):
            trimmed_mean([1, 2, 3], trim=0.6)

    def test_reduce_dispatch(self):
        xs = [1, 2, 3, 4, 100]
        self.assertAlmostEqual(reduce(xs, "mean"), 22.0)
        self.assertEqual(reduce(xs, "median"), 3)

    def test_reduce_unknown_method(self):
        with self.assertRaises(ValueError):
            reduce([1, 2, 3], "kalman")

    def test_block_methods_constant(self):
        self.assertEqual(BLOCK_METHODS, ("mean", "median", "trimmed"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Rodar os testes e verificar que falham**

Run: `.venv-modbus/bin/python -m unittest tests.test_filters -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'common.filters'`

- [ ] **Step 4: Implementar `common/filters.py` (parte 1)**

```python
"""Filtros de sinal genéricos, reutilizáveis por qualquer driver do projeto.

Só depende da stdlib (statistics, math). Três grupos:
  - redutores de bloco: reduzem uma lista de amostras a um único valor
    (média simples, mediana, média aparada);
  - rejeição de outlier (MAD) e estatística de bloco;
  - EWMA: filtro exponencial contínuo, com estado entre leituras.

A filosofia do projeto é depender só de pyserial; aqui não há dependência
externa alguma. Usa fmean (float, mais rápido) no caminho de leitura.
"""

import math
import statistics

# Métodos de redução de bloco aceitos por reduce().
BLOCK_METHODS = ("mean", "median", "trimmed")


def simple_mean(xs):
    """Média aritmética simples (float)."""
    return statistics.fmean(xs)


def median(xs):
    """Mediana das amostras."""
    return statistics.median(xs)


def trimmed_mean(xs, trim=0.1):
    """Média aparada: descarta a fração `trim` de cada ponta e faz a média.

    trim=0.1 numa lista de 10 remove 1 elemento de cada lado. Se a aparação
    esvaziaria o núcleo (trim grande p/ lista curta), cai para a mediana.
    """
    if not xs:
        raise ValueError("lista vazia")
    if not 0 <= trim < 0.5:
        raise ValueError("trim deve estar em [0, 0.5)")
    ordered = sorted(xs)
    k = int(len(ordered) * trim)
    core = ordered[k:len(ordered) - k] if k else ordered
    if not core:
        return statistics.median(ordered)
    return statistics.fmean(core)


def reduce(xs, method="median", trim=0.1):
    """Reduz um bloco de amostras a um valor, despachando por nome."""
    if method == "mean":
        return simple_mean(xs)
    if method == "median":
        return median(xs)
    if method == "trimmed":
        return trimmed_mean(xs, trim)
    raise ValueError(f"método desconhecido: {method!r} (use {BLOCK_METHODS})")
```

- [ ] **Step 5: Rodar os testes e verificar que passam**

Run: `.venv-modbus/bin/python -m unittest tests.test_filters -v`
Expected: PASS (10 testes)

- [ ] **Step 6: Commit**

```bash
git add common/__init__.py common/filters.py tests/__init__.py tests/test_filters.py
git commit -m "feat(filters): redutores de bloco (media/mediana/aparada)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Rejeição de outlier (MAD) e estatística de bloco

**Files:**
- Modify: `common/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: `statistics`, `math` (já importados na Task 1).
- Produces:
  - `reject_outliers(xs: list[float], k: float = 3.0) -> list[float]`
  - `block_stats(xs: list[float]) -> dict` com chaves `n, mean, median, s, u, min, max`.

- [ ] **Step 1: Escrever os testes que falham** — adicionar a `tests/test_filters.py`

Adicione o import no topo:

```python
from common.filters import (
    BLOCK_METHODS, simple_mean, median, trimmed_mean, reduce,
    reject_outliers, block_stats,
)
```

Adicione as classes de teste antes do `if __name__`:

```python
class TestRejectOutliers(unittest.TestCase):
    def test_removes_spike(self):
        xs = [10, 10, 11, 10, 9, 10, 500]
        kept = reject_outliers(xs, k=3.0)
        self.assertNotIn(500, kept)
        self.assertEqual(len(kept), 6)

    def test_keeps_clean_data(self):
        xs = [10, 11, 9, 10, 12, 8]
        self.assertEqual(sorted(reject_outliers(xs)), sorted(xs))

    def test_mad_zero_returns_all(self):
        xs = [7, 7, 7, 7]
        self.assertEqual(reject_outliers(xs), xs)

    def test_short_list_unchanged(self):
        self.assertEqual(reject_outliers([1, 100]), [1, 100])

    def test_never_empties(self):
        # mesmo com dados patológicos, nunca devolve lista vazia
        self.assertTrue(len(reject_outliers([1, 2, 3, 4, 5])) >= 1)


class TestBlockStats(unittest.TestCase):
    def test_stats_basic(self):
        st = block_stats([2, 4, 6])
        self.assertEqual(st["n"], 3)
        self.assertAlmostEqual(st["mean"], 4.0)
        self.assertEqual(st["median"], 4)
        self.assertAlmostEqual(st["s"], 2.0)  # stdev amostral de 2,4,6
        self.assertAlmostEqual(st["u"], 2.0 / math.sqrt(3))
        self.assertEqual(st["min"], 2)
        self.assertEqual(st["max"], 6)

    def test_stats_single_sample(self):
        st = block_stats([5])
        self.assertEqual(st["n"], 1)
        self.assertEqual(st["s"], 0.0)
        self.assertEqual(st["u"], 0.0)

    def test_stats_empty_raises(self):
        with self.assertRaises(ValueError):
            block_stats([])
```

Adicione `import math` no topo do arquivo de teste (usado no cálculo esperado de `u`).

- [ ] **Step 2: Rodar e verificar que falham**

Run: `.venv-modbus/bin/python -m unittest tests.test_filters -v`
Expected: FAIL com `ImportError: cannot import name 'reject_outliers'`

- [ ] **Step 3: Implementar em `common/filters.py`** — adicionar após `reduce`

```python
def reject_outliers(xs, k=3.0):
    """Remove outliers por MAD (mediana dos desvios absolutos), robusto.

    Mantém os pontos com |x - mediana| <= k * 1.4826 * MAD (o fator normaliza
    o MAD para equivaler a um desvio-padrão em dados gaussianos). Devolve uma
    nova lista; nunca esvazia (se removeria tudo, ou MAD==0, ou a lista tem
    menos de 3 pontos, devolve os dados originais).
    """
    if len(xs) < 3:
        return list(xs)
    med = statistics.median(xs)
    mad = statistics.median([abs(x - med) for x in xs])
    if mad == 0:
        return list(xs)
    threshold = k * 1.4826 * mad
    kept = [x for x in xs if abs(x - med) <= threshold]
    return kept if kept else list(xs)


def block_stats(xs):
    """Estatística de um bloco: {n, mean, median, s, u, min, max}.

    s = desvio-padrão amostral (n-1); para n<2, s=0. u = s/sqrt(n) é a
    incerteza padrão da média (Tipo A).
    """
    n = len(xs)
    if n == 0:
        raise ValueError("lista vazia")
    s = statistics.stdev(xs) if n >= 2 else 0.0
    return {
        "n": n,
        "mean": statistics.fmean(xs),
        "median": statistics.median(xs),
        "s": s,
        "u": s / math.sqrt(n),
        "min": min(xs),
        "max": max(xs),
    }
```

- [ ] **Step 4: Rodar e verificar que passam**

Run: `.venv-modbus/bin/python -m unittest tests.test_filters -v`
Expected: PASS (18 testes)

- [ ] **Step 5: Commit**

```bash
git add common/filters.py tests/test_filters.py
git commit -m "feat(filters): rejeicao de outlier (MAD) e block_stats

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: EWMA (filtro exponencial com estado)

**Files:**
- Modify: `common/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: nada novo.
- Produces: classe `EWMA`:
  - `EWMA(alpha: float = 0.2, initial: float | None = None)`
  - `.update(x: float) -> float`
  - `.value -> float | None` (property)
  - `.reset() -> None`

- [ ] **Step 1: Escrever os testes que falham** — adicionar a `tests/test_filters.py`

Atualize o import para incluir `EWMA`:

```python
from common.filters import (
    BLOCK_METHODS, simple_mean, median, trimmed_mean, reduce,
    reject_outliers, block_stats, EWMA,
)
```

Adicione a classe de teste:

```python
class TestEWMA(unittest.TestCase):
    def test_first_sample_initializes(self):
        f = EWMA(alpha=0.5)
        self.assertIsNone(f.value)
        self.assertEqual(f.update(10), 10)
        self.assertEqual(f.value, 10)

    def test_smoothing(self):
        f = EWMA(alpha=0.5)
        f.update(10)                    # estado = 10
        self.assertEqual(f.update(20), 15)   # 0.5*20 + 0.5*10

    def test_initial_value(self):
        f = EWMA(alpha=0.5, initial=0)
        self.assertEqual(f.update(10), 5)    # 0.5*10 + 0.5*0

    def test_reset(self):
        f = EWMA(alpha=0.5)
        f.update(10)
        f.reset()
        self.assertIsNone(f.value)
        self.assertEqual(f.update(99), 99)

    def test_invalid_alpha(self):
        with self.assertRaises(ValueError):
            EWMA(alpha=0)
        with self.assertRaises(ValueError):
            EWMA(alpha=1.5)
```

- [ ] **Step 2: Rodar e verificar que falham**

Run: `.venv-modbus/bin/python -m unittest tests.test_filters -v`
Expected: FAIL com `ImportError: cannot import name 'EWMA'`

- [ ] **Step 3: Implementar em `common/filters.py`** — adicionar ao final

```python
class EWMA:
    """Média móvel exponencial (filtro exponencial) com estado.

    Suaviza uma sequência de leituras entre chamadas:
        y = alpha * x + (1 - alpha) * y_anterior
    alpha maior -> segue mais rápido o sinal, filtra menos ruído.
    A primeira amostra (sem `initial`) inicializa o estado com ela mesma.
    """

    def __init__(self, alpha=0.2, initial=None):
        if not 0 < alpha <= 1:
            raise ValueError("alpha deve estar em (0, 1]")
        self.alpha = alpha
        self._value = None if initial is None else float(initial)

    def update(self, x):
        """Alimenta uma nova amostra e devolve o valor suavizado."""
        if self._value is None:
            self._value = float(x)
        else:
            self._value = self.alpha * x + (1 - self.alpha) * self._value
        return self._value

    @property
    def value(self):
        """Último valor suavizado, ou None se ainda não recebeu amostra."""
        return self._value

    def reset(self):
        """Zera o estado (próxima amostra vira o novo ponto de partida)."""
        self._value = None
```

- [ ] **Step 4: Rodar e verificar que passam**

Run: `.venv-modbus/bin/python -m unittest tests.test_filters -v`
Expected: PASS (23 testes)

- [ ] **Step 5: Commit**

```bash
git add common/filters.py tests/test_filters.py
git commit -m "feat(filters): EWMA (filtro exponencial com estado)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `common/scaling.py` — map linear e MapSpec

**Files:**
- Create: `common/scaling.py`
- Test: `tests/test_scaling.py`

**Interfaces:**
- Consumes: `dataclasses`.
- Produces:
  - `map_range(x, in_min, in_max, out_min, out_max, clamp=False) -> float`
  - dataclass `MapSpec(channels: set, in_min, in_max, out_min, out_max, unit="", clamp=False)` com `.apply(x) -> float`.

- [ ] **Step 1: Escrever os testes que falham** — `tests/test_scaling.py`

```python
import unittest

from common.scaling import map_range, MapSpec


class TestMapRange(unittest.TestCase):
    def test_linear_4_20_to_0_10(self):
        self.assertAlmostEqual(map_range(4, 4, 20, 0, 10), 0.0)
        self.assertAlmostEqual(map_range(20, 4, 20, 0, 10), 10.0)
        self.assertAlmostEqual(map_range(12, 4, 20, 0, 10), 5.0)

    def test_extrapolates_without_clamp(self):
        self.assertAlmostEqual(map_range(2, 4, 20, 0, 10), -1.25)

    def test_clamp_limits_output(self):
        self.assertAlmostEqual(map_range(2, 4, 20, 0, 10, clamp=True), 0.0)
        self.assertAlmostEqual(map_range(30, 4, 20, 0, 10, clamp=True), 10.0)

    def test_inverted_input_range(self):
        # 20 mA -> 0, 4 mA -> 100 (escala invertida)
        self.assertAlmostEqual(map_range(20, 20, 4, 0, 100), 0.0)
        self.assertAlmostEqual(map_range(4, 20, 4, 0, 100), 100.0)

    def test_equal_input_bounds_raises(self):
        with self.assertRaises(ValueError):
            map_range(5, 4, 4, 0, 10)


class TestMapSpec(unittest.TestCase):
    def test_apply(self):
        spec = MapSpec({1, 4, 6}, 4, 20, 0, 10, "bar")
        self.assertAlmostEqual(spec.apply(12), 5.0)
        self.assertEqual(spec.unit, "bar")

    def test_apply_clamp(self):
        spec = MapSpec({1}, 4, 20, 0, 10, "bar", clamp=True)
        self.assertAlmostEqual(spec.apply(2), 0.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e verificar que falham**

Run: `.venv-modbus/bin/python -m unittest tests.test_scaling -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'common.scaling'`

- [ ] **Step 3: Implementar `common/scaling.py` (parte 1)**

```python
"""Escalonamento linear (map) do valor medido para unidade de engenharia.

Genérico, reutilizável por qualquer driver. Converte uma grandeza de uma
faixa de entrada (ex.: 4-20 mA) para uma faixa de saída (ex.: 0-10 bar).
"""

from dataclasses import dataclass


def map_range(x, in_min, in_max, out_min, out_max, clamp=False):
    """Converte x linearmente da faixa [in_min,in_max] para [out_min,out_max].

    Suporta faixas invertidas (in_min > in_max). Com clamp=True, limita a
    saída ao intervalo de saída quando a entrada extrapola.
    """
    if in_min == in_max:
        raise ValueError("in_min e in_max não podem ser iguais")
    y = out_min + (x - in_min) * (out_max - out_min) / (in_max - in_min)
    if clamp:
        lo, hi = min(out_min, out_max), max(out_min, out_max)
        y = max(lo, min(hi, y))
    return y


@dataclass
class MapSpec:
    """Uma escala linear e o conjunto de canais que a seguem."""

    channels: set
    in_min: float
    in_max: float
    out_min: float
    out_max: float
    unit: str = ""
    clamp: bool = False

    def apply(self, x):
        """Aplica esta escala a um valor."""
        return map_range(x, self.in_min, self.in_max,
                         self.out_min, self.out_max, self.clamp)
```

- [ ] **Step 4: Rodar e verificar que passam**

Run: `.venv-modbus/bin/python -m unittest tests.test_scaling -v`
Expected: PASS (7 testes)

- [ ] **Step 5: Commit**

```bash
git add common/scaling.py tests/test_scaling.py
git commit -m "feat(scaling): map_range e MapSpec

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Parse da CLI de map e resolução por canal

**Files:**
- Modify: `common/scaling.py`
- Test: `tests/test_scaling.py`

**Interfaces:**
- Consumes: `MapSpec` (Task 4).
- Produces:
  - `parse_map_arg(s: str, clamp: bool = False) -> MapSpec` — parseia `CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE]`.
  - `resolve_maps(specs: list[MapSpec]) -> dict[int, MapSpec]` — canal→spec, erro se canal repetido.

- [ ] **Step 1: Escrever os testes que falham** — adicionar a `tests/test_scaling.py`

Atualize o import:

```python
from common.scaling import map_range, MapSpec, parse_map_arg, resolve_maps
```

Adicione as classes de teste:

```python
class TestParseMapArg(unittest.TestCase):
    def test_full(self):
        spec = parse_map_arg("1,4,6:4:20:0:10:bar")
        self.assertEqual(spec.channels, {1, 4, 6})
        self.assertEqual(spec.in_min, 4.0)
        self.assertEqual(spec.in_max, 20.0)
        self.assertEqual(spec.out_min, 0.0)
        self.assertEqual(spec.out_max, 10.0)
        self.assertEqual(spec.unit, "bar")

    def test_without_unit(self):
        spec = parse_map_arg("2:4:20:0:100")
        self.assertEqual(spec.channels, {2})
        self.assertEqual(spec.unit, "")

    def test_clamp_flag_propagates(self):
        spec = parse_map_arg("1:4:20:0:10:bar", clamp=True)
        self.assertTrue(spec.clamp)

    def test_too_few_fields(self):
        with self.assertRaises(ValueError):
            parse_map_arg("1:4:20:0")

    def test_too_many_fields(self):
        with self.assertRaises(ValueError):
            parse_map_arg("1:4:20:0:10:bar:extra")

    def test_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_map_arg("1:quatro:20:0:10")

    def test_bad_channel(self):
        with self.assertRaises(ValueError):
            parse_map_arg("x,y:4:20:0:10")


class TestResolveMaps(unittest.TestCase):
    def test_maps_channels(self):
        a = parse_map_arg("1,4:4:20:0:10:bar")
        b = parse_map_arg("2:4:20:0:100:%")
        resolved = resolve_maps([a, b])
        self.assertIs(resolved[1], a)
        self.assertIs(resolved[4], a)
        self.assertIs(resolved[2], b)

    def test_duplicate_channel_raises(self):
        a = parse_map_arg("1,2:4:20:0:10:bar")
        b = parse_map_arg("2:4:20:0:100:%")
        with self.assertRaises(ValueError):
            resolve_maps([a, b])
```

- [ ] **Step 2: Rodar e verificar que falham**

Run: `.venv-modbus/bin/python -m unittest tests.test_scaling -v`
Expected: FAIL com `ImportError: cannot import name 'parse_map_arg'`

- [ ] **Step 3: Implementar em `common/scaling.py`** — adicionar ao final

```python
def parse_map_arg(s, clamp=False):
    """Parseia a string de CLI de um map.

    Formato: CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE]
    CANAIS é uma lista separada por vírgula (ex.: "1,4,6").
    """
    parts = s.split(":")
    if len(parts) < 5 or len(parts) > 6:
        raise ValueError(
            f"map inválido: {s!r} "
            "(esperado CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE])"
        )
    try:
        channels = {int(c) for c in parts[0].split(",") if c.strip() != ""}
    except ValueError:
        raise ValueError(f"canais inválidos em {s!r}")
    if not channels:
        raise ValueError(f"nenhum canal em {s!r}")
    try:
        in_min, in_max, out_min, out_max = (float(p) for p in parts[1:5])
    except ValueError:
        raise ValueError(f"valores numéricos inválidos em {s!r}")
    unit = parts[5] if len(parts) == 6 else ""
    return MapSpec(channels, in_min, in_max, out_min, out_max, unit, clamp)


def resolve_maps(specs):
    """Constrói o dicionário canal->MapSpec, um spec por canal.

    Erro se o mesmo canal aparecer em mais de um map.
    """
    resolved = {}
    for spec in specs:
        for ch in spec.channels:
            if ch in resolved:
                raise ValueError(f"canal {ch} aparece em mais de um map")
            resolved[ch] = spec
    return resolved
```

- [ ] **Step 4: Rodar e verificar que passam**

Run: `.venv-modbus/bin/python -m unittest tests.test_scaling -v`
Expected: PASS (16 testes)

- [ ] **Step 5: Commit**

```bash
git add common/scaling.py tests/test_scaling.py
git commit -m "feat(scaling): parse_map_arg e resolve_maps

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Integração no driver — `read_channels` filtrado

**Files:**
- Modify: `drivers/n4aib16.py` (imports; `__init__`; novos métodos `_read_block`, `read_channels`, `reset_filters`)
- Test: `tests/test_n4aib16_filtered.py`

**Interfaces:**
- Consumes: `reduce`, `reject_outliers`, `block_stats`, `EWMA` de `common.filters`; `resolve_maps` de `common.scaling`; `raw_to_current`, `raw_to_voltage`, `NUM_CHANNELS`, `CURRENT_CHANNELS`, `BASE_REGISTER` (já no módulo).
- Produces:
  - `N4AIB16(..., ewma_alpha: float | None = None)`
  - `read_channels(self, samples=1, method="mean", trim=0.1, reject=False, reject_k=3.0, interval=0.0, with_stats=False, maps=None) -> list[dict]`
  - `reset_filters(self) -> None`
  - Dict por canal: sempre `{channel, register, raw, value, unit, type}`; com `maps`, ganha o campo físico (`"mA"` ou `"V"`) e `value`/`unit` na unidade nova; com `with_stats`, ganha `stats: {n, s, u, min, max}`.

- [ ] **Step 1: Escrever os testes que falham** — `tests/test_n4aib16_filtered.py`

```python
import unittest
from unittest.mock import patch

from drivers.n4aib16 import N4AIB16
from common.scaling import parse_map_arg


def make_dev(**kwargs):
    """Cria um N4AIB16 sem abrir porta serial (open_serial mockado)."""
    with patch("drivers.n4aib16.open_serial", return_value=object()):
        return N4AIB16(port="/dev/null", **kwargs)


def frame(ch1):
    """Um frame de 16 canais com CH1=ch1 e o resto zero."""
    return [ch1] + [0] * 15


class TestReadChannelsFiltered(unittest.TestCase):
    def test_single_read_backward_compatible(self):
        dev = make_dev()
        dev.read_raw = lambda: frame(400)
        chans = dev.read_channels()  # defaults: samples=1
        self.assertEqual(chans[0]["raw"], 400)
        self.assertAlmostEqual(chans[0]["value"], 4.0)
        self.assertEqual(chans[0]["unit"], "mA")
        self.assertNotIn("stats", chans[0])
        self.assertNotIn("mA", chans[0])  # sem map: campo físico não é duplicado

    def test_block_median_reduces(self):
        dev = make_dev()
        frames = iter([frame(400), frame(500), frame(402)])  # 500 é spike
        dev.read_raw = lambda: next(frames)
        chans = dev.read_channels(samples=3, method="median")
        # mediana de [400,500,402] = 402 -> 4.02 mA
        self.assertAlmostEqual(chans[0]["value"], 4.02)

    def test_reject_then_reduce(self):
        dev = make_dev()
        frames = iter([frame(400), frame(401), frame(399),
                       frame(400), frame(9000)])  # 9000 é outlier
        dev.read_raw = lambda: next(frames)
        chans = dev.read_channels(samples=5, method="mean", reject=True)
        # sem o 9000, média ~ 400 -> ~4.0 mA (não puxado para cima)
        self.assertLess(chans[0]["value"], 4.1)

    def test_discards_comm_failure(self):
        dev = make_dev()
        seq = [frame(400), "fail", frame(402), frame(404)]
        it = iter(seq)

        def fake_read():
            v = next(it)
            if v == "fail":
                raise RuntimeError("timeout")
            return v

        dev.read_raw = fake_read
        chans = dev.read_channels(samples=3, method="mean")
        # 3 leituras boas: 400,402,404 -> 402 -> 4.02 mA
        self.assertAlmostEqual(chans[0]["value"], 4.02)

    def test_with_stats(self):
        dev = make_dev()
        frames = iter([frame(400), frame(402), frame(404)])
        dev.read_raw = lambda: next(frames)
        chans = dev.read_channels(samples=3, method="mean", with_stats=True)
        st = chans[0]["stats"]
        self.assertEqual(st["n"], 3)
        self.assertIn("u", st)
        self.assertGreater(st["s"], 0)

    def test_map_applied_per_channel(self):
        dev = make_dev()
        dev.read_raw = lambda: frame(1200)  # 12.00 mA
        spec = parse_map_arg("1:4:20:0:10:bar")
        chans = dev.read_channels(maps=[spec])
        self.assertAlmostEqual(chans[0]["value"], 5.0)  # 12 mA -> 5 bar
        self.assertEqual(chans[0]["unit"], "bar")
        self.assertAlmostEqual(chans[0]["mA"], 12.0)  # físico preservado
        # canal sem map continua em mA
        self.assertEqual(chans[1]["unit"], "mA")

    def test_ewma_smooths_between_calls(self):
        dev = make_dev(ewma_alpha=0.5)
        frames = iter([frame(400), frame(800)])  # 4.0 mA depois 8.0 mA
        dev.read_raw = lambda: next(frames)
        first = dev.read_channels()[0]["value"]
        second = dev.read_channels()[0]["value"]
        self.assertAlmostEqual(first, 4.0)
        self.assertAlmostEqual(second, 6.0)  # 0.5*8 + 0.5*4
        dev.reset_filters()
        third = dev.read_channels.__self__  # smoke: reset_filters existe
        self.assertTrue(hasattr(dev, "reset_filters"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e verificar que falham**

Run: `.venv-modbus/bin/python -m unittest tests.test_n4aib16_filtered -v`
Expected: FAIL (`read_channels() got an unexpected keyword argument 'samples'` ou `unexpected keyword argument 'ewma_alpha'`)

- [ ] **Step 3: Atualizar imports em `drivers/n4aib16.py`**

Após o bloco `try/except` que importa de `modbus_scanner` (por volta da linha 50), adicione:

```python
from common.filters import reduce, reject_outliers, block_stats, EWMA  # noqa: E402
from common.scaling import resolve_maps  # noqa: E402
```

- [ ] **Step 4: Adicionar `ewma_alpha` ao construtor**

Em `N4AIB16.__init__`, adicione o parâmetro e o estado. Substitua a assinatura e o corpo atuais:

```python
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
```

- [ ] **Step 5: Substituir `read_channels` e adicionar `_read_block` / `reset_filters`**

Substitua o método `read_channels` inteiro (linhas ~125-152) por:

```python
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
                    k: (round(v, 5) if isinstance(v, float) else v)
                    for k, v in st.items() if k in ("n", "s", "u", "min", "max")
                }

            result.append(entry)
        return result

    def reset_filters(self):
        """Zera o estado dos filtros EWMA de todos os canais."""
        self._ewma.clear()
```

- [ ] **Step 6: Rodar e verificar que passam**

Run: `.venv-modbus/bin/python -m unittest tests.test_n4aib16_filtered -v`
Expected: PASS (7 testes)

- [ ] **Step 7: Rodar a suíte inteira (garantir que nada quebrou)**

Run: `.venv-modbus/bin/python -m unittest discover -s tests -v`
Expected: PASS (todos os testes das Tasks 1-6)

- [ ] **Step 8: Commit**

```bash
git add drivers/n4aib16.py tests/test_n4aib16_filtered.py
git commit -m "feat(n4aib16): read_channels com filtros, EWMA e map por canal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Flags de CLI e renderização

**Files:**
- Modify: `drivers/n4aib16.py` (`build_parser`, `render_once`, `main`)
- Test: manual (a CLI é fina; a lógica já é coberta pelos testes da Task 6)

**Interfaces:**
- Consumes: `read_channels(...)` e `ewma_alpha` (Task 6); `parse_map_arg` de `common.scaling`.
- Produces: flags `--samples`, `--filter`, `--trim`, `--reject`, `--reject-k`, `--ewma`, `--sample-interval`, `--stats`, `--map` (repetível), `--map-clamp`.

- [ ] **Step 1: Importar `parse_map_arg`**

Ajuste o import de scaling em `drivers/n4aib16.py`:

```python
from common.scaling import resolve_maps, parse_map_arg  # noqa: E402
```

- [ ] **Step 2: Adicionar os flags em `build_parser`**

Antes do `return p`, adicione:

```python
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
```

- [ ] **Step 3: Passar os filtros em `render_once`**

Substitua a chamada `channels = dev.read_channels()` em `render_once` por:

```python
    channels = dev.read_channels(
        samples=args.samples, method=args.filter, trim=args.trim,
        reject=args.reject, reject_k=args.reject_k,
        interval=args.sample_interval, with_stats=args.stats,
        maps=args._map_specs,
    )
```

E substitua o laço de impressão (o `else:` que imprime cada canal) por uma versão que mostra a unidade nova e, se houver, o físico e as stats:

```python
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
```

- [ ] **Step 4: Parsear os maps em `main` (antes de abrir o dispositivo) e passar `ewma_alpha`**

No início de `main()`, depois de `args = build_parser().parse_args()`, adicione o parse dos maps e a validação:

```python
    try:
        args._map_specs = [parse_map_arg(s, clamp=args.map_clamp)
                           for s in args.maps]
        resolve_maps(args._map_specs)   # valida canal duplicado cedo
    except ValueError as e:
        sys.exit(f"Erro no --map: {e}")
```

E na construção do dispositivo, adicione `ewma_alpha=args.ewma`:

```python
        with N4AIB16(args.port, baud=args.baud, address=args.address,
                     function=args.function, databits=args.databits,
                     parity=args.parity, stopbits=args.stopbits,
                     timeout=args.timeout, current_mode=args.current_mode,
                     ewma_alpha=args.ewma) as dev:
```

- [ ] **Step 5: Verificar o help da CLI**

Run: `.venv-modbus/bin/python drivers/n4aib16.py --help`
Expected: aparecem os flags `--samples`, `--filter`, `--ewma`, `--map`, `--stats`, etc., sem erro.

- [ ] **Step 6: Smoke test do parse de map inválido (sem hardware)**

Run: `.venv-modbus/bin/python drivers/n4aib16.py -p /dev/null --map 1:4:20:0 2>&1 | head -1`
Expected: `Erro no --map: map inválido: '1:4:20:0' ...` (falha no parse, antes de tocar a serial)

- [ ] **Step 7: Rodar a suíte inteira**

Run: `.venv-modbus/bin/python -m unittest discover -s tests -v`
Expected: PASS (tudo verde)

- [ ] **Step 8: Commit**

```bash
git add drivers/n4aib16.py
git commit -m "feat(n4aib16): flags de CLI para filtros, EWMA, stats e map

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Documentação (README + MANUAL)

**Files:**
- Modify: `README.md` (seção de uso do driver)
- Modify: `drivers/MANUAL_N4AIB16.md` (nova seção sobre filtros e map)

**Interfaces:**
- Consumes: os flags de CLI da Task 7.
- Produces: documentação; sem código.

- [ ] **Step 1: Documentar em `drivers/MANUAL_N4AIB16.md`**

Adicione ao final do arquivo uma nova seção:

```markdown
## Filtros de leitura e escala (map)

O driver reaproveita a biblioteca genérica `common/` (filtros + escala),
compartilhável com outros drivers. Pipeline por canal:

    ler bloco → [rejeitar outliers] → reduzir → [EWMA] → [map]

### Filtros de bloco
- `--samples N` — nº de leituras por valor (padrão 1 = leitura única).
- `--filter {mean,median,trimmed}` — redutor (padrão mean).
- `--trim FRAC` — fração aparada por ponta (só para trimmed).
- `--reject` / `--reject-k K` — rejeita outliers por MAD antes de reduzir.
- `--stats` — mostra desvio-padrão s, incerteza u = s/√n e n por canal.

### EWMA (suavização contínua)
- `--ewma ALPHA` (0<α≤1) — filtro exponencial com estado, ideal com `--watch`.

### Map (escala por canal)
- `--map CANAIS:IN_MIN:IN_MAX:OUT_MIN:OUT_MAX[:UNIDADE]` — repetível.
- `--map-clamp` — limita a saída à faixa de saída.

Exemplos:

    # 10 amostras, mediana, com incerteza
    python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median --stats

    # monitoramento contínuo suavizado
    python drivers/n4aib16.py -p /dev/ttyUSB0 --watch --ewma 0.2

    # 4-20 mA -> 0-10 bar nos canais 1,4,6 ; 0-100 % no canal 2
    python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median \
        --map 1,4,6:4:20:0:10:bar --map 2:4:20:0:100:%
```

- [ ] **Step 2: Documentar em `README.md`**

Localize a seção de exemplos do driver N4AIB16 no `README.md` e adicione, logo após ela, um parágrafo curto:

```markdown
### Filtros e escala

O driver aceita filtros de leitura (bloco de N amostras com média/mediana/média
aparada, rejeição de outlier, EWMA) e escala por canal (map 4-20 mA → unidade de
engenharia). Veja `drivers/MANUAL_N4AIB16.md` §"Filtros de leitura e escala".
Exemplo:

    python drivers/n4aib16.py -p /dev/ttyUSB0 --samples 10 --filter median --stats \
        --map 1,4,6:4:20:0:10:bar
```

- [ ] **Step 3: Commit**

```bash
git add README.md drivers/MANUAL_N4AIB16.md
git commit -m "docs: filtros de leitura e map no README e manual do N4AIB16

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- `common/filters.py` (mean/median/trimmed/reduce) → Task 1 ✓
- `reject_outliers` (MAD), `block_stats` → Task 2 ✓
- `EWMA` → Task 3 ✓
- `common/scaling.py` (`map_range`, `MapSpec`) → Task 4 ✓
- `parse_map_arg`, `resolve_maps` (canal duplicado) → Task 5 ✓
- Driver: `_read_block`, `read_channels` estendido, EWMA por canal, `reset_filters`, campo físico + stats + map → Task 6 ✓
- CLI: todos os flags da §5.4 → Task 7 ✓
- Testes (filters, scaling, driver mockado) → Tasks 1-6 ✓
- Docs → Task 8 ✓
- Retrocompatibilidade (samples=1) → coberto por `test_single_read_backward_compatible` (Task 6) ✓

**Placeholder scan:** sem TBD/TODO; todo passo tem código ou comando concreto.

**Type consistency:** `reduce(xs, method, trim)`, `reject_outliers(xs, k)`, `block_stats(xs)`, `EWMA(alpha, initial)`, `map_range(x, in_min, in_max, out_min, out_max, clamp)`, `MapSpec(channels, in_min, in_max, out_min, out_max, unit, clamp)`, `parse_map_arg(s, clamp)`, `resolve_maps(specs)`, `read_channels(samples, method, trim, reject, reject_k, interval, with_stats, maps)` — nomes consistentes entre as tasks que definem e as que consomem. O campo físico preservado usa a mesma string de unidade (`"mA"`/`"V"`) devolvida por `_physical`, e a renderização (Task 7) lê essa mesma chave.
