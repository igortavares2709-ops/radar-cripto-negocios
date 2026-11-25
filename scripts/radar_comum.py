"""Peças compartilhadas pelos scripts do radar.

- Cliente da API pública da OKX, com limite de requisições por grupo de rotas (seguro entre threads).
- Arredondamentos, datas e leitura da lista de sinais.
- A gestão de uma operação (stop, alvo 1, alvo 2 e prazo), usada tanto na conferência dos sinais
  publicados (avaliar_sinais.py) quanto no backtest do radar (analise_tecnica.py), para as duas
  seguirem exatamente as mesmas regras.

Só biblioteca padrão.
"""
import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

# ---------------------------------------------------------------- cliente da OKX

OKX_API = "https://www.okx.com/api/v5"
USER_AGENT = "Mozilla/5.0 radar-cripto/1.0"
# requisições por segundo, com folga sobre os limites da OKX por IP (candles 40/2s, public 20/2s, rubik 5/2s)
LIMITES_OKX = {"/market/": 12.0, "/public/": 6.0, "/rubik/": 2.0}


class ClienteOKX:
    """GET na API pública da OKX. Respeita o ritmo de cada grupo de rotas mesmo com várias threads
    e tenta de novo até `tentativas` vezes. Devolve o campo "data" ou None."""

    def __init__(self, limites: Dict[str, float] = LIMITES_OKX, tentativas: int = 3, timeout: float = 20):
        self.limites = dict(limites)
        self.tentativas = tentativas
        self.timeout = timeout
        self._proxima = {g: 0.0 for g in self.limites}
        self._trava = threading.Lock()

    def _esperar_vez(self, path: str) -> None:
        grupo = next((g for g in self.limites if path.startswith(g)), None)
        if not grupo:
            return
        with self._trava:
            agora = time.monotonic()
            quando = max(agora, self._proxima[grupo])
            self._proxima[grupo] = quando + 1.0 / self.limites[grupo]
        if quando > agora:
            time.sleep(quando - agora)

    def get(self, path: str) -> Optional[list]:
        for tentativa in range(self.tentativas):
            self._esperar_vez(path)
            try:
                req = urllib.request.Request(OKX_API + path, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    resposta = json.load(r)
                if resposta.get("code") == "0":
                    return resposta["data"]
            except Exception:
                pass  # rede ou JSON ruim: tenta de novo
            time.sleep(1.5 * (tentativa + 1))
        return None


okx = ClienteOKX()

# ---------------------------------------------------------------- números e datas


def rd_sig(x: Optional[float]) -> Optional[float]:
    """Arredonda por 6 algarismos significativos (serve para o preço do BTC e do PEPE)."""
    return None if x is None else float(f"{x:.6g}")


def rd2(x: Optional[float]) -> Optional[float]:
    """Arredonda em 2 casas (resultados em R e em dólares)."""
    return None if x is None else round(x, 2)


def iso(ms: int) -> str:
    """Milissegundos Unix -> "AAAA-MM-DDTHH:MM:SSZ"."""
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms(texto_iso: str) -> int:
    """"AAAA-MM-DDTHH:MM:SSZ" -> milissegundos Unix."""
    return int(datetime.fromisoformat(texto_iso.replace("Z", "+00:00")).timestamp() * 1000)


def agora_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_compacto(obj) -> str:
    """JSON numa linha, para guardar listas grandes num campo de texto do painel."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- sinais

# situações de um sinal LONG/SHORT depois da conferência
FINAIS_COM_RESULTADO = frozenset({"stop", "alvo1", "alvo2", "encerrado no prazo"})
FINAIS_SEM_OPERACAO = frozenset({"expirado", "invalido"})
FINAIS = FINAIS_COM_RESULTADO | FINAIS_SEM_OPERACAO
ABERTOS = frozenset({"aberto", "alvo1 (aberto)"})


def carregar_sinais(caminho: str) -> List[dict]:
    """Lê a lista de sinais. Aceita uma lista simples de sinais (cada um com "id") ou a saída do
    ArtifactData (objetos {"id", "data"}, soltos ou dentro de "documents"/"docs")."""
    with open(caminho) as f:
        sinais = json.load(f)
    if isinstance(sinais, dict):
        sinais = sinais.get("documents") or sinais.get("docs") or list(sinais.values())
    return [dict(x.get("data", {}), id=x["id"]) if "data" in x else x for x in sinais]


# ---------------------------------------------------------------- gestão de uma operação


@dataclass(frozen=True)
class Plano:
    """Níveis de uma operação. sgn = +1 para LONG, -1 para SHORT."""
    sgn: int
    entrada: float
    stop: float
    alvo1: float
    alvo2: float

    @property
    def risco(self) -> float:
        return abs(self.entrada - self.stop)

    @property
    def r1(self) -> float:
        return abs(self.alvo1 - self.entrada) / self.risco

    @property
    def r2(self) -> float:
        return abs(self.alvo2 - self.entrada) / self.risco

    def r_aberto(self, preco: float, meia: bool) -> float:
        """Resultado em R de uma operação ainda aberta, pelo preço dado."""
        aberto = self.sgn * (preco - self.entrada) / self.risco
        return (self.r1 + aberto) / 2 if meia else aberto


@dataclass(frozen=True)
class Saida:
    """Como a operação terminou (ou não).
    situacao: "stop" | "alvo1" | "alvo2" | "prazo" | "aberto".
    r: resultado em R; em "aberto", o resultado flutuante pelo último fechamento.
    indice: candle da saída (em "aberto", o último candle visto).
    meia: True se a metade da posição já saiu no alvo 1."""
    situacao: str
    r: float
    indice: int
    meia: bool


def gerir(plano: Plano, h: Sequence[float], l: Sequence[float], c: Sequence[float],
          inicio: int, vencido: Callable[[int], bool]) -> Saida:
    """Acompanha a operação candle a candle a partir de `inicio`, com as regras do placar:

    - stop antes de alvo quando os dois cabem no mesmo candle (conservador);
    - no alvo 1 sai metade e o stop da outra metade vai para a entrada;
    - a outra metade sai no alvo 2 ou de volta na entrada;
    - se `vencido(j)` for verdadeiro depois do candle j, sai tudo pelo fechamento dele.
    """
    p = plano
    stop, meia = p.stop, False
    j = inicio - 1
    for j in range(inicio, len(c)):
        bateu_stop = (p.sgn > 0 and l[j] <= stop) or (p.sgn < 0 and h[j] >= stop)
        bateu_a1 = (p.sgn > 0 and h[j] >= p.alvo1) or (p.sgn < 0 and l[j] <= p.alvo1)
        bateu_a2 = (p.sgn > 0 and h[j] >= p.alvo2) or (p.sgn < 0 and l[j] <= p.alvo2)
        if bateu_stop:
            return Saida("alvo1", p.r1 / 2, j, True) if meia else Saida("stop", -1.0, j, False)
        if bateu_a1 and not meia:
            meia, stop = True, p.entrada
        if meia and bateu_a2:
            return Saida("alvo2", (p.r1 + p.r2) / 2, j, True)
        if vencido(j):
            return Saida("prazo", p.r_aberto(c[j], meia), j, meia)
    return Saida("aberto", p.r_aberto(c[j], meia) if j >= inicio else 0.0, j, meia)
