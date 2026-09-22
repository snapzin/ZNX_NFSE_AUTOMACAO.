# Correção dos relatórios — 21/09/2026

## Causa e ajuste

A busca global de `vBC` confundia a base de IBS/CBS com a base de ISS quando
esta última estava ausente. ISS e totais agora são lidos de
`NFSe/infNFSe/valores`, sem buscar valores em `IBSCBS`.

Nas emitidas, os cabeçalhos distinguem PIS/COFINS de apuração própria das
contribuições sociais retidas agrupadas em `vRetCSLL`. Foi acrescentada a
descrição de `tpRetPisCofins`, sem mudar as posições das colunas monetárias.
Não se somam os débitos próprios novamente às retenções, nem se tenta repartir
o agregado em parcelas que o XML não discrimina.

Referência: [NT 007/2026, páginas 3–5](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/nt-007-se-cgnfse-v1-0.pdf).

O indicador de ISS continua a seguir o XML: 1 = Não; 2 ou 3 = Sim.
Não foram recalculados valores oficiais de imposto, retenção ou líquido.

## Conferência da amostra local

- 35 relatórios regenerados em pasta nova, preservando os originais.
- 1.196 XMLs: 238 emitidas e 958 recebidas.
- Base de ISS corrigida em 7 emitidas e 6 recebidas.
- Nenhuma outra alteração nos valores comparados das 28 colunas originais.
- A conferência compara base de ISS, valor de ISS, retenções, líquido e
  indicador de retenção diretamente com os campos correspondentes do XML.

Essa verificação não comprova que todas as notas das caixas ADN foram baixadas.
O utilitário não consulta o portal e não gera um consolidado de completude.
Os 40 casos com divergência aritmética encontrados anteriormente nos próprios
XMLs permanecem com os valores da fonte; não se inventou um líquido alternativo.

A amostra não contém a nota exata da captura enviada. Um teste com dados
fictícios reproduz os valores exibidos: PIS 9,18; COFINS 42,36; IRRF 21,18;
contribuições sociais retidas 65,66; CP 0,00. Total retido 86,84, sem duplicar
PIS/COFINS e sem usar os totais aproximados de tributos como retenções.

## Como verificar

```powershell
python -B -m unittest discover -s tests -v
npm.cmd --prefix electron test
python -B tools/regerar_relatorios_locais.py CAMINHO_ORIGEM CAMINHO_DESTINO_NOVO
```

O destino precisa ser novo e estar fora da origem. O utilitário copia somente
os XMLs, gera as planilhas e grava o resultado da comparação em `conferencia.json`.
Certificados, senhas e XMLs reais não devem ser adicionados ao Git.
