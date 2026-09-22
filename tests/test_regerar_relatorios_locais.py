from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.regerar_relatorios_locais import main
from test_retencoes_conferencia import xml_retencoes


class TestRegeneracaoLocal(unittest.TestCase):
    def test_auditoria_compara_total_ajustado_e_preserva_original(self):
        with TemporaryDirectory() as temp:
            origem, destino = Path(temp) / 'origem', Path(temp) / 'destino'
            pasta = origem / 'cliente' / 'Recebidas'
            pasta.mkdir(parents=True)
            (pasta / 'nota.xml').write_text(xml_retencoes(), encoding='utf-8')
            saida = StringIO()
            with patch('sys.argv', ['regerar', str(origem), str(destino)]), patch('tools.regerar_relatorios_locais.logging.disable'), redirect_stdout(saida):
                main()
            resultado = json.loads(saida.getvalue())
            self.assertEqual(resultado['retencoes_ajustadas'], {'Recebidas': 1})
            self.assertEqual(resultado['retencoes_a_conferir'], {})

    def test_nome_longo_e_amostra_sem_consolidado_de_completude(self):
        with TemporaryDirectory() as temp:
            origem = Path(temp) / 'origem'
            destino = Path(temp) / 'destino'
            pasta = origem / ('cliente-' + 'a' * 80) / 'Emitidas'
            pasta.mkdir(parents=True)
            xml = pasta / 'nota.xml'
            conteudo = '''<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">
              <infNFSe><valores><vBC>100</vBC><vISSQN>2</vISSQN>
                <vTotalRet>0</vTotalRet><vLiq>100</vLiq></valores>
                <DPS><infDPS><valores><trib><tribMun><tpRetISSQN>1</tpRetISSQN>
                </tribMun></trib></valores></infDPS></DPS>
              </infNFSe></NFSe>'''
            xml.write_text(conteudo, encoding='utf-8')
            saida = StringIO()
            with patch('sys.argv', ['regerar', str(origem), str(destino)]), patch('tools.regerar_relatorios_locais.logging.disable'), redirect_stdout(saida):
                main()
            resultado = json.loads(saida.getvalue())
            self.assertEqual(resultado['relatorios'], 1)
            self.assertEqual(resultado['notas'], {'Emitidas': 1})
            self.assertEqual(len(list(destino.glob('*/_Relatorio_*.xlsx'))), 1)
            self.assertFalse((destino / '_Relatorio_Consolidado.xlsx').exists())
            self.assertEqual(xml.read_text(encoding='utf-8'), conteudo)
