#!/usr/bin/env python3
"""Executor dos sinais na Binance Futures — SOMENTE TESTNET (dinheiro de mentira).

Uso:
  python3 executor_binance.py sinais.json [--avaliacao avaliacao.json] [--simular] [--pausado] > execucao.json

Entrada: a mesma lista de sinais do painel (coleção de futuros); --avaliacao aplica por cima a
conferência mais recente (a saída do monitor.py), para não abrir um sinal que acabou de bater no stop.
Chaves: variáveis de ambiente BINANCE_TESTNET_API_KEY e BINANCE_TESTNET_API_SECRET (de uma conta do
testnet: https://testnet.binancefuture.com). Sem elas, roda em simulação.

O que faz em cada execução:
- Abre os sinais LONG/SHORT ainda válidos ("aguardando" ou "aberto"), novos e antigos, os mais novos primeiro:
  entrada a mercado (imediato) ou limitada (condicional) e, junto, a proteção: stop da posição inteira,
  metade no alvo 1 e metade no alvo 2. Tamanho igual ao da gestão dos analistas (a carteira fictícia,
  --carteira): o mesmo valor em risco da carteira (1% do saldo dela) e posição de no máximo 5x o saldo
  dela. Sem --carteira, 1% do saldo do testnet. Na corretora a alavancagem fica em 10x só para prender
  menos margem: ela não muda tamanho, stop nem risco (a conta é em margem cruzada).
- A Binance só aceita stop e alvo pela API de ordens algo (POST /fapi/v1/algoOrder, algoType
  CONDITIONAL), e "fechar posição" só com posição aberta; por isso a proteção vai com quantidade e
  "só reduzir" (reduceOnly), o que vale antes de a entrada condicional executar.
- Segue o painel (a conta de teste tem preços próprios, então pode divergir da OKX): sinal que acionou
  no painel e não no testnet entra pelo preço; alvo 1 no painel realiza a metade pelo preço e leva o
  stop para a entrada; sinal encerrado no painel (stop, alvo, prazo ou expirado) sai pelo preço.
- Cuida dos que já abriu: quando o alvo 1 executa, move o stop da metade restante para a entrada;
  recoloca proteção que sumiu; cancela a entrada condicional que não executou em 48h; fecha pelo preço
  a operação com mais de 7 dias; limpa ordens que sobraram de uma posição já fechada.
- Não guarda estado: cada ordem tem um código "rd-<id do sinal>-<papel>" (clientOrderId ou
  clientAlgoId), e o que já foi feito é lido da própria corretora. Rodar duas vezes não duplica nada.

Segue os sinais como os analistas publicam, sem limite de quantidade de trades nem de perda do dia.
Só ficam as checagens técnicas: só aceita endereços do testnet; pula a moeda que não existe no testnet
e o sinal cujo preço já passou do stop ou do alvo 1; nenhuma posição fica sem stop; um símbolo com
posição ou ordens de outro sinal não recebe outro (a conta tem uma posição por símbolo); --pausado só
protege e administra o que já está aberto.
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

from radar_comum import ABERTOS, FINAIS, carregar_sinais, iso, ms, numero

BASES_TESTNET = ("https://testnet.binancefuture.com",)  # a Demo Trading (demo-fapi) também bloqueia estes servidores (HTTP 451)
RISCO_PCT = 1.0
ALAV_PADRAO = 5           # quando o sinal não traz alavancagem_max
ALAV_MARGEM = 10          # alavancagem posta na corretora: só define a margem presa, não o tamanho
TAMANHO_MAX_X_SALDO = 5   # como a carteira fictícia: posição de no máximo 5x o saldo
GATILHO_MAX_H = 48         # entrada condicional não executada é cancelada depois disso
OPERACAO_MAX_H = 7 * 24
PREFIXO = "rd-"
PAPEIS = ("e", "s", "t1", "t2", "be", "r", "t3", "x", "m")  # entrada, stop, alvo 1, alvo 2, stop na entrada, stop recolocado, alvo 2 recolocado, saída pelo preço, metade pelo preço
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
            # stop e alvos disparam ordens a mercado: valem os dois tetos (LOT_SIZE e MARKET_LOT_SIZE)
            tetos = [float(f[k]["maxQty"]) for k in ("LOT_SIZE", "MARKET_LOT_SIZE") if float(f.get(k, {}).get("maxQty") or 0) > 0]
            out[s["symbol"]] = {"tick": f["PRICE_FILTER"]["tickSize"], "step": f["LOT_SIZE"]["stepSize"],
                                "min_qtd": f["LOT_SIZE"]["minQty"], "min_nocional": f.get("MIN_NOTIONAL", {}).get("notional", "5"),
                                "max_qtd": min(tetos) if tetos else None}
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


def sinais_para_abrir(sinais: List[dict], agora_ms: int) -> List[dict]:
    """LONG/SHORT ainda válidos no painel ("aguardando" ou "aberto"), de qualquer idade, mais novos primeiro.
    Um sinal antigo entra atrasado: planejar_entrada só o pula se o preço já passou do stop ou do alvo 1."""
    out = []
    for s in sinais:
        if s.get("direcao") not in ("LONG", "SHORT") or s.get("status") not in (None, "aguardando", "aberto"):
            continue
        try:
            t = ms(s["at"])
        except Exception:
            continue
        if t > agora_ms or (s.get("status") in (None, "aguardando") and agora_ms - t > GATILHO_MAX_H * 3600_000):
            continue  # ainda no futuro, ou gatilho vencido (o painel vai marcar expirado)
        out.append(s)
    return sorted(out, key=lambda s: str(s.get("at")), reverse=True)


def planejar_entrada(s: dict, r: dict, saldo: float, preco: float, gestao: Optional[dict] = None) -> dict:
    """Ordens de abertura de um sinal, ou {"pular": motivo}. `gestao` é a carteira dos analistas
    ({"saldo": saldo dela, "risco": {id: risco em US$ já fixado}}): o tamanho segue o dela."""
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
    imediato = imediato or s.get("status") in ABERTOS  # já acionado no painel: entra pelo preço, como a carteira já está dentro
    ref = preco if imediato else e
    dist = abs(e - st)  # tamanho como o da carteira: risco ÷ distância da entrada do sinal até o stop
    base = float(gestao["saldo"]) if gestao and gestao.get("saldo") else saldo
    risco_usd = float((gestao or {}).get("risco", {}).get(s["id"]) or base * RISCO_PCT / 100)
    alav = max(1, int(numero(s.get("alavancagem_max") or ALAV_PADRAO)))
    qtd = risco_usd / dist
    if qtd * ref > TAMANHO_MAX_X_SALDO * base:  # a carteira limita a posição a 5x o saldo e reduz o risco
        qtd = TAMANHO_MAX_X_SALDO * base / ref
        risco_usd = qtd * dist
    qtd = min(qtd, saldo * ALAV_MARGEM * 0.9 / ref)  # cabe na margem da conta
    if r.get("max_qtd"):
        qtd = min(qtd, float(r["max_qtd"]))  # teto da corretora por ordem (o risco fica menor que 1%)
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
    return {"simbolo": sim, "alavancagem": alav, "quantidade": q, "risco_usd": round(float(q) * dist, 2),
            "ordens": [entrada] + protecao}


def pode_reabrir(ords: Dict[str, dict]) -> bool:
    """O sinal ainda vale no painel, mas a entrada daqui foi cancelada sem executar nada (stop recusado,
    ordem limitada que venceu, cancelamento manual) e não sobrou ordem viva: entra de novo, como o painel."""
    e = ords.get("e")
    return bool(e) and e.get("status") in ("CANCELED", "EXPIRED", "REJECTED") and float(e.get("executedQty") or 0) == 0 \
        and not any(o.get("status") in ("NEW", "PARTIALLY_FILLED", "FILLED") for p, o in ords.items() if p != "e")


def ordens_do_sinal(ordens: List[dict], sinal_id: str) -> Dict[str, dict]:
    """As ordens deste sinal na corretora (comuns e algo, já no mesmo formato), por papel (a mais recente)."""
    out = {}
    for o in sorted(ordens, key=lambda o: o.get("updateTime", 0)):
        for p in PAPEIS:
            if o.get("clientOrderId") == cid(sinal_id, p):
                out[p] = o
    return out


def acoes_de_gestao(s: dict, ords: Dict[str, dict], pos_qtd: float, r: dict, agora_ms: int, dono: bool = True) -> List[dict]:
    """O que fazer com um sinal que já tem ordens. Devolve uma lista de ações
    ({"cancelar": cid, "algo": bool} ou {"enviar": ordem, "algo": bool}, com "motivo").
    `dono`: este é o sinal mais recente com entrada executada no símbolo, ou seja, a posição é dele.
    A Binance segue o painel: quando o painel encerra, realiza a metade no alvo 1 ou aciona a
    entrada, a conta de teste faz o mesmo pelo preço (os preços do testnet são outros)."""
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

    def a_mercado(papel, qtd, motivo):
        return {"enviar": {"symbol": sim, "side": contra, "type": "MARKET", "reduceOnly": "true", "quantity": qtd,
                           "newClientOrderId": cid(sid, papel)}, "algo": False, "motivo": motivo}

    executada = float(e.get("executedQty", 0))
    if executada > 0 and not dono:
        return cancelar_todas("operação anterior neste símbolo já encerrada: limpando ordens dela")
    if e.get("status") in ("NEW", "PARTIALLY_FILLED") and executada == 0:
        if s.get("status") in FINAIS:
            return cancelar_todas(f"sinal encerrado no painel ({s.get('status')}) antes de a entrada executar")
        if s.get("status") in ABERTOS and e.get("type") == "LIMIT" and not pos_qtd:
            # o painel já entrou (a OKX tocou a entrada) e o testnet não: entra pelo preço, mesma quantidade
            lado = "BUY" if sgn > 0 else "SELL"
            return cancelar(["e"], "o sinal já está aberto no painel: trocando a entrada limitada pela entrada a mercado") + [
                {"enviar": {"symbol": sim, "side": lado, "type": "MARKET", "quantity": str(e.get("origQty") or e.get("quantity")),
                            "newClientOrderId": cid(sid, "e")}, "algo": False, "motivo": "entrada pelo preço, como no painel"}]
        # sinal que entrou atrasado (já acionado no painel): as 48h contam de quando a ordem foi posta
        inicio = ms(s["at"])
        if s.get("acionado_em"):
            inicio = max(inicio, int(e.get("time") or e.get("updateTime") or inicio))
        if agora_ms - inicio > GATILHO_MAX_H * 3600_000:
            return cancelar_todas("entrada condicional não executou em 48h")
        return []
    if e.get("status") in ("CANCELED", "EXPIRED", "REJECTED") and executada == 0:
        return cancelar_todas("entrada cancelada")
    if not pos_qtd:
        return cancelar_todas("posição já encerrada: limpando ordens que sobraram")
    acoes = []
    if s.get("status") in FINAIS:  # stop, alvo 2, prazo, ou expirou sem acionar no painel
        acoes += cancelar_todas(f"sinal encerrado no painel ({s.get('status')})")
        acoes.append(a_mercado("x", arred_qtd(abs(pos_qtd), r["step"]), f"sinal encerrado no painel ({s.get('status')}): saída pelo preço, como a carteira"))
        return acoes
    aberta_em = int(e.get("updateTime") or ms(s["at"]))
    try:  # entrada atrasada: o prazo de 7 dias é o do sinal no painel, não o da ordem
        aberta_em = min(aberta_em, ms(s["acionado_em"])) if s.get("acionado_em") else aberta_em
    except Exception:
        pass
    if agora_ms - aberta_em > OPERACAO_MAX_H * 3600_000:
        acoes += cancelar_todas("prazo de 7 dias")
        acoes.append(a_mercado("x", arred_qtd(abs(pos_qtd), r["step"]), "prazo de 7 dias: saída pelo preço"))
        return acoes
    resto = arred_qtd(abs(pos_qtd), r["step"])
    preco_medio = float(e.get("avgPrice") or 0) or numero(s["entrada"])
    t1_feito = ords.get("t1", {}).get("status") == "FILLED" or ords.get("m", {}).get("status") == "FILLED"
    tem_stop_vivo = any(p in vivas for p in ("s", "be", "r"))
    if s.get("status") == "alvo1 (aberto)" and not t1_feito and "be" not in ords and "m" not in ords:
        # o painel realizou a metade no alvo 1 e o testnet não: realiza pelo preço e leva o stop para a entrada
        metade = arred_qtd(abs(pos_qtd) / 2, r["step"])
        if Decimal(metade) >= Decimal(r["min_qtd"]):
            sobra = str(Decimal(resto) - Decimal(metade))
            acoes += cancelar(["t1", "s", "r"], "alvo 1 no painel: realizando a metade")
            acoes.append(a_mercado("m", metade, "alvo 1 no painel: metade pelo preço, como a carteira"))
            acoes.append(prot("be", "STOP_MARKET", preco_medio, sobra, "stop da metade restante na entrada"))
            if "t2" in vivas and Decimal(str(vivas["t2"].get("quantity") or "0")) > Decimal(sobra):
                acoes += cancelar(["t2"], "alvo 2 ajustado à metade restante")
                acoes.append(prot("t3", "TAKE_PROFIT_MARKET", numero(s["alvo2"]), sobra, "alvo 2 da metade restante"))
            return acoes
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

def executar(sinais: List[dict], cli, simular: bool, pausado: bool, agora_ms: Optional[int] = None,
             gestao: Optional[dict] = None) -> dict:
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
    com_ordens = []
    for s in sinais:
        if s.get("direcao") not in ("LONG", "SHORT") or simbolo_de(s.get("ativo", "")) not in regras:
            continue
        sim = simbolo_de(s["ativo"])
        try:
            ords = ordens_do_sinal(ordens_de(sim, s), s["id"])
        except BinanceErro as e:
            rel["erros"].append(f"{s['id']}: {e}")
            continue
        if ords:
            com_ordens.append((s, sim, ords))
    # a posição de um símbolo é do sinal mais recente com entrada executada (um só por vez por símbolo)
    dono: Dict[str, tuple] = {}
    for s, sim, ords in com_ordens:
        e = ords.get("e") or {}
        if float(e.get("executedQty") or 0) > 0:
            quando = int(e.get("time") or e.get("updateTime") or 0)
            if sim not in dono or quando >= dono[sim][0]:
                dono[sim] = (quando, s["id"])
    for s, sim, ords in com_ordens:
        if any(o.get("status") in ("NEW", "PARTIALLY_FILLED") for o in ords.values()):
            ocupados.add(sim)
        pos = float(posicoes.get(sim, {}).get("positionAmt", 0))
        for a in acoes_de_gestao(s, ords, pos, regras[sim], agora_ms, dono=dono.get(sim, (0, s["id"]))[1] == s["id"]):
            fazer(a, s["id"])

    def por_alavancagem(sim) -> bool:
        """Põe a alavancagem de margem no símbolo (se o símbolo não aceitar 10x, tenta menos)."""
        ultimo = None
        for x in (ALAV_MARGEM, 5, 3, 2, 1):
            try:
                cli.alavancagem(sim, x)
                return True
            except BinanceErro as e:
                ultimo = e
        rel["erros"].append(f"{sim}: alavancagem: {ultimo}")
        return False

    # margem: os símbolos que já operam também ficam com a alavancagem de margem (libera margem para os próximos)
    if not simular:
        for sim in sorted(ocupados & set(posicoes)):
            if int(float(posicoes[sim].get("leverage") or 0)) < 5:  # aberta antes da margem de 10x (ou o máximo do símbolo)
                por_alavancagem(sim)

    def desfazer_sem_stop(s, sim, qtd, entrada, enviadas):
        """O stop foi recusado: nenhuma posição pode ficar sem ele. Cancela o que foi posto e,
        se a entrada já executou (a mercado), zera a posição pelo preço."""
        for o in enviadas[1:]:
            fazer({"cancelar": o["clientAlgoId"], "simbolo": sim, "algo": True, "motivo": "stop recusado: desfazendo"}, s["id"])
        if entrada["type"] == "MARKET":
            contra = "BUY" if entrada["side"] == "SELL" else "SELL"
            fazer({"enviar": {"symbol": sim, "side": contra, "type": "MARKET", "reduceOnly": "true", "quantity": qtd,
                              "newClientOrderId": cid(s["id"], "x")}, "algo": False, "motivo": "stop recusado: saindo da posição"}, s["id"])
        else:
            fazer({"cancelar": entrada["newClientOrderId"], "simbolo": sim, "algo": False, "motivo": "stop recusado: cancelando a entrada"}, s["id"])

    # 2) abrir os novos
    if pausado:
        rel["ignorados"].append({"sinal": "-", "motivo": "executor pausado: nenhum trade novo"})
        return rel
    for s in sinais_para_abrir(sinais, agora_ms):
        sim = simbolo_de(s["ativo"])
        if sim not in regras:
            rel["ignorados"].append({"sinal": s["id"], "motivo": f"{sim} não existe no testnet"})
            continue
        anteriores = ordens_do_sinal(ordens_de(sim, s), s["id"])
        if anteriores and not pode_reabrir(anteriores):
            continue  # já executado antes
        if sim in ocupados:
            rel["ignorados"].append({"sinal": s["id"], "motivo": f"{sim} já tem posição ou ordens abertas"})
            continue
        try:
            plano = planejar_entrada(s, regras[sim], saldo, cli.preco(sim), gestao)
        except BinanceErro as e:
            rel["erros"].append(f"{s['id']}: {e}")
            continue
        if "pular" in plano:
            rel["ignorados"].append({"sinal": s["id"], "motivo": plano["pular"]})
            continue
        if not simular and not por_alavancagem(sim):
            continue
        enviadas = []
        for i, o in enumerate(plano["ordens"]):
            risco_txt = f"{plano['risco_usd']:.2f}".replace(".", ",")
            fazer({"enviar": o, "algo": i > 0, "motivo": ("abertura" if i == 0 else "proteção") + f" (risco US$ {risco_txt}, sinal {plano['alavancagem']}x)"}, s["id"])
            erro = rel["acoes"][-1].get("erro")
            if i == 0 and erro:
                break  # entrada recusada: não manda a proteção
            if i == 1 and erro and not simular:
                desfazer_sem_stop(s, sim, o["quantity"], plano["ordens"][0], enviadas)
                break
            enviadas.append(o)
        ocupados.add(sim)
    rel["operacoes"] = foto_das_operacoes(cli, sinais, regras)
    return rel


def foto_das_operacoes(cli, sinais: List[dict], regras: Dict[str, dict]) -> List[dict]:
    """Como cada operação está na corretora agora, depois do que esta execução fez (consulta nova, sem cache):
    posição ou entrada pendente, preço de entrada, preço de marcação, resultado em aberto, stop e alvos vivos.
    É o que o painel mostra na seção da Binance, recalculando o resultado com o preço ao vivo."""
    try:
        posicoes = cli.posicoes()
    except BinanceErro:
        return []
    out = []
    por_simbolo: Dict[str, List[dict]] = {}
    for s in sinais:
        if s.get("direcao") in ("LONG", "SHORT") and simbolo_de(s.get("ativo", "")) in regras:
            por_simbolo.setdefault(simbolo_de(s["ativo"]), []).append(s)
    for sim, lista in sorted(por_simbolo.items()):
        try:
            todas = cli.ordens(sim) + cli.ordens_algo(sim)
        except BinanceErro:
            continue
        pos = posicoes.get(sim) or {}
        qtd_pos = float(pos.get("positionAmt") or 0)
        for s in lista:
            ords = ordens_do_sinal(todas, s["id"])
            e = ords.get("e")
            vivas = {p: o for p, o in ords.items() if o.get("status") in ("NEW", "PARTIALLY_FILLED")}
            if not e or not (vivas or (e.get("status") == "FILLED" and qtd_pos)):
                continue

            def disparo(*papeis):
                for p in papeis:
                    if p in vivas and vivas[p].get("triggerPrice"):
                        return float(vivas[p]["triggerPrice"])
                return None
            pendente = e.get("status") in ("NEW", "PARTIALLY_FILLED") and float(e.get("executedQty") or 0) == 0
            entrada = float(e.get("price") or 0) if pendente else float(pos.get("entryPrice") or e.get("avgPrice") or 0)
            qtd = float(e.get("origQty") or e.get("quantity") or 0) * (1 if s["direcao"] == "LONG" else -1) if pendente else qtd_pos
            stop = disparo("be", "r", "s")
            marca = float(pos.get("markPrice") or 0) or None
            if marca is None:
                try:
                    marca = cli.preco(sim)
                except (BinanceErro, OSError, ValueError):
                    pass
            out.append({
                "sinal": s["id"], "ativo": s.get("ativo"), "simbolo": sim, "direcao": s["direcao"],
                "situacao": "entrada pendente" if pendente else "posição aberta",
                "quantidade": qtd, "entrada": entrada,
                "preco_marca": marca,
                "pnl_aberto": None if pendente else round(float(pos.get("unRealizedProfit") or 0), 2),
                "stop": stop, "alvo1": disparo("t1"), "alvo2": disparo("t2", "t3"),
                "stop_na_entrada": "be" in vivas,
                "alvo1_feito": ords.get("t1", {}).get("status") == "FILLED" or ords.get("m", {}).get("status") == "FILLED",
                "risco_usd": round(abs(qtd) * abs(entrada - stop), 2) if stop and entrada else None,
            })
    return out


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


def ler_gestao(caminho: Optional[str]) -> Optional[dict]:
    """Da carteira fictícia: o saldo e o risco em US$ que ela fixou para cada operação aberta."""
    if not caminho:
        return None
    try:
        with open(caminho) as f:
            c = json.load(f)
        abertas = json.loads(c.get("abertas_json") or "[]")
        return {"saldo": float(c["saldo"]), "risco": {o["id"]: float(o["risco_usd"]) for o in abertas if o.get("risco_usd")}}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sinais")
    ap.add_argument("--avaliacao", help="conferência mais recente (saída --avaliacao do monitor.py)")
    ap.add_argument("--simular", action="store_true", help="mostra o que faria, sem enviar ordens")
    ap.add_argument("--carteira", help="carteira fictícia dos analistas (saída --carteira do monitor.py): o tamanho segue o dela")
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
        rel = executar(sinais, cli, simular, a.pausado, gestao=ler_gestao(a.carteira))
    except (BinanceErro, urllib.error.URLError, OSError) as e:
        rel = {"at": iso(int(time.time() * 1000)), "erros": [f"falha de comunicação com o testnet: {e}"], "acoes": [], "ignorados": []}
    if not (chave and segredo):
        rel["aviso"] = "sem BINANCE_TESTNET_API_KEY/SECRET: rodou em simulação"
    # campos de lista também como texto JSON, para o documento do painel
    for k in ("acoes", "ignorados", "erros", "posicoes", "operacoes"):
        rel[k + "_json"] = json.dumps(rel.get(k, []), ensure_ascii=False, separators=(",", ":"))
    json.dump(rel, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
