# Radar Cripto & Negócios

Uma equipe de 3 agentes de IA (Claude) que roda sozinha **a cada 4 horas**, 24h por dia. Ela analisa o mercado de criptoativos e todas as moedas com futuros na OKX, publica sinais só entre as 10 melhores oportunidades, acompanha uma **carteira fictícia de US$ 2.000** que segue esses sinais e procura oportunidades de renda digital. Grava tudo num painel e envia um relatório por e-mail e push.

> Isto é análise automatizada, **não é recomendação de investimento**. Futuros com alavancagem podem zerar a margem.

## A equipe

| Agente | O que faz |
|---|---|
| Analista de mercado cripto | Preços, variações, fluxo dos ETFs, liquidações, moedas fora do normal e agenda macro. Também aponta os riscos do momento e define o nível: calmo, atenção ou alerta |
| Analista técnico de futuros | Decide sozinho os sinais, escolhendo só entre as moedas do **Top 10** do radar. Análise gráfica em 3 tempos (1D, 4H, 1H) com EMAs 20/50/200, RSI, MACD, ATR, suportes e resistências, funding, open interest e long/short. Publica LONG/SHORT, condicionais ou AGUARDAR, com entrada, stop, alvos, R:R e alavancagem máxima |
| Caçador de renda digital | Novas oportunidades de renda na internet que dá para começar a partir do Brasil, com custo, prazo, primeiros passos e a nota de risco dele |

## Como uma rodada funciona

1. **Memória:** lê as rodadas anteriores no painel, para comparar o que mudou e não repetir ideias.
2. **Dados:** CoinGecko (preços, dominância), alternative.me (Medo e Ganância), DeFiLlama (TVL) e OKX (candles e derivativos dos perpétuos).
3. **Radar e Top 10:** roda [`scripts/analise_tecnica.py`](scripts/analise_tecnica.py) em todas as moedas e separa as 10 melhores oportunidades (veja abaixo).
4. **Conferência:** roda [`scripts/avaliar_sinais.py`](scripts/avaliar_sinais.py), que confere cada sinal anterior contra os candles de 1h e grava a situação e o resultado.
5. **Carteira fictícia:** roda [`scripts/carteira.py`](scripts/carteira.py), que aplica todos os sinais numa conta de US$ 2.000 e grava o saldo.
6. **Notícias:** só de fontes fortes. Cripto: CoinDesk, The Block, Decrypt, Bloomberg, CNBC, SEC, Farside. Brasil: Valor, InfoMoney, Banco Central, gov.br, NeoFeed, Brazil Journal. Renda digital: TechCrunch, The Verge, Reuters, Bloomberg, Exame, Sebrae e os blogs oficiais das plataformas. Cada notícia vai com a fonte e a data.
7. **Grava** no painel e **envia o relatório** por e-mail e push, com o saldo da carteira no assunto.

O passo a passo completo que cada rodada segue está em [`agentes/rodada.md`](agentes/rodada.md).

## Top 10: as melhores oportunidades

Toda moeda com plano (imediato ou condicional) ganha uma **nota de qualidade de 0 a 100**, que estima a chance de a operação dar lucro:

| Parte | Peso | O que mede |
|---|---|---|
| Backtest | 50 | O mesmo plano testado nos candles 4H da própria moeda (~50 dias): rendimento médio em R (35) e % de operações com lucro (15). Com poucas operações no histórico, o peso cai. |
| Confluência | 20 | Pontuação do setup e quantos tempos gráficos (1D, 4H, 1H) apontam para o mesmo lado |
| Entrada | 10 | RSI do 4H: entrar depois que o preço já esticou perde pontos |
| Liquidez | 10 | De US$ 5 milhões (0) a US$ 500 milhões (cheio) negociados por dia |
| Posicionamento | 10 | Funding e contas long/short com a multidão do outro lado |

As 10 maiores notas, com mais de US$ 5 milhões por dia e sem backtest negativo, formam o **Top 10**. O analista técnico só publica sinais entre elas, de 0 a 4 por rodada.

O backtest usa o regime atual da moeda e janela curta, então tende a ser otimista: serve para ordenar as moedas, não para prometer a taxa de acerto. O resultado real fica no placar e na carteira.

A antiga mesa de decisão (regras em [`agentes/mesa_de_decisao.md`](agentes/mesa_de_decisao.md)) não é mais usada. Os sinais de 23 e 24/11 ainda mostram o parecer dela.

## Carteira fictícia de US$ 2.000

[`scripts/carteira.py`](scripts/carteira.py) aplica todos os sinais LONG/SHORT numa conta de mentira que começou com US$ 2.000, a partir do primeiro sinal (17/11):

- Cada operação arrisca **1% do saldo** do momento em que foi acionada. Resultado = valor arriscado × resultado em R.
- Tamanho da posição = valor arriscado ÷ distância até o stop, limitado a 5x o saldo.
- **Taxas** de 0,05% do tamanho da posição na entrada e na saída.
- Sinais que expiraram sem acionar não entram. Operações abertas aparecem com o resultado flutuante, mas só mudam o saldo quando encerram.

O painel mostra o saldo, o resultado, a taxa de acerto, a maior queda, a curva do saldo e o extrato. É simulação: não cobra derrapagem de preço nem funding.

O documento da carteira também guarda o **placar completo** de todos os sinais, inclusive os arquivados. O painel usa esse placar e o extrato para a curva de R, porque só lê os 1.000 registros mais antigos de cada coleção.

## Trades ao vivo

[`scripts/monitor.py`](scripts/monitor.py) acompanha cada trade em andamento. Ele roda **a cada 2 horas** (aos 29 minutos das horas ímpares, UTC) numa tarefa própria, leve, sem notícias nem análise, e também dentro de cada rodada completa. Em cada execução ele:

- confere os sinais (com o `avaliar_sinais.py`) e atualiza a carteira (com o `carteira.py`);
- pega o último preço de cada moeda na OKX e monta o quadro de cada trade: resultado flutuante em R e em US$, folga até o stop, quanto falta para o alvo, o melhor e o pior momento desde a entrada (MFE/MAE) e quando vence o prazo;
- grava o quadro em `data/radar/ao_vivo/atual` e, quando um trade é acionado, bate um alvo, bate o stop ou vence, manda um **aviso no celular**.

No painel, a aba **Sinais abertos** mostra um resumo (resultado em aberto, saldo contando as abertas, quantos estão no lucro, o mais perto do stop e do alvo) e esses cartões, do mais perto do stop ao mais longe, com a régua de preço (stop, entrada, alvos e o preço agora) e os últimos avisos. Com a página aberta, o preço vem **ao vivo do conector CoinGecko** e se atualiza sozinho a cada 30 segundos: o resultado em R e em US$, as distâncias até o stop e o alvo e o melhor e o pior momento são refeitos na hora, com as mesmas contas do monitor. O monitor grava o código do CoinGecko de cada moeda (`cg_id`, achado pela API pública pelo símbolo, ficando com a de maior valor de mercado). O preço do CoinGecko é a média do mercado à vista e pode diferir um pouco do perpétuo da OKX; quem decide oficialmente se bateu o stop ou o alvo é a conferência do monitor, pelo preço da OKX. Sem o conector, a aba mostra o preço da última conferência. A Visão geral também lista os sinais abertos, com uma barra de onde o preço está entre o stop e o alvo.

## Execução no testnet da Binance (dinheiro de mentira)

[`scripts/executor_binance.py`](scripts/executor_binance.py) replica os sinais numa **conta de teste** da Binance Futures, junto com o monitor (a cada 2 horas). Os servidores dos agentes não conseguem operar na Binance real (ela responde "localização restrita", HTTP 451), e o executor recusa qualquer endereço que não seja do testnet.

- Abre os sinais LONG/SHORT publicados nas últimas 6 horas: entrada a mercado (imediato) ou limitada (condicional), com o stop e o alvo 2 já na corretora, 1% do saldo de risco e alavancagem até a do sinal (máx. 5x).
- Administra os abertos: metade sai no alvo 1 e o stop vai para a entrada; recoloca um stop que sumiu; cancela a entrada condicional que não executou em 48h; fecha pelo preço depois de 7 dias.
- Não guarda estado: cada ordem tem o código `rd-<id do sinal>-<papel>` e o que já foi feito é lido da corretora.
- Travas: até 4 trades novos por execução e 8 abertos; não abre novos se a perda do dia passar de 3%; `--pausado` só administra; `--simular` só mostra o plano.
- Chaves: variáveis de ambiente `BINANCE_TESTNET_API_KEY` e `BINANCE_TESTNET_API_SECRET` (conta em testnet.binancefuture.com). Sem elas, roda em simulação. **Nunca use ali chaves da conta real.**
- O testnet tem preços próprios, que podem se afastar do mercado real: ele valida a mecânica das ordens, não o resultado. O painel mostra a execução na aba Sinais abertos.

## Arquivamento (para o painel não travar com o tempo)

O painel lê no máximo 1.000 registros por coleção, começando pelos mais antigos. A cada rodada, os registros velhos vão para coleções de arquivo, que o painel não lê:

| Coleção | Vai para o arquivo depois de | Arquivo |
|---|---|---|
| `data/radar/futuros` (AGUARDAR) | 3 dias | `data/radar/arquivo_futuros` |
| `data/radar/futuros` (LONG/SHORT encerrados ou expirados) | 30 dias | `data/radar/arquivo_futuros` |
| `data/radar/sinais` | 10 dias | `data/radar/arquivo_sinais` |
| `data/radar/briefings` | 60 dias | `data/radar/arquivo_briefings` |
| `data/radar/renda` | 60 dias | `data/radar/arquivo_renda` |

Os sinais LONG/SHORT arquivados continuam entrando na conferência, no placar e na carteira. Para o banco do painel não lotar (limite de 25.000 registros), os AGUARDAR arquivados há mais de 30 dias e as notas de mercado arquivadas há mais de 90 dias são apagadas: o conteúdo delas já está no relatório de cada rodada.

## Regras do analista técnico

- LONG ou SHORT imediato só com confluência: a tendência no diário e no 4H tem que concordar.
- Nunca compra em sobrecompra extrema nem vende em sobrevenda extrema (RSI diário acima de 75 ou abaixo de 25). Nesses casos publica um **plano condicional**: operar no repique ou no recuo até a EMA20 do 4H.
- Stop sempre definido (1,5 × ATR do 4H, ou além do suporte/resistência mais próximo). Alvos de 1,5R e 3R.
- Alavancagem máxima calculada para perder no máximo cerca de 2% da margem se o stop for atingido, limitada a 5x.
- Sem confluência, a resposta é **AGUARDAR**.
- Só sinaliza moedas do Top 10, e cita na tese a posição, a nota e o backtest.
- A cada rodada, confere se os sinais anteriores bateram no alvo, no stop ou se o gatilho não aconteceu (veja abaixo).

## Histórico e placar dos sinais

Todo sinal LONG ou SHORT fica guardado no painel, na seção **Histórico e desempenho dos sinais**, junto com a situação e o resultado. Quem confere é o [`scripts/avaliar_sinais.py`](scripts/avaliar_sinais.py), sempre com as mesmas regras:

- **Imediato:** a operação começa na hora do sinal, no preço de entrada.
- **Condicional:** começa quando o preço toca a entrada. Se não tocar em 48 horas, o sinal **expira** e não conta no placar.
- **Gestão:** no alvo 1 sai metade e o stop da outra metade vai para a entrada. A outra metade sai no alvo 2 ou na entrada.
- **Stop e alvo no mesmo candle de 1h:** conta o stop (critério conservador).
- **Operação aberta há mais de 7 dias:** é encerrada pelo último preço.
- **Resultado em R:** 1R é a distância entre a entrada e o stop. Stop = -1R. Alvo 1 e volta à entrada = metade do R do alvo 1. Alvo 2 = média do R dos dois alvos.

Situações possíveis: `aguardando`, `aberto`, `alvo1 (aberto)`, `alvo1`, `alvo2`, `stop`, `expirado`, `encerrado no prazo`.

O placar mostra os sinais publicados, os acionados, os encerrados, a taxa de acerto dos encerrados e o resultado acumulado em R.

Limitação: o gatilho do condicional conta só o toque no preço, sem exigir o candle de rejeição descrito no sinal. O placar pode incluir operações que um trader mais exigente não teria aberto.

## Rodar a análise técnica localmente

Só algumas moedas:

```bash
python3 scripts/analise_tecnica.py BTC ETH SOL
```

Radar de todas as criptos com perpétuo USDT na OKX e mais de US$ 1 milhão negociado em 24h (sem ações, commodities e stablecoins; leva uns 3 minutos):

```bash
python3 scripts/analise_tecnica.py --todas --saida ta.json --scanner scanner.json > resumo.json
```

`resumo.json` traz o Top 10 (com a nota de qualidade, o backtest e o detalhe de cada moeda) e as melhores de fora do Top 10; `scanner.json` é a tabela que o painel mostra na aba Radar.

Para conferir sinais, salve-os numa lista JSON (campos `id`, `at`, `ativo`, `direcao`, `tipo`, `entrada`, `stop`, `alvo1`, `alvo2`) e rode:

```bash
python3 scripts/avaliar_sinais.py sinais.json
```

Para a carteira, junte o resultado da conferência a cada sinal (campos `status`, `resultado_r`, `r_flutuante`, `acionado_em`, `encerrado_em`) e rode:

```bash
python3 scripts/carteira.py sinais_conferidos.json --inicial 2000 --risco 1 --taxa 0.05
```

Usa só a biblioteca padrão do Python 3 e dados públicos da OKX (sem chave de API). A saída é JSON com os indicadores por tempo gráfico, os derivativos e o setup sugerido.

## Organização do código

| Arquivo | O que faz |
|---|---|
| [`scripts/analise_tecnica.py`](scripts/analise_tecnica.py) | Radar: candles e derivativos da OKX, setup, backtest, nota de qualidade e Top 10 |
| [`scripts/avaliar_sinais.py`](scripts/avaliar_sinais.py) | Conferência dos sinais publicados contra os candles de 1h |
| [`scripts/carteira.py`](scripts/carteira.py) | Carteira fictícia de US$ 2.000 |
| [`scripts/executor_binance.py`](scripts/executor_binance.py) | Replica os sinais numa conta de teste da Binance Futures (só testnet), com travas de risco |
| [`scripts/monitor.py`](scripts/monitor.py) | Trades ao vivo: conferência + carteira + quadro de cada trade em andamento e os avisos |
| [`scripts/indicadores.py`](scripts/indicadores.py) | EMA, RSI, ATR e topos/fundos (funções puras, sem rede) |
| [`scripts/radar_comum.py`](scripts/radar_comum.py) | Cliente da OKX com limite de requisições, datas, leitura dos sinais e a gestão de uma operação (stop, alvos e prazo), usada igual pela conferência e pelo backtest |
| [`tests/test_regras.py`](tests/test_regras.py) | Testes das regras, sem rede |

Os parâmetros (pontuação mínima, múltiplos de ATR, limites de RSI, pesos da nota, prazos) ficam como constantes nomeadas no topo de cada script.

Para rodar os testes:

```bash
python3 -m unittest discover -s tests -v
```

## Painel

[`painel/index.html`](painel/index.html) é a página do painel. Ela roda dentro de um Artifact do claude.ai e lê os dados destas coleções:

- `data/radar/briefings`: um relatório por rodada
- `data/radar/sinais`: sinais de mercado
- `data/radar/futuros`: sinais de futuros, com `status`, `resultado_r`, `acionado_em` e `encerrado_em` preenchidos pela conferência
- `data/radar/renda`: oportunidades de renda digital
- `data/radar/scanner/atual`: a tabela do radar, com a nota de qualidade e o Top 10
- `data/radar/carteira/atual`: a carteira fictícia (saldo, curva, extrato, operações abertas e placar completo)
- `data/radar/ao_vivo/atual`: o quadro ao vivo dos trades em andamento e os últimos avisos
- `data/radar/ideias`: o antigo banco de ideias de negócio (só histórico)

## Limitações honestas

- Análise técnica não prevê o futuro: indicadores atrasam e falham em notícias inesperadas.
- O analista técnico é um modelo de IA com regras disciplinadas, não um trader humano. Acompanhe o histórico de acertos antes de confiar.
- Binance e Bybit bloqueiam a região dos servidores, por isso os dados de futuros vêm da OKX.
