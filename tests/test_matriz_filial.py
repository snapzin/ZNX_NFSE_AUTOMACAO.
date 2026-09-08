from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.automation.cert_reader import CertificadoInfo, indexar_certificados_por_cnpj
from app.automation.nfse_automacao import (
    Cliente,
    NFSeRunner,
    Parametros,
    _baixar_cliente_adn,
    _certificado_da_matriz_para_filial,
)


def certificado(cnpj: str, validade: str = "31/12/2030") -> CertificadoInfo:
    return CertificadoInfo(
        arquivo=Path("certificado.pfx"), nome_amigavel="Empresa", senha="senha",
        cn=f"Empresa:{cnpj}", documento=cnpj, thumbprint_sha1="ABC",
        serial_number="1", valido_ate=validade,
    )


class TestMatrizFilial(unittest.TestCase):
    def test_cnpjs_da_mesma_raiz_continuam_distintos(self):
        matriz = certificado("12345678000190")
        filial = certificado("12345678000270")
        unicos, duplicados = indexar_certificados_por_cnpj([matriz, filial])
        self.assertEqual(set(unicos), {"12345678000190", "12345678000270"})
        self.assertEqual(duplicados, {})

    def test_filial_encontra_apenas_certificado_da_matriz(self):
        matriz = certificado("12345678000190")
        outra_empresa = certificado("99999999000199")
        escolhido = _certificado_da_matriz_para_filial(
            "12345678000511", [outra_empresa, matriz]
        )
        self.assertIs(escolhido, matriz)

    def test_adn_recebe_cnpj_completo_da_filial(self):
        matriz = certificado("12345678000190")
        cliente = Cliente(cnpj="12345678000511", nome="Filial 5")
        params = Parametros(
            xlsx_path="", pasta_certs="", pasta_saida="", data_inicio="01/08/2026",
            data_fim="31/08/2026",
        )
        resultado = SimpleNamespace(
            erro="", nsus_faltando=[], ultimo_nsu=0, nsus_vistos=[], caixa_completa=True,
            emitidas=0, recebidas=0, canceladas=0, ignorados_competencia=0,
        )
        with patch("app.automation.adn_client.baixar_dfe", return_value=resultado) as baixar:
            _baixar_cliente_adn(matriz, cliente, params, Path("saida"))
        chamada = baixar.call_args.kwargs
        self.assertEqual(chamada["owner_cnpj"], "12345678000511")
        self.assertEqual(chamada["cnpj_consulta"], "12345678000511")

    def test_matriz_e_filial_usam_pastas_separadas(self):
        params = Parametros(
            xlsx_path="", pasta_certs="", pasta_saida="", data_inicio="01/08/2026",
            data_fim="31/08/2026", clientes_map={
                "12345678000190": "Empresa", "12345678000511": "Empresa",
            },
        )
        runner = object.__new__(NFSeRunner)
        runner.cancel_event = None
        runner.base_output = Path("saida")
        filial = Cliente(cnpj="12345678000511", nome="Empresa")
        retorno = (0, "", 0, "", {})
        with (
            patch("app.automation.portal_auth.credenciais_para", return_value=SimpleNamespace(tipo_login="certificado")),
            patch("app.automation.nfse_automacao._baixar_cliente_adn", return_value=retorno) as baixar,
            patch.object(Path, "mkdir"),
        ):
            runner.processar_cliente(filial, certificado("12345678000190"), params)
        self.assertEqual(baixar.call_args.args[3], Path("saida/Empresa - 12345678000511"))


if __name__ == "__main__":
    unittest.main()
