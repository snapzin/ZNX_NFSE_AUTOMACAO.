from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from app.reports.relatorio import extrair_nota, gerar_excel_cliente, normalizar_iss_retido


class TestIssRetido(unittest.TestCase):
    def test_normaliza_dominio_oficial_tp_ret_issqn(self):
        self.assertEqual(normalizar_iss_retido("1"), "Não")
        self.assertEqual(normalizar_iss_retido("2"), "Sim")
        self.assertEqual(normalizar_iss_retido("3"), "Sim")

    def test_normaliza_formatos_alternativos(self):
        positivos = [True, "true", "Sim", "S", "Retido", "retido pelo tomador"]
        negativos = [False, None, "", "false", "0", "Nao", "N", "Não Retido"]
        self.assertTrue(all(normalizar_iss_retido(v) == "Sim" for v in positivos))
        self.assertTrue(all(normalizar_iss_retido(v) == "Não" for v in negativos))

    def test_xml_alimenta_coluna_iss_retido(self):
        with tempfile.TemporaryDirectory() as pasta:
            xml = Path(pasta) / "nota.xml"
            xml.write_text("<NFSe><infNFSe Id='NFS123'><tpRetISSQN>2</tpRetISSQN></infNFSe></NFSe>")
            self.assertEqual(extrair_nota(xml)["issRetido"], "Sim")
            xml.write_text("<NFSe><infNFSe Id='NFS123'><tpRetISSQN>1</tpRetISSQN></infNFSe></NFSe>")
            self.assertEqual(extrair_nota(xml)["issRetido"], "Não")

    def test_excel_recebe_sim_e_nao(self):
        with tempfile.TemporaryDirectory() as pasta:
            base = Path(pasta)
            emitidas = base / "Emitidas"
            emitidas.mkdir()
            (emitidas / "retida.xml").write_text(
                "<NFSe><infNFSe Id='NFS1'><nNFSe>1</nNFSe><tpRetISSQN>2</tpRetISSQN></infNFSe></NFSe>"
            )
            (emitidas / "nao_retida.xml").write_text(
                "<NFSe><infNFSe Id='NFS2'><nNFSe>2</nNFSe><tpRetISSQN>1</tpRetISSQN></infNFSe></NFSe>"
            )
            gerar_excel_cliente(base, "Cliente Teste")
            wb = load_workbook(base / "_Relatorio_Cliente Teste.xlsx", data_only=True)
            ws = wb["Emitidas"]
            coluna = [cell.value for cell in ws[1]].index("ISS Retido") + 1
            self.assertEqual(ws.cell(2, coluna).value, "Não")  # ordem alfabetica dos arquivos
            self.assertEqual(ws.cell(3, coluna).value, "Sim")
            wb.close()

    def test_iss_e_valores_monetarios_do_xml_chegam_juntos_ao_excel(self):
        # Casos derivados dos valores das imagens, sem dados reais de clientes.
        casos = [
            ("2", "Sim", "115445.42", "2308.91", "12698.99", "15007.90", "100437.52"),
            ("1", "Não", "411097.44", "20554.87", "0.00", "0.00", "411097.44"),
            ("1", "Não", "1200.00", "26.04", "132.00", "132.00", "1068.00"),
            ("3", "Sim", "1000.00", "50.00", "0.00", "50.00", "950.00"),
        ]
        for tipo in ("Emitidas", "Recebidas"):
            for codigo, flag, servico, iss, inss, retencoes, liquido in casos:
                with self.subTest(tipo=tipo, codigo=codigo, servico=servico), tempfile.TemporaryDirectory() as pasta:
                    base = Path(pasta)
                    (base / tipo).mkdir()
                    xml = base / tipo / "nota.xml"
                    xml.write_text(f'''<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">
                      <infNFSe Id="NFS123"><nNFSe>1</nNFSe>
                        <emit><CNPJ>12345678000100</CNPJ><xNome>Exemplo</xNome></emit>
                        <valores><vBC>{servico}</vBC><vISSQN>{iss}</vISSQN>
                          <vTotalRet>{retencoes}</vTotalRet><vLiq>{liquido}</vLiq></valores>
                        <DPS><infDPS><dCompet>2026-08-01</dCompet><valores>
                          <vServPrest><vServ>{servico}</vServ></vServPrest>
                          <trib><tribMun><tpRetISSQN>{codigo}</tpRetISSQN></tribMun>
                            <tribFed><vRetCP>{inss}</vRetCP></tribFed></trib>
                        </valores></infDPS></DPS>
                      </infNFSe></NFSe>''', encoding="utf-8")
                    nota = extrair_nota(xml)
                    self.assertEqual(nota["issRetido"], flag)
                    gerar_excel_cliente(base, "Teste")
                    wb = load_workbook(base / "_Relatorio_Teste.xlsx", data_only=True)
                    try:
                        ws = wb[tipo]
                        row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
                        self.assertEqual(row["ISS Retido"], flag)
                        for label, expected in (("Valor Servico", servico), ("Valor ISS", iss),
                                                ("INSS/CP", inss), ("Total Retencoes", retencoes), ("Valor Liquido", liquido)):
                            self.assertEqual(row[label], float(expected))
                        # Totais da aba preservam os valores do XML, sem somar ISS nao retido.
                        self.assertEqual(ws.cell(3, 24).value, float(retencoes))
                        self.assertEqual(ws.cell(3, 25).value, float(liquido))
                    finally:
                        wb.close()


if __name__ == "__main__":
    unittest.main()
