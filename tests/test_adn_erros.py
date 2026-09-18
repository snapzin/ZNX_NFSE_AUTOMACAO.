import unittest
from unittest.mock import patch
from app.automation.adn_client import iter_documentos


class TestFalhasADN(unittest.TestCase):
    def test_falha_http_nao_pode_ser_resultado_vazio_com_sucesso(self):
        for status in (0, 200, 401, 403, 429, 500, 503):
            with self.subTest(status=status), patch('app.automation.adn_client._fetch_lote', return_value=(None, status)):
                with self.assertRaises(RuntimeError):
                    list(iter_documentos(object()))

    def test_fim_real_da_caixa_permite_resultado_vazio(self):
        with patch('app.automation.adn_client._fetch_lote', return_value=(None, 404)):
            self.assertEqual(list(iter_documentos(object())), [])

    def test_interrupcao_apos_primeiro_lote_nao_passa_por_caixa_completa(self):
        with patch('app.automation.adn_client._fetch_lote', side_effect=[({'LoteDFe': [{'NSU': 1}]}, 200), (None, 503)]):
            documentos = iter_documentos(object())
            self.assertEqual(next(documentos)['NSU'], 1)
            with self.assertRaises(RuntimeError):
                next(documentos)
