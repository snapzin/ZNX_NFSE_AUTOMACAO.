import test from 'node:test';
import assert from 'node:assert/strict';
import { clientesPorCertificado } from '../renderer/src/clientes-identidade.mjs';

test('migra cadastros legados sem perder filial e sem enviar senhas', () => {
  const result = clientesPorCertificado([
    { documento: '12345678000100', nome: 'Legado', tipo_login: 'usuario_senha', senha_nfse: 'segredo', ativo: true },
    { documento: '12.345.678/0001-00', nome: 'Matriz', tipo_login: 'certificado', ativo: false },
    { documento: '12345678000200', nome: 'Filial', tipo_login: 'usuario_senha', ativo: false },
  ]);
  assert.deepEqual(result, [
    { documento: '12345678000100', nome: 'Matriz', tipo_login: 'certificado', ativo: true },
    { documento: '12345678000200', nome: 'Filial', tipo_login: 'certificado', ativo: false },
  ]);
});
