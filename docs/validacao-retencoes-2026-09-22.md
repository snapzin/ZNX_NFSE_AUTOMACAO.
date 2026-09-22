# Retenções nas recebidas — 22/09/2026

Esta verificação complementa a de 21/09. Copiar `vTotalRet` do XML sem
conferência não era suficiente: na amostra existem totais que incluem ISS
explicitamente não retido, apesar de o líquido não descontar esse imposto.

## Comportamento corrigido

- Emitidas, recebidas e canceladas distinguem PIS/COFINS de apuração própria
  das contribuições sociais retidas. `vRetCSLL` é apresentado como agregado,
  sem inventar a distribuição individual entre PIS, COFINS e CSLL.
- As posições das primeiras 28 colunas são preservadas. Acrescentam-se a
  descrição das contribuições, o valor do ISS efetivamente retido, o total
  original do XML e o resultado da conferência.
- O total só é ajustado automaticamente quando `tpRetISSQN=1`, o ISS é
  positivo, o total original é exatamente retenções federais + ISS e o
  líquido confirma serviço - descontos - retenções federais. A comparação
  usa Decimal. Não se alteram o XML, o imposto devido ou o líquido informado.
- Indicadores desconhecidos, dados inválidos, domínio legado ambíguo ou
  outras divergências são sinalizados como **CONFERIR**, sem estimar impostos.
- O resumo mostra quantidades de notas ajustadas e pendentes; o consolidado
  também mostra pendências fiscais, separadas da completude do download.

Referências: [NT 007/2026, seção PIS/COFINS](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/nt-007-se-cgnfse-v1-0.pdf)
e [XSD de produção, TCValoresNFSe/vTotalRet e vLiq](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/nfse-esquemas_xsd-v1-01-20260209.zip).

## Casos reproduzidos sem dados identificadores

| Caso | Retenções conferidas | Tratamento |
| --- | ---: | --- |
| Serviço 1.412,00, IRRF 21,18, contribuições sociais 65,66, ISS não retido | 86,84 | PIS 9,18 e COFINS 42,36 próprios não são somados novamente |
| Serviço 563,52, ISS 28,17 não retido, sem retenções federais, líquido 563,52 | 0,00 | Exclui ISS indevido do total e mantém 28,17 na coluna original XML |
| ISS 28,17 retido pelo tomador ou intermediário | 28,17 | Preserva a retenção |
| Dados contraditórios ou insuficientes | Sem ajuste automático | Marca CONFERIR |

As duas notas exatas das imagens não estão na amostra local; esses casos
foram reproduzidos por testes com XMLs fictícios. A homologação das notas
específicas depende de receber seus XMLs.

## Amostra real e limites

Nova conferência independente em 1.196 XMLs (238 emitidas, 958 recebidas),
regenerando 35 relatórios sem alterar a origem:

- 9 recebidas tiveram o ISS não retido excluído do total.
- 46 recebidas sinalizadas para conferência: 43 divergências de total/líquido
  e 3 com domínio legado/ambíguo. Valores preservados, não aprovados como corretos.
- As 13 correções anteriores de base de ISS continuam aplicadas.
- Não houve outras alterações de valores nas 28 colunas originais.

Não houve nova consulta remota com certificados, validação de todas as caixas
ADN, construção de instalador nem publicação de release nesta etapa.

## Reexecutar

```powershell
python -B -m unittest discover -s tests -v
npm.cmd --prefix electron test
python -B tools/regerar_relatorios_locais.py ORIGEM DESTINO_NOVO
```

O utilitário confere diretamente campos do XML contra o Excel e registra
contagens em `conferencia.json`. O destino precisa ser novo e fora da origem.
