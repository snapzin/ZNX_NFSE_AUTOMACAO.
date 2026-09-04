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


if __name__ == "__main__":
    unittest.main()
