"""Regressoes de blocos fiscais nas emitidas (XML nacional, dados ficticios)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
from app.reports.relatorio import extrair_nota, gerar_excel_cliente


def xml_nacional(valores_iss="", federal="", ibs="", totais=""):
    return f'''<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">
      <infNFSe Id="NFS123"><nNFSe>1</nNFSe>
        <emit><CNPJ>12345678000100</CNPJ><xNome>Exemplo</xNome></emit>
        <valores>{valores_iss}<vTotalRet>86.84</vTotalRet><vLiq>1325.16</vLiq></valores>
        <DPS><infDPS><dhEmi>2026-09-01T12:00:00-03:00</dhEmi><dCompet>2026-09-01</dCompet>
          <valores><vServPrest><vServ>1412.00</vServ></vServPrest>
            <trib><tribMun><tpRetISSQN>1</tpRetISSQN></tribMun>
              <tribFed>{federal}</tribFed>{totais}</trib>
          </valores>
        </infDPS></DPS>{ibs}
      </infNFSe></NFSe>'''


FEDERAL_PORTAL = '''<piscofins><CST>01</CST><vBCPisCofins>1412.00</vBCPisCofins>
  <pAliqPis>0.65</pAliqPis><pAliqCofins>3.00</pAliqCofins>
  <vPis>9.18</vPis><vCofins>42.36</vCofins><tpRetPisCofins>3</tpRetPisCofins>
  </piscofins><vRetIRRF>21.18</vRetIRRF><vRetCSLL>65.66</vRetCSLL><vRetCP>0.00</vRetCP>'''


class TestRelatorioEmitidas(unittest.TestCase):
    def test_base_ibs_nao_se_torna_base_iss_quando_iss_ausente(self):
        for tipo in ('Emitidas', 'Recebidas'):
            with self.subTest(tipo=tipo), TemporaryDirectory() as temp:
                path = Path(temp) / tipo / 'nota.xml'
                path.parent.mkdir()
                path.write_text(xml_nacional(ibs='<IBSCBS><valores><vBC>1412.00</vBC></valores></IBSCBS>'), encoding='utf-8')
                nota = extrair_nota(path)
                self.assertEqual(nota['vBC'], 0)
                self.assertEqual(nota['vISSQN'], 0)
                self.assertEqual(nota['vServ'], 1412)

    def test_descricao_distingue_nao_retido_de_indicador_ausente(self):
        for codigo, descricao in (
            ('0', '0 - PIS/COFINS/CSLL Nao Retidos'),
            ('8', '8 - PIS/COFINS Nao Retidos, CSLL Retido'),
            ('99', '99 - Codigo nao reconhecido'),
            ('', 'Nao informado'),
        ):
            with self.subTest(codigo=codigo), TemporaryDirectory() as temp:
                path = Path(temp) / 'nota.xml'
                path.write_text(xml_nacional(federal=f'<piscofins><tpRetPisCofins>{codigo}</tpRetPisCofins></piscofins>'), encoding='utf-8')
                self.assertEqual(extrair_nota(path)['descricaoRetSociais'], descricao)

    def test_base_iss_preservada_quando_xml_tambem_contem_ibs(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'Emitidas' / 'nota.xml'
            path.parent.mkdir()
            path.write_text(xml_nacional(valores_iss='<vBC>1200.00</vBC><pAliqAplic>2.17</pAliqAplic><vISSQN>26.04</vISSQN>',
                                          ibs='<IBSCBS><valores><vBC>1412.00</vBC></valores></IBSCBS>'), encoding='utf-8')
            nota = extrair_nota(path)
            self.assertEqual((nota['vBC'], nota['pAliqAplic'], nota['vISSQN']), (1200, 2.17, 26.04))

    def test_emitidas_identificam_contribuicoes_agrupadas_sem_duplicar_pis_cofins(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'Emitidas').mkdir()
            (base / 'Emitidas' / 'nota.xml').write_text(xml_nacional(federal=FEDERAL_PORTAL,
                totais='<totTrib><vTotTrib><vTotTribFed>189.91</vTotTribFed><vTotTribMun>39.25</vTotTribMun></vTotTrib></totTrib>'), encoding='utf-8')
            gerar_excel_cliente(base, 'Teste')
            wb = load_workbook(base / '_Relatorio_Teste.xlsx', data_only=True)
            try:
                ws = wb['Emitidas']
                row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
                self.assertEqual(row.get('Contrib. sociais retidas (PIS/COFINS/CSLL)'), 65.66)
                self.assertEqual(row.get('PIS - Apuracao propria'), 9.18)
                self.assertEqual(row.get('COFINS - Apuracao propria'), 42.36)
                self.assertEqual(row['IRRF'], 21.18)
                self.assertEqual(row['INSS/CP'], 0)
                self.assertEqual(row['Total Retencoes'], 86.84)
                self.assertEqual(row['Valor Liquido'], 1325.16)
                self.assertEqual(row.get('Descricao contrib. sociais retidas'), '3 - PIS/COFINS/CSLL Retidos')
                self.assertEqual(ws.cell(3, 24).value, 86.84)
                resumo_rows = list(wb['Resumo'].values)
                headers = next(r for r in resumo_rows if r[0] == 'Tipo')
                emitida = next(r for r in resumo_rows if r[0] == 'Emitidas')
                self.assertEqual(emitida[headers.index('Contrib. sociais retidas (PIS/COFINS/CSLL)')], 65.66)
            finally:
                wb.close()

    def test_recebidas_preservam_posicoes_e_valores_e_identificam_apuracao_propria(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'Recebidas').mkdir()
            (base / 'Recebidas' / 'nota.xml').write_text(xml_nacional(federal=FEDERAL_PORTAL), encoding='utf-8')
            gerar_excel_cliente(base, 'Teste')
            wb = load_workbook(base / '_Relatorio_Teste.xlsx', data_only=True)
            try:
                ws = wb['Recebidas']
                self.assertEqual(ws.max_column, 32)
                self.assertEqual([ws.cell(1, c).value for c in (19, 20, 22)],
                                 ['PIS - Apuracao propria', 'COFINS - Apuracao propria',
                                  'Contrib. sociais retidas (PIS/COFINS/CSLL)'])
                self.assertEqual([ws.cell(2, c).value for c in (19, 20, 21, 22, 23, 24, 25)], [9.18, 42.36, 21.18, 65.66, 0, 86.84, 1325.16])
            finally:
                wb.close()
