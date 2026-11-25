"""Testes das regras do radar, sem rede. Rode na raiz do repositório:

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import analise_tecnica as at  # noqa: E402
import avaliar_sinais as av  # noqa: E402
import carteira as ct  # noqa: E402
from indicadores import atr, atr_serie, ema, rsi, swings  # noqa: E402
from radar_comum import Plano, carregar_sinais, gerir, iso, ms, rd2, rd_sig  # noqa: E402

HORA = 3600_000
T0 = ms("2026-11-20T00:00:00Z")


def nunca(_):
    return False


class TestIndicadores(unittest.TestCase):
    def test_ema_de_serie_constante(self):
        self.assertEqual(ema([5.0] * 30, 10), [5.0] * 30)

    def test_rsi_extremos(self):
        self.assertEqual(rsi([float(i) for i in range(30)]), 100)
        self.assertLess(rsi([float(30 - i) for i in range(30)]), 1)

    def test_atr_de_candles_iguais(self):
        h, l, c = [11.0] * 30, [9.0] * 30, [10.0] * 30
        self.assertAlmostEqual(atr(h, l, c), 2.0)
        serie = atr_serie(h, l, c)
        self.assertIsNone(serie[13])
        self.assertAlmostEqual(serie[14], 2.0)
        self.assertEqual(serie[-1], atr(h, l, c))

    def test_swings_acha_topo_e_fundo(self):
        h = [1.0] * 10 + [5.0] + [1.0] * 10
        l = [1.0] * 10 + [0.5] + [1.0] * 10
        topos, fundos = swings(h, l)
        self.assertIn(5.0, topos)
        self.assertIn(0.5, fundos)


class TestArredondamento(unittest.TestCase):
    def test_rd_sig_serve_para_preco_grande_e_pequeno(self):
        self.assertEqual(rd_sig(65417.123456), 65417.1)
        self.assertEqual(rd_sig(0.000012345678), 0.0000123457)
        self.assertIsNone(rd_sig(None))

    def test_datas_ida_e_volta(self):
        self.assertEqual(iso(ms("2026-11-25T15:00:00Z")), "2026-11-25T15:00:00Z")


class TestGerir(unittest.TestCase):
    """Plano LONG: entrada 100, stop 90 (1R = 10), alvo1 115 (1,5R), alvo2 130 (3R)."""
    p = Plano(1, 100.0, 90.0, 115.0, 130.0)

    def rodar(self, candles, vencido=nunca):
        h, l, c = zip(*candles)
        return gerir(self.p, h, l, c, 0, vencido)

    def test_stop(self):
        s = self.rodar([(105, 95, 100), (101, 89, 92)])
        self.assertEqual((s.situacao, s.r, s.indice), ("stop", -1.0, 1))

    def test_stop_e_alvo_no_mesmo_candle_conta_stop(self):
        self.assertEqual(self.rodar([(116, 89, 100)]).situacao, "stop")

    def test_alvo1_e_volta_na_entrada(self):
        s = self.rodar([(116, 101, 110), (112, 99, 100)])
        self.assertEqual(s.situacao, "alvo1")
        self.assertAlmostEqual(s.r, 0.75)

    def test_alvo2(self):
        s = self.rodar([(116, 101, 110), (131, 110, 129)])
        self.assertEqual(s.situacao, "alvo2")
        self.assertAlmostEqual(s.r, 2.25)

    def test_alvo1_e_alvo2_no_mesmo_candle(self):
        self.assertEqual(self.rodar([(131, 101, 125)]).situacao, "alvo2")

    def test_stop_vai_para_a_entrada_so_no_candle_seguinte(self):
        # no candle do alvo 1 a mínima foi a 99 (abaixo da entrada), mas o stop ainda era 90
        s = self.rodar([(116, 99, 110), (120, 105, 118)])
        self.assertEqual(s.situacao, "aberto")
        self.assertTrue(s.meia)

    def test_prazo_sai_pelo_fechamento(self):
        s = self.rodar([(105, 95, 102), (106, 96, 104)], vencido=lambda j: j == 1)
        self.assertEqual(s.situacao, "prazo")
        self.assertAlmostEqual(s.r, 0.4)

    def test_aberto_tem_resultado_flutuante(self):
        s = self.rodar([(105, 95, 103)])
        self.assertEqual(s.situacao, "aberto")
        self.assertAlmostEqual(s.r, 0.3)

    def test_short_espelhado(self):
        p = Plano(-1, 100.0, 110.0, 85.0, 70.0)
        h, l, c = (101, 84), (95, 69), (98, 72)
        s = gerir(p, h, l, c, 0, nunca)
        self.assertEqual(s.situacao, "alvo2")
        self.assertAlmostEqual(s.r, 2.25)


def sinal(**kw):
    base = {"id": "f1", "at": "2026-11-20T00:00:00Z", "ativo": "BTC", "direcao": "LONG", "tipo": "imediato",
            "entrada": 100, "stop": 90, "alvo1": 115, "alvo2": 130}
    base.update(kw)
    return base


def candles(*hlc, t0=T0):
    return {"t": [t0 + i * HORA for i in range(len(hlc))], "h": [x[0] for x in hlc],
            "l": [x[1] for x in hlc], "c": [x[2] for x in hlc]}


class TestAvaliarSinais(unittest.TestCase):
    def test_imediato_no_stop(self):
        r = av.avaliar(sinal(), candles((105, 95, 100), (101, 89, 92)), T0 + 5 * HORA)
        self.assertEqual(r["status"], "stop")
        self.assertEqual(r["resultado_r"], -1.0)
        self.assertEqual(r["encerrado_em"], iso(T0 + HORA))

    def test_candle_anterior_ao_sinal_nao_conta(self):
        k = candles((101, 80, 92), (105, 95, 100), t0=T0 - HORA)  # o stop foi tocado antes do sinal
        self.assertEqual(av.avaliar(sinal(), k, T0 + 2 * HORA)["status"], "aberto")

    def test_condicional_expira_em_48h(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125)
        k = candles(*[(105, 100, 102)] * 50)
        r = av.avaliar(s, k, T0 + 50 * HORA)
        self.assertEqual((r["status"], r["resultado_r"]), ("expirado", 0))

    def test_condicional_aguardando_antes_de_48h(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125)
        self.assertEqual(av.avaliar(s, candles((105, 100, 102)), T0 + 2 * HORA)["status"], "aguardando")

    def test_condicional_aciona_no_toque(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125)
        r = av.avaliar(s, candles((105, 100, 102), (99, 94, 96), (126, 96, 124)), T0 + 3 * HORA)
        self.assertEqual(r["status"], "alvo2")
        self.assertEqual(r["acionado_em"], iso(T0 + HORA))

    def test_prazo_de_7_dias(self):
        k = candles(*[(105, 95, 101)] * 200)
        r = av.avaliar(sinal(), k, T0 + 200 * HORA)
        self.assertEqual(r["status"], "encerrado no prazo")
        self.assertEqual(r["encerrado_em"], iso(T0 + 169 * HORA))
        self.assertAlmostEqual(r["resultado_r"], 0.1)

    def test_stop_igual_entrada_e_invalido(self):
        self.assertEqual(av.avaliar(sinal(stop=100), candles((1, 1, 1)), T0)["status"], "invalido")

    def test_encerrados_nao_sao_reavaliados(self):
        self.assertEqual(av.avaliar_todos([sinal(status="stop")], T0), {})


class TestSetup(unittest.TestCase):
    def tf(self, tendencia, rsi14=50.0, macd=(0.0, 0.0)):
        return {"tendencia": tendencia, "rsi14": rsi14, "macd_hist": macd[0], "macd_hist_ant": macd[1],
                "fechamento": 100.0, "atr14": 2.0, "ema20": 98.0, "suportes": [], "resistencias": []}

    def test_long_imediato_com_confluencia(self):
        st = at.setup({"1D": self.tf(3), "4H": self.tf(3, macd=(1.0, 0.5)), "1H": self.tf(1)}, {})
        self.assertEqual(st["direcao"], "LONG")
        self.assertEqual((st["entrada"], st["stop"], st["alvo1"], st["alvo2"]), (100.0, 97.0, 104.5, 109.0))

    def test_sobrecompra_vira_plano_condicional(self):
        st = at.setup({"1D": self.tf(3), "4H": self.tf(3, rsi14=80), "1H": self.tf(1)}, {})
        self.assertEqual(st["direcao"], "SEM SINAL")
        self.assertIn("RSI 4H sobrecomprado: esperar recuo", st["motivos"])
        self.assertEqual(st["plano_condicional"]["direcao"], "LONG")
        self.assertEqual(st["plano_condicional"]["entrada"], 98.0)

    def test_sem_direcao_sem_plano(self):
        st = at.setup({"1D": self.tf(1), "4H": self.tf(-1), "1H": self.tf(0)}, {})
        self.assertEqual(st["direcao"], "SEM SINAL")
        self.assertNotIn("plano_condicional", st)

    def test_dados_insuficientes(self):
        self.assertEqual(at.setup({"1D": None, "4H": None, "1H": None}, {})["motivo"], "dados insuficientes")


class TestBacktestETop10(unittest.TestCase):
    def test_tendencia_de_alta_limpa_da_lucro_no_long(self):
        c = [100 * 1.004 ** i for i in range(300)]
        k = {"h": [x * 1.01 for x in c], "l": [x * 0.99 for x in c], "c": c}
        bt = at.backtest(k, "LONG", "imediato")
        self.assertGreater(bt["n"], 0)
        self.assertGreater(bt["exp_r"], 0)
        self.assertEqual(at.backtest(k, "SHORT", "imediato")["n"], 0)

    def linha(self, ativo, vol, bt, pontuacao=-6):
        return {"ativo": ativo, "vol_usd_m": vol, "pontuacao": pontuacao, "t1d": -3, "t4h": -3, "t1h": -1,
                "rsi_4h": 40, "funding": 0.01, "long_short": 2.5, "bt": bt,
                "plano": {"direcao": "SHORT", "tipo": "imediato"}, "qualidade": None, "top10": None}

    def test_qualidade_fica_entre_0_e_100(self):
        for bt in ({}, {"n": 50, "acerto": 100, "exp_r": 5}, {"n": 50, "acerto": 0, "exp_r": -1}):
            q = at.qualidade(self.linha("X", 1000, bt), bt)
            self.assertTrue(0 <= q <= 100)

    def test_top10_exclui_pouco_volume_e_backtest_negativo(self):
        bom = {"n": 8, "acerto": 75, "exp_r": 1.0}
        linhas = [self.linha("FINA", 2, bom), self.linha("RUIM", 100, {"n": 5, "acerto": 20, "exp_r": -0.3})]
        linhas += [self.linha(f"M{i}", 10 + i, bom) for i in range(12)]
        linhas.append(dict(self.linha("SEMPLANO", 100, None), plano=None))
        elegiveis = at.marcar_top10(linhas)
        nomes = [l["ativo"] for l in elegiveis]
        self.assertNotIn("FINA", nomes)
        self.assertNotIn("RUIM", nomes)
        self.assertEqual(sum(1 for l in linhas if l["top10"]), 10)
        self.assertIsNone(linhas[-1]["qualidade"])
        self.assertEqual(elegiveis[0]["top10"], 1)


class TestCarteira(unittest.TestCase):
    def op(self, id_, ini, fim, r, status="alvo2", entrada=100, stop=90, **kw):
        s = sinal(id=id_, entrada=entrada, stop=stop, status=status, acionado_em=ini, resultado_r=r)
        if fim:
            s["encerrado_em"] = fim
        s.update(kw)
        return s

    def test_saldo_com_risco_de_1pct_e_taxas(self):
        sinais = [self.op("a", "2026-11-20T01:00:00Z", "2026-11-20T05:00:00Z", 2.25),
                  self.op("b", "2026-11-21T01:00:00Z", "2026-11-21T05:00:00Z", -1.0, status="stop")]
        doc = ct.carteira(sinais, ct.Regras())
        # a: arrisca 20, posição 200 (stop a 10%), taxas 0,20 -> +44,80
        # b: arrisca 20,448, posição 204,48, taxas 0,20448 -> -20,65248
        self.assertAlmostEqual(doc["saldo"], rd2(2000 + 44.8 - 20.448 - 0.20448))
        self.assertEqual((doc["operacoes"], doc["ganhos"], doc["perdas"]), (2, 1, 1))
        self.assertEqual(doc["acerto_pct"], 50.0)

    def test_posicao_limitada_pela_alavancagem(self):
        # stop a 0,1% da entrada: 1% de risco pediria posição de 10x o saldo; o limite é 5x
        sinais = [self.op("a", "2026-11-20T01:00:00Z", "2026-11-20T05:00:00Z", -1.0, status="stop", stop=99.9)]
        extrato = json.loads(ct.carteira(sinais, ct.Regras())["extrato_json"])
        self.assertAlmostEqual(extrato[0]["tamanho_usd"], 10000.0)
        self.assertAlmostEqual(extrato[0]["risco_usd"], 10.0, places=6)

    def test_expirado_e_nao_acionado_ficam_de_fora(self):
        sinais = [sinal(id="x", status="expirado", resultado_r=0), sinal(id="y", status="aguardando")]
        doc = ct.carteira(sinais, ct.Regras())
        self.assertEqual((doc["saldo"], doc["operacoes"], doc["abertas"]), (2000.0, 0, 0))

    def test_aberta_nao_mexe_no_saldo(self):
        sinais = [self.op("a", "2026-11-20T01:00:00Z", None, None, status="aberto", r_flutuante=0.5)]
        doc = ct.carteira(sinais, ct.Regras())
        self.assertEqual(doc["saldo"], 2000.0)
        self.assertEqual(doc["abertas"], 1)
        self.assertAlmostEqual(doc["flutuante_usd"], 10 - 0.1)

    def test_saida_antes_da_entrada_no_mesmo_horario(self):
        # "b" entra na mesma hora em que "a" sai: o risco de "b" usa o saldo já com o lucro de "a"
        sinais = [self.op("a", "2026-11-20T01:00:00Z", "2026-11-20T05:00:00Z", 2.25),
                  self.op("b", "2026-11-20T05:00:00Z", "2026-11-20T09:00:00Z", -1.0, status="stop")]
        extrato = json.loads(ct.carteira(sinais, ct.Regras())["extrato_json"])
        self.assertAlmostEqual(extrato[1]["risco_usd"], rd2(2044.8 * 0.01))


class TestCarregarSinais(unittest.TestCase):
    def test_aceita_formato_do_artifactdata(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"documents": [{"id": "f1", "data": {"ativo": "BTC"}}]}, f)
        try:
            self.assertEqual(carregar_sinais(f.name), [{"ativo": "BTC", "id": "f1"}])
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
