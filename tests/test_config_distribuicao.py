from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.criar_config_distribuicao import criar_config_distribuicao


class TestConfigDistribuicao(unittest.TestCase):
    def test_gera_config_limpa_sem_copiar_segredos_locais(self):
        with TemporaryDirectory() as temp:
            destino = Path(temp) / 'config.py'
            criar_config_distribuicao(destino)
            texto = destino.read_text(encoding='utf-8')
            self.assertIn("LICENSE_ADMIN_TOKEN = ''", texto)
            self.assertIn("ZOHO_SMTP_PASSWORD = ''", texto)
            self.assertIn("NFSE_CREDENCIAIS = {}", texto)
            self.assertIn("PASTA_CERTS = 'Certificados'", texto)


if __name__ == '__main__':
    unittest.main()
