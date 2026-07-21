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
