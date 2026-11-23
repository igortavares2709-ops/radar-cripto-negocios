# Mesa de decisão dos sinais de futuros

A mesa de decisão é a última etapa antes de um sinal LONG ou SHORT ser publicado. Ela revisa cada sinal candidato e decide se ele faz sentido. Só um sinal **APROVADO** pela mesa vai ao painel e ao relatório como operação. Um sinal **VETADO** vira "AGUARDAR", com o motivo escrito.

A mesa raciocina com os princípios que grandes traders de futuros e macro **ensinam publicamente**, em livros, entrevistas e palestras. Ela não fala em nome dessas pessoas nem garante o resultado que elas têm. Os princípios valem como uma disciplina de decisão.

## Os princípios usados

| Inspiração | Princípio aplicado pela mesa |
|---|---|
| Paul Tudor Jones | Defesa primeiro: pensar onde está o erro antes de pensar no lucro. Preferir operações em que o ganho possível é bem maior que a perda (5:1 como ideal). Respeitar a média de 200 dias: abaixo dela, não comprar; acima dela, não vender. |
| Stanley Druckenmiller | O que move o mercado é liquidez e banco central. Não operar contra o Fed. Só aumentar a aposta quando a convicção é alta; sem clareza, preservar capital. |
| Peter Brandt | Operar só os gráficos mais limpos. A maioria das operações dá prejuízo: o lucro vem de poucas operações boas com perdas pequenas. O stop tem que ficar onde a ideia se prova errada, não num número redondo. |
| Linda Raschke | Primeiro o contexto (tendência, volatilidade, regime), depois a entrada. A melhor entrada é a favor da tendência, num recuo. Mercado sem direção é para ficar de fora. |
| Ed Seykota e os Turtles (Richard Dennis) | Seguir a tendência, cortar as perdas rápido, deixar os lucros correrem. Não tentar adivinhar topo nem fundo. |
| Mark Douglas | Pensar em probabilidades: um sinal isolado não importa, o que importa é seguir as mesmas regras em todos. |

## Vetos automáticos

Basta um destes para o sinal ser **VETADO**:

1. **Contra a tendência principal:** LONG com o preço abaixo da EMA200 diária, ou SHORT com o preço acima dela.
2. **Correndo atrás do preço:** SHORT com o RSI diário abaixo de 25, ou LONG com o RSI diário acima de 75, quando o sinal é imediato. Sinal condicional, que espera repique ou recuo, pode passar.
3. **Risco/retorno ruim:** risco/retorno até o alvo 2 abaixo de 1:2,5, ou até o alvo 1 abaixo de 1:1,5.
4. **Evento de alto impacto nas próximas 12 horas** (decisão ou ata do Fed, CPI, payroll, resultado de uma empresa que mexe com o mercado), quando o sinal é imediato.
5. **Stop mal colocado:** stop a menos de 1 ATR do 4H da entrada, que o ruído normal derruba, ou sem um suporte ou resistência que justifique o lugar.
6. **Notícia contra:** fluxo de ETFs, regulação ou hack recente indo forte contra a direção do sinal.
7. **Placar ruim:** se os 3 últimos sinais encerrados deram stop, só passam sinais com nota 8 ou mais.

## Nota da mesa (0 a 10)

Some 1 ponto por critério atendido. Só é **APROVADO** o sinal com **nota 7 ou mais** e nenhum veto.

1. Tendência a favor nos três tempos gráficos (diário, 4H, 1H).
2. Preço do lado certo da EMA200 diária.
3. Entrada num recuo ou repique até a EMA20 do 4H ou até um suporte/resistência, e não no meio do movimento.
4. Stop num ponto técnico claro, onde a ideia fica errada.
5. Risco/retorno de pelo menos 1:3 até o alvo 2.
6. Momento confirmando: MACD do 4H a favor e ganhando força.
7. Posicionamento a favor: funding e relação de contas long/short mostram a multidão do lado errado (ex.: varejo muito comprado num SHORT).
8. Volume confirmando o movimento (volume acima da média de 50 candles a favor da direção).
9. Macro a favor: liquidez, Fed e fluxo de ETFs não vão contra a operação.
10. Gráfico limpo: sem sinais em conflito entre os indicadores.

## O que a mesa grava em cada sinal

- `mesa_decisao`: "APROVADO" ou "VETADO".
- `mesa_nota`: a nota, de 0 a 10.
- `mesa_parecer`: de 2 a 4 frases com o raciocínio, citando quais princípios pesaram. Num veto, diz qual veto e o que precisaria mudar para aprovar.
- Sinal vetado é gravado como `direcao: "AGUARDAR"`, `tipo: "sem sinal"`, com os níveis de entrada, stop e alvos guardados no `mesa_parecer` para referência.

## Tamanho de posição recomendado

Mesmo aprovado, a mesa sugere arriscar no máximo:
- **1% do capital** com nota 7 ou 8;
- **1,5% do capital** com nota 9 ou 10.

O "risco" é quanto se perde se o stop for atingido. Alavancagem máxima: 5x, nunca mais.
