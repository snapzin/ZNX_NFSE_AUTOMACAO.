"""Smoke local do renderer compilado; usa dados ficticios e IPC de teste."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    root = Path(__file__).resolve().parents[1]
    handler = partial(QuietHandler, directory=str(root / 'electron' / 'renderer-dist'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script('''
                  window.__calls = [];
                  window.electronAPI = {
                    licenseStatus: async () => ({licensed: true}),
                    apiCall: async (method, endpoint, body) => {
                      window.__calls.push({method, endpoint, body});
                      if (endpoint === '/paths/status') return {needsSetup: false};
                      if (endpoint === '/clientes' && method === 'GET') return {clientes: [
                        {documento: '12345678000100', nome: 'Matriz', tipo_login: 'certificado', ativo: true},
                        {documento: '12345678000100', nome: 'Legado', tipo_login: 'usuario_senha', senha_nfse: 'teste', ativo: true},
                        {documento: '12345678000200', nome: 'Filial', tipo_login: 'usuario_senha', ativo: true}
                      ]};
                      if (endpoint === '/clientes') return {salvos: body.clientes.length};
                      if (endpoint === '/executar') return {jobId: 'teste'};
                      if (endpoint.endsWith('/status')) return {status: 'ok', logs: []};
                      return {};
                    }
                  };
                ''')
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.get_by_role('button', name='Executar agora', exact=True).click()
                page.wait_for_function("window.__calls.some(c => c.endpoint === '/executar')")
                payload = page.evaluate("window.__calls.find(c => c.endpoint === '/executar').body")
                assert payload['tipoLogin'] == 'certificado'
                assert page.get_by_text('Somente usuário e senha', exact=True).count() == 0
                page.get_by_role('button', name='Clientes', exact=True).click()
                page.get_by_display_value('Matriz') if False else None
                page.locator('input[value="Matriz"]').wait_for()
                assert page.locator('.clientes-table tbody tr').count() == 2
                assert page.locator('.clientes-table input[type="password"]').count() == 0
                page.get_by_placeholder('Filtrar por CNPJ/CPF ou nome...').fill('Filial')
                assert page.locator('.clientes-table tbody tr').count() == 1
                page.get_by_role('button', name='Salvar', exact=True).click()
                page.wait_for_function("window.__calls.some(c => c.endpoint === '/clientes' && c.method === 'POST')")
                saved = page.evaluate("window.__calls.find(c => c.endpoint === '/clientes' && c.method === 'POST').body.clientes")
                assert len(saved) == 2
                assert all(c['tipo_login'] == 'certificado' and 'senha_nfse' not in c for c in saved)
                assert not errors, errors
                print('OK: executar por certificado, migrar matriz/filial, filtrar nome e salvar sem senha; renderer sem erros.')
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
