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
