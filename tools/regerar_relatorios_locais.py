"""Regenera uma copia dos relatorios existentes sem consultar ADN ou alterar a origem.

Uso: python tools/regerar_relatorios_locais.py ORIGEM DESTINO_NOVO
Confere os valores emitidos no Excel contra os XMLs locais e compara a versao antiga.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
import logging
from pathlib import Path
import shutil
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import load_workbook
from app.reports.relatorio import gerar_excel_cliente


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origem', type=Path)
    parser.add_argument('destino', type=Path)
    args = parser.parse_args()
    origem, destino = args.origem.resolve(), args.destino.resolve()
    if not origem.is_dir() or destino.exists() or destino.is_relative_to(origem):
        parser.error('Informe uma origem existente e um destino novo fora da origem.')
    destino.mkdir(parents=True)
    logging.disable(logging.CRITICAL)
    totais, bases_corrigidas, alteracoes = Counter(), Counter(), []
    retencoes_ajustadas, retencoes_a_conferir = Counter(), Counter()
    resumos = []
    ns = {'n': 'http://www.sped.fazenda.gov.br/nfse'}
    for cliente in sorted(p for p in origem.iterdir() if p.is_dir()):
        copia = destino / cliente.name
        copiados = []
        for tipo in ('Emitidas', 'Recebidas', 'Canceladas'):
            for xml in sorted((cliente / tipo).glob('*.xml')):
                alvo = copia / tipo / xml.name
                alvo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(xml, alvo)
                if not xml.name.lower().startswith('cancelamento'):
                    copiados.append((tipo, alvo))
        if not copiados:
            continue
        resumo = gerar_excel_cliente(copia, cliente.name)
        resumos.append(resumo)
        # O gerador sanitiza e limita o nome do cliente no arquivo final.
        novo = load_workbook(next(copia.glob('_Relatorio_*.xlsx')), data_only=True)
        anteriores = list(cliente.glob('_Relatorio_*.xlsx'))
        antigo = load_workbook(anteriores[0], data_only=True) if len(anteriores) == 1 else None
        try:
            for tipo in ('Emitidas', 'Recebidas', 'Canceladas'):
                if tipo not in novo:
                    continue
                ws = novo[tipo]
                headers = [c.value for c in ws[1]]
                linhas_antigas = {}
                if antigo is not None and tipo in antigo:
                    for row in antigo[tipo].iter_rows(min_row=2, max_row=antigo[tipo].max_row - 1, values_only=True):
                        linhas_antigas[row[27]] = row
                for row in ws.iter_rows(min_row=2, max_row=ws.max_row - 1, values_only=True):
                    arquivo = row[headers.index('Arquivo')]
                    root = ET.fromstring((copia / tipo / arquivo).read_bytes())
                    valores = root.find('n:infNFSe/n:valores', ns)
                    assert valores is not None, 'A auditoria requer XML nacional com infNFSe/valores'
                    for tag, coluna in (('vBC', 'Base Calculo'), ('vISSQN', 'Valor ISS'), ('vTotalRet', 'Total Retencoes XML'), ('vLiq', 'Valor Liquido')):
                        esperado = float(valores.findtext('n:' + tag, default='0', namespaces=ns))
                        assert abs((row[headers.index(coluna)] or 0) - esperado) < 0.001, (cliente.name, tipo, arquivo, tag)
                    codigo = root.findtext('n:infNFSe/n:DPS/n:infDPS/n:valores/n:trib/n:tribMun/n:tpRetISSQN', namespaces=ns)
                    assert row[headers.index('ISS Retido')] == {'1': 'Não', '2': 'Sim', '3': 'Sim'}[codigo]
                    # Conferencia independente: compara a identidade monetaria do
                    # XML, sem chamar o calculo de retencoes do gerador.
                    dps = root.find('n:infNFSe/n:DPS/n:infDPS/n:valores', ns)
                    def num(bloco, caminho):
                        return Decimal(bloco.findtext(caminho, default='0', namespaces=ns))
                    fed_path = 'n:trib/n:tribFed/'
                    fed = sum(num(dps, fed_path + 'n:' + tag) for tag in ('vRetIRRF', 'vRetCSLL', 'vRetCP'))
                    original = num(valores, 'n:vTotalRet')
                    iss = num(valores, 'n:vISSQN')
                    liq = num(valores, 'n:vLiq')
                    serv = num(dps, 'n:vServPrest/n:vServ')
                    descontos = sum(num(dps, 'n:vDescCondIncond/n:' + tag) for tag in ('vDescCond', 'vDescIncond'))
                    sociais = dps.findtext(fed_path + 'n:piscofins/n:tpRetPisCofins', default='', namespaces=ns)
                    proprio = sum(num(dps, fed_path + 'n:piscofins/n:' + tag) for tag in ('vPis', 'vCofins'))
                    ambiguo = (sociais == '1' and proprio > 0) or sociais not in {'', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'}
                    ambiguo = ambiguo or (sociais == '0' and num(dps, fed_path + 'n:vRetCSLL') > 0)
                    ajustar = codigo == '1' and iss > 0 and original == fed + iss and liq == serv - descontos - fed and not ambiguo
                    esperado_total = fed if ajustar else original
                    assert abs(Decimal(str(row[headers.index('Total Retencoes')] or 0)) - esperado_total) < Decimal('0.001')
                    esperado_iss_retido = iss if codigo in {'2', '3'} else Decimal('0')
                    assert Decimal(str(row[headers.index('ISS Retido (valor)')])) == esperado_iss_retido
                    status = row[headers.index('Conferencia Retencoes')]
                    assert status.startswith('AJUSTADO') == ajustar
                    if ajustar:
                        retencoes_ajustadas[tipo] += 1
                    if status.startswith('CONFERIR'):
                        retencoes_a_conferir[tipo] += 1
                    totais[tipo] += 1
                    antes = linhas_antigas.get(arquivo)
                    if antes is not None:
                        for i in range(28):
                            if row[i] != antes[i]:
                                if i == 14:
                                    bases_corrigidas[tipo] += 1
                                elif i == 23 and ajustar:
                                    pass  # Comparado acima, com trilha do total original.
                                else:
                                    alteracoes.append({'cliente': cliente.name, 'tipo': tipo, 'arquivo': arquivo, 'coluna': i + 1})
        finally:
            novo.close()
            if antigo is not None:
                antigo.close()
    # Sem metadados de NSU nao se pode emitir um veredito de completude ADN.
    # Esta ferramenta verifica apenas os XMLs locais, sem gerar consolidado.
    resultado = {
        'relatorios': len(resumos), 'notas': dict(totais),
        'bases_iss_corrigidas': dict(bases_corrigidas), 'outras_alteracoes_de_valores': alteracoes,
        'retencoes_ajustadas': dict(retencoes_ajustadas),
        'retencoes_a_conferir': dict(retencoes_a_conferir),
        'observacao': 'Amostra local; nao comprova completude das caixas ADN. Nenhuma consulta remota realizada.',
    }
    (destino / 'conferencia.json').write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(resultado, ensure_ascii=True))
    assert not alteracoes, 'Houve alteracoes adicionais: consulte conferencia.json'


if __name__ == '__main__':
    main()
