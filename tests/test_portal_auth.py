from types import SimpleNamespace
import unittest

from app.automation.portal_auth import ErroLoginNFSe, credenciais_para, login_usuario_senha


class Locator:
    def __init__(self, page, selector): self.page, self.selector, self.first = page, selector, self
    def count(self): return 1 if self.selector in self.page.present else 0
    def is_visible(self): return True
    def fill(self, value): self.page.filled[self.selector] = value
    def click(self, **_): self.page.url = self.page.after_url
    def inner_text(self, **_): return self.page.body
    def wait_for(self, **_):
        if self.selector not in self.page.present: raise TimeoutError()


class Page:
    def __init__(self, after_url="https://www.nfse.gov.br/EmissorNacional", body=""):
        self.url, self.after_url, self.body = "", after_url, body
        self.present = {"input[placeholder*='CPF/CNPJ' i]", "input[type='password']", "button:has-text('Entrar')", "body"}
        self.filled = {}
    def goto(self, url, **_): self.url = url
    def locator(self, selector): return Locator(self, selector)
    def wait_for_load_state(self, *_args, **_kwargs): pass


class TestPortalAuth(unittest.TestCase):
    def test_login_valido_preenche_documento_e_senha(self):
        page = Page()
        login_usuario_senha(page, "12345678000100", "segredo", login_url="https://portal/login")
        self.assertEqual(page.filled["input[placeholder*='CPF/CNPJ' i]"], "12345678000100")
        self.assertEqual(page.filled["input[type='password']"], "segredo")

    def test_senha_incorreta_interrompe_fluxo(self):
        page = Page(after_url="https://portal/login", body="Usuário ou senha inválidos")
        with self.assertRaisesRegex(ErroLoginNFSe, "invalidos"):
            login_usuario_senha(page, "12345678901", "errada", login_url="https://portal/login")

    def test_certificado_nao_exige_usuario_ou_senha(self):
        cfg = SimpleNamespace(NFSE_CREDENCIAIS={"12345678000100": {"tipo_login": "certificado"}})
        self.assertEqual(
            credenciais_para(cfg, "12.345.678/0001-00").tipo_login, "certificado"
        )


if __name__ == "__main__":
    unittest.main()
