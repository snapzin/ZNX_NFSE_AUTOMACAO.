from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api import server
from app.automation.nfse_automacao import preparar_parametros


class TestClientesCredenciais(unittest.TestCase):
    def test_normalizacao_preserva_tipo_usuario_e_senha(self):
        cliente = server._normalizar_clientes([{
            "documento": "12.345.678/0001-00",
            "nome": "Cliente",
            "tipo_login": "usuario_senha",
            "usuario_nfse": "12345678000100",
            "senha_nfse": "segredo",
        }])[0]
        self.assertEqual(cliente["tipo_login"], "usuario_senha")
        self.assertEqual(cliente["usuario_nfse"], "12345678000100")
        self.assertEqual(cliente["senha_nfse"], "segredo")

    def test_credenciais_ficam_no_arquivo_local(self):
        with TemporaryDirectory() as pasta:
            xlsx = Path(pasta) / "clientes.xlsx"
            cliente = {
                "documento": "12345678000100", "nome": "Cliente", "ativo": True,
                "tipo_login": "usuario_senha", "usuario_nfse": "12345678000100",
                "senha_nfse": "segredo",
            }
            with patch.object(server, "_resolver_xlsx_path", return_value=xlsx):
                server._salvar_credenciais_locais([cliente])
                carregado = server._aplicar_credenciais_locais([{
                    "documento": "12345678000100", "nome": "Cliente",
                    "tipo_login": "certificado", "usuario_nfse": "", "senha_nfse": "",
                }])[0]
            self.assertEqual(carregado["tipo_login"], "usuario_senha")
            self.assertEqual(carregado["senha_nfse"], "segredo")

    def test_automacao_recebe_dados_da_aba_clientes(self):
        params = preparar_parametros(clientes=[{
            "documento": "12345678000100", "nome": "Cliente",
            "tipo_login": "usuario_senha", "usuario_nfse": "12345678000100",
            "senha_nfse": "segredo",
        }])
        dados = params.clientes_dados["12345678000100"]
        self.assertEqual(dados["tipo_login"], "usuario_senha")
        self.assertEqual(dados["senha_nfse"], "segredo")

    def test_senha_nao_e_enviada_ao_servidor_remoto(self):
        enviados = {}

        def chamada(op, _license, clientes=None):
            enviados["op"] = op
            enviados["clientes"] = clientes
            return {"salvos": len(clientes or []), "machine_id": "abc", "updatedAt": "agora"}

        payload = {"clientes": [{
            "documento": "12345678000100", "nome": "Cliente", "ativo": True,
            "tipo_login": "usuario_senha", "usuario_nfse": "12345678000100",
            "senha_nfse": "segredo",
        }]}
        request = SimpleNamespace(headers={"x-license-key": "licenca"})
        with (
            patch.object(server, "_clientes_remotos_ativos", return_value=True),
            patch.object(server, "_clientes_remote_call", side_effect=chamada),
            patch.object(server, "_salvar_overlay"),
            patch.object(server, "_salvar_credenciais_locais"),
        ):
            asyncio.run(server.salvar_clientes(request, payload))
        remoto = enviados["clientes"][0]
        self.assertNotIn("senha_nfse", remoto)
        self.assertNotIn("usuario_nfse", remoto)
        self.assertEqual(remoto["nome"], "Cliente")


if __name__ == "__main__":
    unittest.main()
