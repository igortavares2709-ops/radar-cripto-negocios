# Radar Cripto & Negócios

Uma equipe de 4 agentes de IA (Claude) que roda sozinha **a cada 4 horas**, 24h por dia. Ela analisa o mercado de criptoativos e as novas ideias de negócio pelo mundo, grava tudo num painel e envia um relatório por e-mail e push.

> Isto é análise automatizada, **não é recomendação de investimento**. Futuros com alavancagem podem zerar a margem.

## A equipe

| Agente | O que faz |
|---|---|
| Analista de mercado cripto | Preços, variações, fluxo dos ETFs, liquidações, moedas fora do normal, agenda macro |
| Caçador de negócios | Rodadas de investimento e tendências (IA, DeFi, tokenização, pagamentos, stablecoins) e como fazer no Brasil |
| Analista técnico de futuros | Análise gráfica em 3 tempos (1D, 4H, 1H) com EMAs 20/50/200, RSI, MACD, ATR, Bollinger, suportes e resistências, funding, open interest e long/short. Publica sinais LONG/SHORT, condicionais ou AGUARDAR, com entrada, stop, alvos, R:R e alavancagem máxima |
| Gestor de risco / cético | Revisa os outros três, pode vetar sinais, dá nota de risco às ideias e define o nível: calmo, atenção ou alerta |

## Como uma rodada funciona

1. **Memória:** lê as rodadas anteriores no painel, para comparar o que mudou e não repetir ideias.
2. **Dados:** CoinGecko (preços, dominância), alternative.me (Medo e Ganância), DeFiLlama (TVL) e OKX (candles e derivativos dos perpétuos).
3. **Gráficos:** roda [`scripts/analise_tecnica.py`](scripts/analise_tecnica.py).
4. **Notícias:** só de fontes fortes. Cripto: CoinDesk, The Block, Decrypt, Bloomberg, CNBC, SEC, Farside. Startups: TechCrunch, Crunchbase News, Bloomberg, The Information. Brasil: Valor, InfoMoney, Banco Central, gov.br, NeoFeed, Brazil Journal. Cada notícia vai com a fonte e a data.
5. **Grava** no painel e **envia o relatório** por e-mail e push.

O passo a passo completo que cada rodada segue está em [`agentes/rodada.md`](agentes/rodada.md).

## Regras do analista técnico

- LONG ou SHORT imediato só com confluência: a tendência no diário e no 4H tem que concordar.
- Nunca compra em sobrecompra extrema nem vende em sobrevenda extrema (RSI diário acima de 75 ou abaixo de 25). Nesses casos publica um **plano condicional**: operar no repique ou no recuo até a EMA20 do 4H.
- Stop sempre definido (1,5 × ATR do 4H, ou além do suporte/resistência mais próximo). Alvos de 1,5R e 3R.
- Alavancagem máxima calculada para perder no máximo cerca de 2% da margem se o stop for atingido, limitada a 5x.
- Sem confluência, a resposta é **AGUARDAR**.
- A cada rodada, confere se os sinais anteriores bateram no alvo, no stop ou se o gatilho não aconteceu.

## Rodar a análise técnica localmente

```bash
python3 scripts/analise_tecnica.py BTC ETH SOL
```

Usa só a biblioteca padrão do Python 3 e dados públicos da OKX (sem chave de API). A saída é JSON com os indicadores por tempo gráfico, os derivativos e o setup sugerido.

## Painel

[`painel/index.html`](painel/index.html) é a página do painel. Ela roda dentro de um Artifact do claude.ai e lê os dados de quatro coleções:

- `data/radar/briefings`: um relatório por rodada
- `data/radar/sinais`: sinais de mercado
- `data/radar/ideias`: banco de ideias de negócio
- `data/radar/futuros`: sinais de futuros

## Limitações honestas

- Análise técnica não prevê o futuro: indicadores atrasam e falham em notícias inesperadas.
- O analista técnico é um modelo de IA com regras disciplinadas, não um trader humano. Acompanhe o histórico de acertos antes de confiar.
- Binance e Bybit bloqueiam a região dos servidores, por isso os dados de futuros vêm da OKX.
