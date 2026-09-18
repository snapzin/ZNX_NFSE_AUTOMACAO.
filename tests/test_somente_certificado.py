import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from api import server
from app.automation.nfse_automacao import Cliente, NFSeRunner, preparar_parametros


class TestSomenteCertificado(unittest.TestCase):
    def test_execucao_legada_unifica_documento_sem_colapsar_filial(self):
        clientes = [
            {"documento": "12345678000100", "nome": "Antigo", "tipo_login": "usuario_senha", "senha_nfse": "segredo"},
            {"documento": "12.345.678/0001-00", "nome": "Matriz", "tipo_login": "certificado"},
            {"documento": "12345678000200", "nome": "Filial", "tipo_login": "usuario_senha"},
        ]
        for modo in ("", "certificado", "ambos", "usuario_senha"):
            with self.subTest(modo=modo):
                result = server._filtrar_clientes_por_login(clientes, modo)
                self.assertEqual([c["documento"] for c in result], ["12345678000100", "12345678000200"])
                self.assertEqual([c["tipo_login"] for c in result], ["certificado", "certificado"])
                self.assertTrue(all("senha_nfse" not in c for c in result))

    def test_parametros_descartam_login_por_senha(self):
        params = preparar_parametros(clientes=[{
            "documento": "12345678000100", "nome": "Cliente",
            "tipo_login": "usuario_senha", "senha_nfse": "segredo",
        }])
        self.assertEqual(params.clientes_lista[0]["tipo_login"], "certificado")
        self.assertFalse(params.clientes_lista[0].get("senha_nfse"))

    def test_runner_ignora_login_legado_e_usa_adn(self):
        with (
            TemporaryDirectory() as pasta,
            patch.object(server.config, "PASTA_SAIDA", pasta),
            patch("app.automation.portal_auth.validar_login_portal", side_effect=AssertionError("Nao deve abrir portal")),
            patch("app.automation.nfse_automacao._baixar_cliente_adn", return_value=(1, pasta, 0, "", {})) as baixar,
        ):
            params = preparar_parametros(clientes=[{"documento": "12345678000100", "nome": "Cliente"}])
            cliente = Cliente("12345678000100", "Cliente", "usuario_senha", "usuario", "senha")
            cert = object()
            resultado = NFSeRunner().processar_cliente(cliente, cert, params)
            self.assertEqual(resultado[0], 1)
            self.assertIs(baixar.call_args.args[0], cert)

    def test_salvar_sem_credenciais_preserva_arquivo_legado(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "clientes_credenciais.json"
            arquivo.write_text('{"legado": {"senha_nfse": "segredo"}}', encoding="utf-8")
            with patch("api.server._credenciais_path", return_value=arquivo):
                server._salvar_credenciais_locais([{"documento": "12345678000100", "tipo_login": "certificado"}])
            self.assertIn("legado", arquivo.read_text(encoding="utf-8"))
