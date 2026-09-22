"""Casos fiscais ficticios: nao confundir imposto devido com retencao."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook

from app.reports.relatorio import extrair_nota, gerar_excel_cliente, gerar_consolidado


def xml_retencoes(iss_retido='1', total='28.17', liquido='563.52', federal='',
                  servico='563.52', iss='28.17'):
    return f'''<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">
      <infNFSe Id="NFS123"><nNFSe>1</nNFSe><valores><vBC>{servico}</vBC>
        <vISSQN>{iss}</vISSQN><vTotalRet>{total}</vTotalRet><vLiq>{liquido}</vLiq>
      </valores><DPS><infDPS><dhEmi>2026-09-01T12:00:00-03:00</dhEmi>
        <valores><vServPrest><vServ>{servico}</vServ></vServPrest>
          <trib><tribMun><tpRetISSQN>{iss_retido}</tpRetISSQN></tribMun>
            <tribFed>{federal}</tribFed>
          </trib></valores></infDPS></DPS></infNFSe></NFSe>'''


FEDERAL = '''<piscofins><vPis>9.18</vPis><vCofins>42.36</vCofins>
  <tpRetPisCofins>3</tpRetPisCofins></piscofins>
  <vRetIRRF>21.18</vRetIRRF><vRetCSLL>65.66</vRetCSLL><vRetCP>0</vRetCP>'''


class TestConferenciaRetencoes(unittest.TestCase):
    def extrair(self, **kwargs):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'nota.xml'
            path.write_text(xml_retencoes(**kwargs), encoding='utf-8')
            return extrair_nota(path)

    def test_iss_nao_retido_nao_compoe_total_e_original_fica_disponivel(self):
        nota = self.extrair()
        self.assertEqual(nota['vTotalRet'], 0)
        self.assertEqual(nota['vTotalRetXML'], 28.17)
        self.assertEqual(nota['vISSRetido'], 0)
        self.assertEqual(nota['vISSQN'], 28.17)
        self.assertEqual(nota['vLiq'], 563.52)
        self.assertIn('AJUSTADO', nota['conferenciaRetencoes'])

    def test_iss_retido_pelo_tomador_ou_intermediario_continua_no_total(self):
        for codigo in ('2', '3'):
            with self.subTest(codigo=codigo):
                nota = self.extrair(iss_retido=codigo, liquido='535.35')
                self.assertEqual(nota['vTotalRet'], 28.17)
                self.assertEqual(nota.get('vISSRetido'), 28.17)
                self.assertEqual(nota.get('conferenciaRetencoes'), 'OK')

    def test_corrige_somente_iss_nao_retido_preservando_retencoes_federais(self):
        nota = self.extrair(servico='1200', iss='26.04', total='158.04',
                            liquido='1068', federal='<vRetCP>132</vRetCP>')
        self.assertEqual(nota['vTotalRet'], 132)
        self.assertEqual(nota['vRetCP'], 132)
        self.assertEqual(nota['vTotalRetXML'], 158.04)

    def test_total_do_print_nao_soma_debitos_proprios_novamente(self):
        nota = self.extrair(servico='1412', iss='42.36', total='86.84',
                            liquido='1325.16', federal=FEDERAL)
        self.assertEqual(nota['vTotalRet'], 86.84)
        self.assertEqual(nota.get('conferenciaRetencoes'), 'OK')

    def test_inconsistencias_sem_evidencia_suficiente_exigem_conferencia(self):
        for kwargs in ({'iss_retido': ''}, {'iss_retido': '99'},
                       {'liquido': '535.35'}, {'total': '40'},
                       {'liquido': 'invalido'}, {'total': 'invalido'}):
            with self.subTest(kwargs=kwargs):
                nota = self.extrair(**kwargs)
                self.assertIn('CONFERIR', nota.get('conferenciaRetencoes', ''))
                if kwargs.get('total') != 'invalido':
                    self.assertEqual(nota['vTotalRet'], float(kwargs.get('total', '28.17')))

    def test_codigo_legado_com_pis_cofins_nao_e_reinterpretado(self):
        nota = self.extrair(total='79.71', liquido='483.81', federal='''
          <piscofins><vPis>9.18</vPis><vCofins>42.36</vCofins>
          <tpRetPisCofins>1</tpRetPisCofins></piscofins>''')
        self.assertEqual(nota['vTotalRet'], 79.71)
        self.assertIn('CONFERIR', nota.get('conferenciaRetencoes', ''))

    def test_excel_separa_apuracao_propria_e_retencoes_em_todas_as_abas(self):
        for tipo in ('Emitidas', 'Recebidas', 'Canceladas'):
            with self.subTest(tipo=tipo), TemporaryDirectory() as temp:
                base = Path(temp)
                pasta = base / tipo
                pasta.mkdir()
                (pasta / 'nota.xml').write_text(xml_retencoes(servico='1412', iss='42.36',
                    total='86.84', liquido='1325.16', federal=FEDERAL), encoding='utf-8')
                gerar_excel_cliente(base, 'Teste')
                wb = load_workbook(base / '_Relatorio_Teste.xlsx', data_only=True)
                try:
                    ws = wb[tipo]
                    row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
                    self.assertEqual(row.get('PIS - Apuracao propria'), 9.18)
                    self.assertEqual(row.get('COFINS - Apuracao propria'), 42.36)
                    self.assertEqual(row.get('Contrib. sociais retidas (PIS/COFINS/CSLL)'), 65.66)
                    self.assertEqual(row.get('ISS Retido (valor)'), 0)
                    self.assertEqual(row.get('Total Retencoes XML'), 86.84)
                    self.assertEqual(row.get('Conferencia Retencoes'), 'OK')
                    self.assertEqual(row['Total Retencoes'], 86.84)
                    headers = next(r for r in wb['Resumo'].values if r[0] == 'Tipo')
                    self.assertIn('PIS - Apuracao propria', headers)
                    self.assertIn('Contrib. sociais retidas (PIS/COFINS/CSLL)', headers)
                finally:
                    wb.close()

    def test_totais_da_aba_resumo_e_consolidado_usam_total_corrigido(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'Recebidas').mkdir()
            (base / 'Recebidas' / 'nota.xml').write_text(xml_retencoes(), encoding='utf-8')
            resumo = gerar_excel_cliente(base, 'Teste', conferencia={'completo': True})
            self.assertEqual(resumo.totais['Recebidas']['vTotalRet'], 0)
            consolidado = gerar_consolidado(base, [resumo])
            for path, aba, header_row, total_row in (
                (base / '_Relatorio_Teste.xlsx', 'Recebidas', 1, 3),
                (consolidado, 'Consolidado', 3, 4),
            ):
                wb = load_workbook(path, data_only=True)
                try:
                    ws = wb[aba]
                    headers = [c.value for c in ws[header_row]]
                    self.assertEqual(ws.cell(total_row, headers.index('Total Retencoes') + 1).value, 0)
                finally:
                    wb.close()

    def test_resumo_mostra_quantidade_ajustada_e_pendencias_fiscais(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            pasta = base / 'Recebidas'
            pasta.mkdir()
            (pasta / 'ajustada.xml').write_text(xml_retencoes(), encoding='utf-8')
            (pasta / 'pendente.xml').write_text(xml_retencoes(total='40'), encoding='utf-8')
            resumo = gerar_excel_cliente(base, 'Teste')
            wb = load_workbook(base / '_Relatorio_Teste.xlsx', data_only=True)
            try:
                indicadores = {r[0]: r[1] for r in wb['Resumo'].values if r[0]}
                self.assertEqual(indicadores.get('Notas com retencoes ajustadas'), 1)
                self.assertEqual(indicadores.get('Notas com retencoes a conferir'), 1)
            finally:
                wb.close()
            consolidado = gerar_consolidado(base, [resumo])
            wb = load_workbook(consolidado, data_only=True)
            try:
                ws = wb['Consolidado']
                headers = [c.value for c in ws[3]]
                self.assertIn('Retencoes a conferir', headers)
                self.assertEqual(ws.cell(4, headers.index('Retencoes a conferir') + 1).value, 1)
            finally:
                wb.close()
