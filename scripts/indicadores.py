"""Indicadores técnicos: funções puras sobre listas de preços, sem rede e sem estado.

Todas recebem a série do candle mais antigo para o mais novo.
"""
from typing import List, Optional, Sequence, Tuple


def ema(valores: Sequence[float], n: int) -> List[float]:
    """Média móvel exponencial de período n, começando pelo primeiro valor."""
    k = 2 / (n + 1)
    out = [valores[0]]
    for x in valores[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def rsi(fechamentos: Sequence[float], n: int = 14) -> float:
    """RSI de Wilder (0 a 100) no último candle."""
    c = fechamentos
    ganho = perda = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        ganho += max(d, 0)
        perda += max(-d, 0)
    ganho, perda = ganho / n, perda / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        ganho = (ganho * (n - 1) + max(d, 0)) / n
        perda = (perda * (n - 1) + max(-d, 0)) / n
    return 100 if perda == 0 else 100 - 100 / (1 + ganho / perda)


def true_range(h: Sequence[float], l: Sequence[float], c: Sequence[float]) -> List[float]:
    """True range de cada candle a partir do segundo (o primeiro não tem fechamento anterior)."""
    return [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]


def atr_serie(h: Sequence[float], l: Sequence[float], c: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """ATR de Wilder em cada candle. Os n primeiros candles não têm ATR (None)."""
    tr = true_range(h, l, c)
    out: List[Optional[float]] = [None] * len(c)
    if len(tr) < n:
        return out
    a = sum(tr[:n]) / n
    out[n] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i + 1] = a
    return out


def atr(h: Sequence[float], l: Sequence[float], c: Sequence[float], n: int = 14) -> float:
    """ATR de Wilder no último candle."""
    return atr_serie(h, l, c, n)[-1]


def swings(h: Sequence[float], l: Sequence[float], janela: int = 5, olhar: int = 120) -> Tuple[List[float], List[float]]:
    """Topos e fundos locais: máximas (mínimas) que são as maiores (menores) da janela em volta,
    só nos últimos `olhar` candles. Devolve (topos, fundos)."""
    topos, fundos = [], []
    inicio = max(janela, len(h) - olhar)
    for i in range(inicio, len(h) - janela):
        if h[i] == max(h[i - janela:i + janela + 1]):
            topos.append(h[i])
        if l[i] == min(l[i - janela:i + janela + 1]):
            fundos.append(l[i])
    return topos, fundos
