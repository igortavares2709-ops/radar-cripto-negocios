#!/usr/bin/env python3
"""Executor dos sinais na Binance Futures — SOMENTE TESTNET (dinheiro de mentira).

Uso:
  python3 executor_binance.py sinais.json [--avaliacao avaliacao.json] [--simular] [--pausado] > execucao.json

Entrada: a mesma lista de sinais do painel (coleção de futuros); --avaliacao aplica por cima a
conferência mais recente (a saída do monitor.py), para não abrir um sinal que acabou de bater no stop.
Chaves: variáveis de ambiente BINANCE_TESTNET_API_KEY e BINANCE_TESTNET_API_SECRET (de uma conta do
testnet: https://testnet.binancefuture.com). Sem elas, roda em simulação.

O que faz em cada execução:
- Abre os sinais LONG/SHORT novos (publicados nas últimas 6 horas, ainda "aguardando" ou "aberto"):
  entrada a mercado (imediato) ou limitada (condicional) e, junto, a proteção: stop da posição inteira,
  metade no alvo 1 e metade no alvo 2. Tamanho: 1% do saldo de risco até o stop, como a carteira
  fictícia; alavancagem até a do sinal (máx. 5x).
- A Binance só aceita stop e alvo pela API de ordens algo (POST /fapi/v1/algoOrder, algoType
  CONDITIONAL), e "fechar posição" só com posição aberta; por isso a proteção vai com quantidade e
  "só reduzir" (reduceOnly), o que vale antes de a entrada condicional executar.
- Cuida dos que já abriu: quando o alvo 1 executa, move o stop da metade restante para a entrada;
  recoloca proteção que sumiu; cancela a entrada condicional que não executou em 48h; fecha pelo preço
  a operação com mais de 7 dias; limpa ordens que sobraram de uma posição já fechada.
- Não guarda estado: cada ordem tem um código "rd-<id do sinal>-<papel>" (clientOrderId ou
  clientAlgoId), e o que já foi feito é lido da própria corretora. Rodar duas vezes não duplica nada.

Travas: só aceita endereços do testnet; no máximo 4 trades novos por execução e 8 abertos ao mesmo
tempo; não abre novos se a perda realizada do dia passar de 3% do saldo; --pausado só protege e
administra o que já está aberto; um símbolo com posição ou ordens de outro sinal não recebe outro.
Na saída padrão, o relatório em JSON. Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from radar_comum import carregar_sinais, iso, ms, numero

BASES_TESTNET = ("https://testnet.binancefuture.com",)  # a Demo Trading (demo-fapi) também bloqueia estes servidores (HTTP 451)
RISCO_PCT = 1.0
ALAV_MAX = 5
MAX_NOVAS_POR_EXECUCAO = 4
MAX_ABERTAS = 8
PERDA_DIARIA_MAX_PCT = 3.0
JANELA_NOVO_H = 6          # só abre sinais publicados há menos disso
ANDOU_MAX_ATE_ALVO1 = 0.5  # imediato: não entra se o preço já fez mais que isso do caminho entrada -> alvo 1
GATILHO_MAX_H = 48         # entrada condicional não executada é cancelada depois disso
OPERACAO_MAX_H = 7 * 24
PREFIXO = "rd-"
PAPEIS = ("e", "s", "t1", "t2", "be", "r", "t3", "x")  # entrada, stop, alvo 1, alvo 2, stop na entrada, stop recolocado, alvo 2 recolocado, saída no prazo
ALGO = ("s", "t1", "t2", "be", "r", "t3")  # papéis que vão pela API de ordens algo
# estados das ordens algo traduzidos para os das ordens comuns
ESTADO_ALGO = {"NEW": "NEW", "TRIGGERED": "FILLED", "FINISHED": "FILLED", "CANCELED": "CANCELED",
               "EXPIRED": "EXPIRED", "REJECTED": "REJECTED"}


class BinanceErro(Exception):
    def __init__(self, code, msg):
        super().__init__(f"Binance {code}: {msg}")
        self.code, self.msg = code, msg


class ClienteBinance:
    """Cliente mínimo da API de futuros USDT-M, com assinatura HMAC. Recusa endereços fora do testnet."""

    def __init__(self, chave: str, segredo: str, base: str = BASES_TESTNET[0]):
        if base.rstrip("/") not in BASES_TESTNET:
            raise ValueError(f"endereço não permitido: {base} (este executor só opera no testnet)")
        self.chave, self.segredo, self.base = chave, segredo.encode(), base.rstrip("/")
        self.desvio_ms = 0

    def _req(self, metodo: str, caminho: str, params: Optional[dict] = None, assinado: bool = False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if assinado:
            params["timestamp"] = int(time.time() * 1000) + self.desvio_ms
            params["recvWindow"] = 5000
        q = urllib.parse.urlencode(params)
        if assinado:
            q += "&signature=" + hmac.new(self.segredo, q.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(self.base + caminho + ("?" + q if q else ""), method=metodo,
                                     headers={"X-MBX-APIKEY": self.chave, "User-Agent": "radar-executor/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                corpo = json.load(e)
            except Exception:
                corpo = {"code": e.code, "msg": str(e)}
            raise BinanceErro(corpo.get("code"), corpo.get("msg"))

    def sincronizar_relogio(self):
        self.desvio_ms = int(self._req("GET", "/fapi/v1/time")["serverTime"]) - int(time.time() * 1000)

    def regras(self) -> Dict[str, dict]:
        out = {}
        for s in self._req("GET", "/fapi/v1/exchangeInfo")["symbols"]:
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
                continue
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"]] = {"tick": f["PRICE_FILTER"]["tickSize"], "step": f["LOT_SIZE"]["stepSize"],
                                "min_qtd": f["LOT_SIZE"]["minQty"], "min_nocional": f.get("MIN_NOTIONAL", {}).get("notional", "5")}
        return out

    def modo_hedge(self) -> bool:
        return bool(self._req("GET", "/fapi/v1/positionSide/dual", assinado=True).get("dualSidePosition"))

    def saldo_usdt(self) -> float:
        for a in self._req("GET", "/fapi/v2/balance", assinado=True):
            if a.get("asset") == "USDT":
                return float(a["balance"])
        return 0.0

    def pnl_realizado_hoje(self) -> float:
        inicio = int(time.time() // 86400 * 86400 * 1000)
        r = self._req("GET", "/fapi/v1/income", {"incomeType": "REALIZED_PNL", "startTime": inicio, "limit": 1000}, assinado=True)
        return sum(float(x.get("income", 0)) for x in r)

    def posicoes(self) -> Dict[str, dict]:
        return {p["symbol"]: p for p in self._req("GET", "/fapi/v2/positionRisk", assinado=True) if float(p.get("positionAmt", 0))}

    def preco(self, simbolo: str) -> float:
        return float(self._req("GET", "/fapi/v1/ticker/price", {"symbol": simbolo})["price"])

    def ordens(self, simbolo: str, inicio: Optional[int] = None, fim: Optional[int] = None) -> List[dict]:
        """Ordens comuns. Sem janela, a Binance devolve só as dos últimos 7 dias."""
        return self._req("GET", "/fapi/v1/allOrders", {"symbol": simbolo, "limit": 500, "startTime": inicio, "endTime": fim}, assinado=True)

    def alavancagem(self, simbolo: str, x: int):
        return self._req("POST", "/fapi/v1/leverage", {"symbol": simbolo, "leverage": x}, assinado=True)

    def enviar(self, ordem: dict):
        return self._req("POST", "/fapi/v1/order", ordem, assinado=True)

    def cancelar(self, simbolo: str, client_id: str):
        return self._req("DELETE", "/fapi/v1/order", {"symbol": simbolo, "origClientOrderId": client_id}, assinado=True)

    def enviar_algo(self, ordem: dict):
        return self._req("POST", "/fapi/v1/algoOrder", dict(ordem, algoType="CONDITIONAL"), assinado=True)

    def cancelar_algo(self, simbolo: str, client_algo_id: str):
        return self._req("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id}, assinado=True)

    def ordens_algo(self, simbolo: str, inicio: Optional[int] = None, fim: Optional[int] = None) -> List[dict]:
        """Ordens algo (sem janela, só as dos últimos 7 dias), no formato das comuns (clientOrderId, status, executedQty)."""
        out = []
        for o in self._req("GET", "/fapi/v1/allAlgoOrders", {"symbol": simbolo, "limit": 100, "startTime": inicio, "endTime": fim}, assinado=True):
            out.append(dict(o, clientOrderId=o.get("clientAlgoId"), status=ESTADO_ALGO.get(o.get("algoStatus"), o.get("algoStatus")),
                            type=o.get("orderType"), updateTime=o.get("updateTime") or o.get("createTime") or 0, algo=True))
        return out


# ---------------------------------------------------------------- regras puras (testáveis sem corretora)

def cid(sinal_id: str, papel: str) -> str:
    """Código da ordem: rd-<id do sinal>-<papel>, até 36 caracteres e só [a-z0-9-]."""
    base = "".join(ch for ch in str(sinal_id).lower() if ch.isalnum() or ch == "-")
    c = f"{PREFIXO}{base}-{papel}"
    if len(c) > 36:
        c = f"{PREFIXO}{hashlib.sha1(base.encode()).hexdigest()[:24]}-{papel}"
    return c


def arred_preco(p: float, tick: str) -> str:
    t = Decimal(tick)
    return str((Decimal(str(p)) / t).quantize(Decimal(1), rounding=ROUND_HALF_UP) * t)


def arred_qtd(q: float, step: str) -> str:
    s = Decimal(step)
    return str((Decimal(str(q)) / s).quantize(Decimal(1), rounding=ROUND_DOWN) * s)


def simbolo_de(ativo: str) -> str:
    return str(ativo).upper() + "USDT"


def sinais_novos(sinais: List[dict], agora_ms: int) -> List[dict]:
    """LONG/SHORT ainda não encerrados, publicados há menos de JANELA_NOVO_H horas, mais novos primeiro."""
    out = []
    for s in sinais:
        if s.get("direcao") not in ("LONG", "SHORT") or s.get("status") not in (None, "aguardando", "aberto"):
            continue
        try:
            t = ms(s["at"])
        except Exception:
            continue
        if 0 <= agora_ms - t <= JANELA_NOVO_H * 3600_000:
            out.append(s)
    return sorted(out, key=lambda s: str(s.get("at")), reverse=True)


def planejar_entrada(s: dict, r: dict, saldo: float, preco: float) -> dict:
    """Ordens de abertura de um sinal, ou {"pular": motivo}."""
    try:
        e, st, a1, a2 = (numero(s[k]) for k in ("entrada", "stop", "alvo1", "alvo2"))
    except Exception:
        return {"pular": "níveis inválidos no sinal"}
    sgn = 1 if s["direcao"] == "LONG" else -1
    if e <= 0 or e == st or (st - e) * sgn >= 0 or (a1 - e) * sgn <= 0:
        return {"pular": "níveis incoerentes (stop ou alvo do lado errado)"}
    if (preco - st) * sgn <= 0:
        return {"pular": f"o preço ({preco}) já passou do stop"}
    if (preco - a1) * sgn >= 0:
        return {"pular": f"o preço ({preco}) já passou do alvo 1"}
    imediato = s.get("tipo") == "imediato"
    if imediato and (preco - e) * sgn > ANDOU_MAX_ATE_ALVO1 * (a1 - e) * sgn:
        return {"pular": f"o preço ({preco}) já andou mais da metade do caminho até o alvo 1: entrar agora seria correr atrás"}
    ref = preco if imediato else e
    dist = abs(ref - st)
    risco_usd = saldo * RISCO_PCT / 100
    alav = max(1, min(ALAV_MAX, int(numero(s.get("alavancagem_max") or ALAV_MAX))))
    qtd = risco_usd / dist
    qtd = min(qtd, saldo * alav * 0.9 / ref)  # cabe na margem com a alavancagem escolhida
    q = arred_qtd(qtd, r["step"])
    if Decimal(q) < Decimal(r["min_qtd"]) or Decimal(q) * Decimal(str(ref)) < Decimal(r["min_nocional"]):
        return {"pular": "tamanho abaixo do mínimo da corretora para 1% de risco"}
    lado, contra = ("BUY", "SELL") if sgn > 0 else ("SELL", "BUY")
    sid, sim = s["id"], simbolo_de(s["ativo"])
    entrada = {"symbol": sim, "side": lado, "newClientOrderId": cid(sid, "e"), "quantity": q}
    if imediato:
        entrada["type"] = "MARKET"
    else:
        entrada.update(type="LIMIT", timeInForce="GTC", price=arred_preco(e, r["tick"]))
    metade = arred_qtd(float(q) / 2, r["step"])
    if Decimal(metade) < Decimal(r["min_qtd"]):
        metade = "0"
    resto = str(Decimal(q) - Decimal(metade))

    def prot(papel, tipo, preco_disparo, qtd):
        return {"symbol": sim, "side": contra, "type": tipo, "triggerPrice": arred_preco(preco_disparo, r["tick"]),
                "quantity": qtd, "reduceOnly": "true", "workingType": "MARK_PRICE", "clientAlgoId": cid(sid, papel)}
    protecao = [prot("s", "STOP_MARKET", st, q)]
    if metade != "0":
        protecao.append(prot("t1", "TAKE_PROFIT_MARKET", a1, metade))
    protecao.append(prot("t2", "TAKE_PROFIT_MARKET", a2, resto))
    return {"simbolo": sim, "alavancagem": alav, "quantidade": q, "risco_usd": round(risco_usd, 2),
            "ordens": [entrada] + protecao}


def ordens_do_sinal(ordens: List[dict], sinal_id: str) -> Dict[str, dict]:
    """As ordens deste sinal na corretora (comuns e algo, já no mesmo formato), por papel (a mais recente)."""
    out = {}
    for o in sorted(ordens, key=lambda o: o.get("updateTime", 0)):
        for p in PAPEIS:
            if o.get("clientOrderId") == cid(sinal_id, p):
                out[p] = o
    return out


def acoes_de_gestao(s: dict, ords: Dict[str, dict], pos_qtd: float, r: dict, agora_ms: int) -> List[dict]:
    """O que fazer com um sinal que já tem ordens. Devolve uma lista de ações
    ({"cancelar": cid, "algo": bool} ou {"enviar": ordem, "algo": bool}, com "motivo")."""
    sim, sid = simbolo_de(s["ativo"]), s["id"]
    e = ords.get("e")
    if not e:
        return []
    sgn = 1 if s["direcao"] == "LONG" else -1
    contra = "SELL" if sgn > 0 else "BUY"
    vivas = {p: o for p, o in ords.items() if o.get("status") in ("NEW", "PARTIALLY_FILLED")}

    def cancelar(papeis, motivo):
        return [{"cancelar": vivas[p]["clientOrderId"], "simbolo": sim, "algo": p in ALGO, "motivo": motivo} for p in papeis if p in vivas]

    def cancelar_todas(motivo):
        return cancelar(list(vivas), motivo)

    def prot(papel, tipo, preco, qtd, motivo):
        return {"enviar": {"symbol": sim, "side": contra, "type": tipo, "triggerPrice": arred_preco(preco, r["tick"]),
                           "quantity": qtd, "reduceOnly": "true", "workingType": "MARK_PRICE", "clientAlgoId": cid(sid, papel)},
                "algo": True, "motivo": motivo}

    executada = float(e.get("executedQty", 0))
    if e.get("status") in ("NEW", "PARTIALLY_FILLED") and executada == 0:
        if agora_ms - ms(s["at"]) > GATILHO_MAX_H * 3600_000:
            return cancelar_todas("entrada condicional não executou em 48h")
        return []
    if e.get("status") in ("CANCELED", "EXPIRED", "REJECTED") and executada == 0:
        return cancelar_todas("entrada cancelada")
    if not pos_qtd:
        return cancelar_todas("posição já encerrada: limpando ordens que sobraram")
    acoes = []
    aberta_em = int(e.get("updateTime") or ms(s["at"]))
    if agora_ms - aberta_em > OPERACAO_MAX_H * 3600_000:
        acoes += cancelar_todas("prazo de 7 dias")
        acoes.append({"enviar": {"symbol": sim, "side": contra, "type": "MARKET", "reduceOnly": "true",
                                 "quantity": arred_qtd(abs(pos_qtd), r["step"]), "newClientOrderId": cid(sid, "x")},
                      "algo": False, "motivo": "prazo de 7 dias: saída pelo preço"})
        return acoes
    resto = arred_qtd(abs(pos_qtd), r["step"])
    preco_medio = float(e.get("avgPrice") or 0) or numero(s["entrada"])
    t1_feito = ords.get("t1", {}).get("status") == "FILLED"
    tem_stop_vivo = any(p in vivas for p in ("s", "be", "r"))
    if t1_feito and "be" not in ords:
        acoes += cancelar(["s", "r"], "alvo 1 executou: stop vai para a entrada")
        acoes.append(prot("be", "STOP_MARKET", preco_medio, resto, "stop da metade restante na entrada"))
        tem_stop_vivo = True
    if not tem_stop_vivo and "r" not in ords:
        acoes.append(prot("r", "STOP_MARKET", preco_medio if t1_feito else numero(s["stop"]), resto, "posição sem stop: recolocando"))
    alvo2_vivo_ou_feito = any(ords.get(p, {}).get("status") in ("NEW", "PARTIALLY_FILLED", "FILLED") for p in ("t2", "t3"))
    if not alvo2_vivo_ou_feito and "t3" not in ords:
        # o alvo 2 pode ter disparado antes de a entrada executar e ficado sem efeito: recoloca com o que sobrou
        qtd = resto if t1_feito or "t1" not in vivas else arred_qtd(float(Decimal(resto) - Decimal(str(vivas["t1"].get("quantity") or "0"))), r["step"])
        if Decimal(qtd) >= Decimal(r["min_qtd"]):
            acoes.append(prot("t3", "TAKE_PROFIT_MARKET", numero(s["alvo2"]), qtd, "alvo 2 recolocado"))
    return acoes


# ---------------------------------------------------------------- execução

def executar(sinais: List[dict], cli, simular: bool, pausado: bool, agora_ms: Optional[int] = None) -> dict:
    agora_ms = agora_ms or int(time.time() * 1000)
    rel = {"at": iso(agora_ms), "base": getattr(cli, "base", "simulação"), "simulacao": simular, "pausado": pausado,
           "acoes": [], "ignorados": [], "erros": [], "posicoes": []}

    def fazer(acao: dict, sinal_id: str):
        reg = {"sinal": sinal_id, **{k: v for k, v in acao.items() if k != "enviar"}}
        if "enviar" in acao:
            o = acao["enviar"]
            reg["ordem"] = {k: o[k] for k in ("symbol", "side", "type", "quantity", "price", "triggerPrice", "newClientOrderId", "clientAlgoId") if k in o}
        if not simular:
            try:
                if "enviar" in acao:
                    resp = cli.enviar_algo(acao["enviar"]) if acao.get("algo") else cli.enviar(acao["enviar"])
                    reg["status"] = resp.get("algoStatus") or resp.get("status")
                elif acao.get("algo"):
                    cli.cancelar_algo(acao["simbolo"], acao["cancelar"])
                    reg["status"] = "CANCELED"
                else:
                    cli.cancelar(acao["simbolo"], acao["cancelar"])
                    reg["status"] = "CANCELED"
            except BinanceErro as e:
                reg["erro"] = str(e)
                rel["erros"].append(f"{sinal_id}: {e}")
        rel["acoes"].append(reg)

    cli.sincronizar_relogio()
    if cli.modo_hedge():
        rel["erros"].append("a conta está no modo hedge; este executor só opera no modo de posição única (one-way)")
        return rel
    regras = cli.regras()
    saldo = cli.saldo_usdt()
    rel["saldo_usdt"] = round(saldo, 2)
    posicoes = cli.posicoes()
    rel["posicoes"] = [{"simbolo": k, "quantidade": float(p["positionAmt"]), "entrada": float(p.get("entryPrice", 0)),
                        "pnl_aberto": round(float(p.get("unRealizedProfit", 0)), 2)} for k, p in posicoes.items()]
    ordens_cache: Dict[tuple, List[dict]] = {}
    SETE_DIAS = 7 * 86400_000 - 1

    def buscar(sim, inicio=None, fim=None):
        chave = (sim, inicio, fim)
        if chave not in ordens_cache:
            ordens_cache[chave] = cli.ordens(sim, inicio, fim) + cli.ordens_algo(sim, inicio, fim)
        return ordens_cache[chave]

    def ordens_de(sim, sinal=None):
        """As ordens recentes (últimos 7 dias) e, para um sinal, também as da semana em que ele saiu:
        a Binance só devolve 7 dias por consulta, e a entrada de um trade perto do prazo já passou disso."""
        lista = list(buscar(sim))
        if sinal is not None:
            try:
                ini = ms(sinal["at"]) - 3600_000
            except Exception:
                ini = None
            if ini is not None and agora_ms - ini > SETE_DIAS:
                vistos = {o.get("clientOrderId") for o in lista}
                lista += [o for o in buscar(sim, ini, min(ini + SETE_DIAS, agora_ms)) if o.get("clientOrderId") not in vistos]
        return lista

    # 1) administrar o que já foi aberto (sinais com ordens nossas)
    ocupados = set(posicoes)
    for s in sinais:
        if s.get("direcao") not in ("LONG", "SHORT") or simbolo_de(s.get("ativo", "")) not in regras:
            continue
        sim = simbolo_de(s["ativo"])
        try:
            ords = ordens_do_sinal(ordens_de(sim, s), s["id"])
        except BinanceErro as e:
            rel["erros"].append(f"{s['id']}: {e}")
            continue
        if not ords:
            continue
        if any(o.get("status") in ("NEW", "PARTIALLY_FILLED") for o in ords.values()):
            ocupados.add(sim)
        pos = float(posicoes.get(sim, {}).get("positionAmt", 0))
        for a in acoes_de_gestao(s, ords, pos, regras[sim], agora_ms):
            fazer(a, s["id"])

    # 2) abrir os novos
    if pausado:
        rel["ignorados"].append({"sinal": "-", "motivo": "executor pausado: nenhum trade novo"})
        return rel
    try:
        perda = cli.pnl_realizado_hoje()
    except BinanceErro:
        perda = 0.0
    if saldo > 0 and perda < -saldo * PERDA_DIARIA_MAX_PCT / 100:
        rel["ignorados"].append({"sinal": "-", "motivo": f"perda realizada hoje (US$ {perda:.2f}) passou de {PERDA_DIARIA_MAX_PCT}% do saldo"})
        return rel
    novas = 0
    for s in sinais_novos(sinais, agora_ms):
        sim = simbolo_de(s["ativo"])
        if sim not in regras:
            rel["ignorados"].append({"sinal": s["id"], "motivo": f"{sim} não existe no testnet"})
            continue
        if ordens_do_sinal(ordens_de(sim), s["id"]):
            continue  # já executado antes
        if sim in ocupados:
            rel["ignorados"].append({"sinal": s["id"], "motivo": f"{sim} já tem posição ou ordens abertas"})
            continue
        if len(ocupados) >= MAX_ABERTAS or novas >= MAX_NOVAS_POR_EXECUCAO:
            rel["ignorados"].append({"sinal": s["id"], "motivo": "limite de trades simultâneos ou por execução"})
            continue
        try:
            plano = planejar_entrada(s, regras[sim], saldo, cli.preco(sim))
        except BinanceErro as e:
            rel["erros"].append(f"{s['id']}: {e}")
            continue
        if "pular" in plano:
            rel["ignorados"].append({"sinal": s["id"], "motivo": plano["pular"]})
            continue
        if not simular:
            try:
                cli.alavancagem(sim, plano["alavancagem"])
            except BinanceErro as e:
                rel["erros"].append(f"{s['id']}: alavancagem: {e}")
                continue
        for i, o in enumerate(plano["ordens"]):
            risco_txt = f"{plano['risco_usd']:.2f}".replace(".", ",")
            fazer({"enviar": o, "algo": i > 0, "motivo": ("abertura" if i == 0 else "proteção") + f" (risco US$ {risco_txt}, {plano['alavancagem']}x)"}, s["id"])
            if i == 0 and rel["acoes"][-1].get("erro"):
                break  # entrada recusada: não manda a proteção
        ocupados.add(sim)
        novas += 1
    return rel


class SemCorretora:
    """Para --simular sem chaves: responde só o público do testnet e trata a conta como vazia."""

    def __init__(self, base=BASES_TESTNET[0]):
        self.pub = ClienteBinance("", "", base)
        self.base = base + " (simulação, sem chave)"

    def sincronizar_relogio(self): pass
    def modo_hedge(self): return False
    def regras(self): return self.pub.regras()
    def saldo_usdt(self): return 10000.0
    def pnl_realizado_hoje(self): return 0.0
    def posicoes(self): return {}
    def ordens(self, simbolo, inicio=None, fim=None): return []
    def ordens_algo(self, simbolo, inicio=None, fim=None): return []
    def preco(self, simbolo): return self.pub.preco(simbolo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sinais")
    ap.add_argument("--avaliacao", help="conferência mais recente (saída --avaliacao do monitor.py)")
    ap.add_argument("--simular", action="store_true", help="mostra o que faria, sem enviar ordens")
    ap.add_argument("--pausado", action="store_true", help="não abre trades novos; só administra os abertos")
    a = ap.parse_args()
    chave, segredo = os.environ.get("BINANCE_TESTNET_API_KEY"), os.environ.get("BINANCE_TESTNET_API_SECRET")
    base = os.environ.get("BINANCE_TESTNET_BASE", BASES_TESTNET[0])
    if chave and segredo:
        cli = ClienteBinance(chave, segredo, base)
        simular = a.simular
    else:
        cli, simular = SemCorretora(base), True
    sinais = carregar_sinais(a.sinais)
    if a.avaliacao:
        with open(a.avaliacao) as f:
            aval = json.load(f)
        sinais = [dict(x, **aval.get(x.get("id"), {})) for x in sinais]
    try:
        rel = executar(sinais, cli, simular, a.pausado)
    except (BinanceErro, urllib.error.URLError, OSError) as e:
        rel = {"at": iso(int(time.time() * 1000)), "erros": [f"falha de comunicação com o testnet: {e}"], "acoes": [], "ignorados": []}
    if not (chave and segredo):
        rel["aviso"] = "sem BINANCE_TESTNET_API_KEY/SECRET: rodou em simulação"
    # campos de lista também como texto JSON, para o documento do painel
    for k in ("acoes", "ignorados", "erros", "posicoes"):
        rel[k + "_json"] = json.dumps(rel.get(k, []), ensure_ascii=False, separators=(",", ":"))
    json.dump(rel, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
