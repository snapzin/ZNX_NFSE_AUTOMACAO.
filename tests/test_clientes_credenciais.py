from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api import server
from fastapi import HTTPException
from app.automation.cert_reader import CertificadoInfo
from app.automation.nfse_automacao import executar_local, preparar_parametros


class TestClientesCredenciais(unittest.TestCase):
    def test_todos_os_modos_legados_usam_certificado(self):
        clientes = [
            {"documento": "1", "tipo_login": "certificado", "ativo": True},
            {"documento": "2", "tipo_login": "usuario_senha", "ativo": True},
            {"documento": "3", "tipo_login": "usuario_senha", "ativo": False},
        ]
        certificados = server._filtrar_clientes_por_login(clientes, "certificado")
        usuarios = server._filtrar_clientes_por_login(clientes, "usuario_senha")
        ambos = server._filtrar_clientes_por_login(clientes, "ambos")
        self.assertEqual([c["documento"] for c in certificados], ["1", "2"])
        self.assertEqual([c["documento"] for c in usuarios], ["1", "2"])
        self.assertEqual([c["documento"] for c in ambos], ["1", "2"])

    def test_mesmo_documento_e_executado_uma_vez_com_certificado(self):
        documento = "12345678000100"
        clientes = [
            {"documento": documento, "tipo_login": "certificado", "ativo": True},
            {"documento": documento, "tipo_login": "usuario_senha", "ativo": True},
        ]
        certificados = server._filtrar_clientes_por_login(clientes, "certificado")
        usuarios = server._filtrar_clientes_por_login(clientes, "usuario_senha")
        ambos = server._filtrar_clientes_por_login(clientes, "ambos")
        self.assertEqual([c["tipo_login"] for c in certificados], ["certificado"])
        self.assertEqual([c["tipo_login"] for c in usuarios], ["certificado"])
        self.assertEqual(len(ambos), 1)

    def test_execucao_nao_exige_escolher_tipo_login(self):
        self.assertEqual(server._filtrar_clientes_por_login([], ""), [])

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

    def test_normalizacao_remove_repeticao_apenas_dentro_da_mesma_aba(self):
        documento = "12345678000100"
        clientes = server._normalizar_clientes([
            {"documento": documento, "nome": "Cert 1", "tipo_login": "certificado"},
            {"documento": documento, "nome": "Cert 2", "tipo_login": "certificado"},
            {"documento": documento, "nome": "Usuario", "tipo_login": "usuario_senha"},
        ])
        self.assertEqual(
            [(c["nome"], c["tipo_login"]) for c in clientes],
            [("Cert 1", "certificado"), ("Usuario", "usuario_senha")],
        )

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

    def test_mesmo_documento_mantem_selecao_e_credenciais_separadas_por_aba(self):
        with TemporaryDirectory() as pasta:
            xlsx = Path(pasta) / "clientes.xlsx"
            documento = "12345678000100"
            cadastrados = [
                {
                    "documento": documento, "nome": "Cliente certificado", "ativo": False,
                    "tipo_login": "certificado", "usuario_nfse": "", "senha_nfse": "",
                },
                {
                    "documento": documento, "nome": "Cliente usuario", "ativo": True,
                    "tipo_login": "usuario_senha", "usuario_nfse": documento,
                    "senha_nfse": "segredo",
                },
            ]
            vindos_do_servidor = [
                {"documento": documento, "nome": "Cliente certificado", "tipo_login": "certificado"},
                {"documento": documento, "nome": "Cliente usuario", "tipo_login": "usuario_senha"},
            ]
            with patch.object(server, "_resolver_xlsx_path", return_value=xlsx):
                server._salvar_overlay(cadastrados)
                server._salvar_credenciais_locais(cadastrados)
                carregados = server._aplicar_dados_locais(vindos_do_servidor)

            certificado, usuario = carregados
            self.assertEqual(certificado["tipo_login"], "certificado")
            self.assertFalse(certificado["ativo"])
            self.assertEqual(certificado.get("senha_nfse", ""), "")
            self.assertEqual(usuario["tipo_login"], "usuario_senha")
            self.assertTrue(usuario["ativo"])
            self.assertEqual(usuario["senha_nfse"], "segredo")

    def test_credencial_legada_nao_converte_as_duas_abas_para_usuario(self):
        with TemporaryDirectory() as pasta:
            xlsx = Path(pasta) / "clientes.xlsx"
            documento = "12345678000100"
            legado = {
                documento: {
                    "tipo_login": "usuario_senha",
                    "usuario_nfse": documento,
                    "senha_nfse": "segredo",
                }
            }
            (Path(pasta) / "clientes_credenciais.json").write_text(
                json.dumps(legado), encoding="utf-8"
            )
            clientes = [
                {"documento": documento, "tipo_login": "certificado"},
                {"documento": documento, "tipo_login": "usuario_senha"},
            ]
            with patch.object(server, "_resolver_xlsx_path", return_value=xlsx):
                carregados = server._aplicar_credenciais_locais(clientes)
        self.assertEqual(carregados[0]["tipo_login"], "certificado")
        self.assertEqual(carregados[0].get("senha_nfse", ""), "")
        self.assertEqual(carregados[1]["tipo_login"], "usuario_senha")
        self.assertEqual(carregados[1]["senha_nfse"], "segredo")

    def test_falha_ao_gravar_credenciais_nao_e_ocultada(self):
        cliente = {"documento": "12345678000100", "tipo_login": "usuario_senha", "senha_nfse": "segredo"}
        with TemporaryDirectory() as pasta:
            xlsx = Path(pasta) / "clientes.xlsx"
            with (
                patch.object(server, "_resolver_xlsx_path", return_value=xlsx),
                patch.object(Path, "write_text", side_effect=OSError("disco indisponivel")),
            ):
                with self.assertRaises(OSError):
                    server._salvar_credenciais_locais([cliente])

    def test_json_de_credenciais_corrompido_e_preservado_como_backup(self):
        with TemporaryDirectory() as pasta:
            xlsx = Path(pasta) / "clientes.xlsx"
            credenciais = Path(pasta) / "clientes_credenciais.json"
            credenciais.write_text("{json quebrado", encoding="utf-8")
            with patch.object(server, "_resolver_xlsx_path", return_value=xlsx):
                carregado = server._ler_credenciais_locais()
            backups = list(Path(pasta).glob("clientes_credenciais.json.corrompido-*"))
            conteudo_backup = backups[0].read_text(encoding="utf-8") if backups else ""
        self.assertEqual(carregado, {})
        self.assertEqual(len(backups), 1)
        self.assertEqual(conteudo_backup, "{json quebrado")

    def test_automacao_recebe_dados_da_aba_clientes(self):
        params = preparar_parametros(clientes=[{
            "documento": "12345678000100", "nome": "Cliente",
            "tipo_login": "usuario_senha", "usuario_nfse": "12345678000100",
            "senha_nfse": "segredo",
        }])
        dados = params.clientes_dados["12345678000100"]
        self.assertEqual(dados["tipo_login"], "certificado")
        self.assertEqual(dados["senha_nfse"], "")

    def test_cadastro_legado_duplicado_executa_so_uma_vez(self):
        documento = "12345678000100"
        params = preparar_parametros(clientes=[
            {
                "documento": documento, "nome": "Cliente certificado",
                "tipo_login": "certificado",
            },
            {
                "documento": documento, "nome": "Cliente usuario",
                "tipo_login": "usuario_senha", "usuario_nfse": documento,
                "senha_nfse": "segredo",
            },
        ])
        cert = CertificadoInfo(
            arquivo=Path("certificado.pfx"), nome_amigavel="Cliente", senha="senha",
            cn=f"Cliente:{documento}", documento=documento, thumbprint_sha1="ABC",
            serial_number="1", valido_ate="31/12/2030",
        )
        retorno = (0, "", 0, "", {})
        with (
            patch("app.automation.nfse_automacao.listar_certificados", return_value=[cert]),
            patch("app.automation.nfse_automacao.NFSeRunner.processar_cliente", return_value=retorno) as processar,
            patch("app.automation.nfse_automacao.NFSeRunner.__init__", return_value=None),
            patch.object(server.config, "GERAR_RELATORIO", False, create=True),
            patch.object(server.config, "DOMINIO_WEB_IMPORTAR", False, create=True),
            patch("app.automation.nfse_automacao.time.sleep"),
        ):
            executar_local(params)
        tipos = [chamada.args[0].tipo_login for chamada in processar.call_args_list]
        self.assertEqual(tipos, ["certificado"])

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
        self.assertEqual(remoto["tipo_login"], "usuario_senha")

    def test_endpoint_rejeita_documento_invalido(self):
        request = SimpleNamespace(headers={"x-license-key": "licenca"})
        payload = {"clientes": [{
            "documento": "123", "nome": "Cliente", "tipo_login": "certificado",
        }]}
        with self.assertRaises(HTTPException) as erro:
            asyncio.run(server.salvar_clientes(request, payload))
        self.assertEqual(erro.exception.status_code, 400)

    def test_endpoint_rejeita_repeticao_na_mesma_aba(self):
        request = SimpleNamespace(headers={"x-license-key": "licenca"})
        cliente = {
            "documento": "12345678000100", "nome": "Cliente",
            "tipo_login": "certificado",
        }
        with self.assertRaises(HTTPException) as erro:
            asyncio.run(server.salvar_clientes(request, {"clientes": [cliente, cliente]}))
        self.assertEqual(erro.exception.status_code, 400)

    def test_endpoint_rejeita_lista_de_clientes_em_formato_invalido(self):
        request = SimpleNamespace(headers={"x-license-key": "licenca"})
        for valor in ({"cliente": "invalido"}, None):
            with self.subTest(valor=valor):
                with self.assertRaises(HTTPException) as erro:
                    asyncio.run(server.salvar_clientes(request, {"clientes": valor}))
                self.assertEqual(erro.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
