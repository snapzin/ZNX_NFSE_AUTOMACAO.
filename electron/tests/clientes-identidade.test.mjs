import assert from 'node:assert/strict';
import test from 'node:test';

let identidade = {};
try {
  identidade = await import('../renderer/src/clientes-identidade.mjs');
} catch {
  // O primeiro ciclo TDD deve falhar pelo comportamento ainda inexistente.
}

test('aceita o mesmo documento uma vez em cada tipo de acesso', () => {
  const duplicado = identidade.temClienteDuplicado?.([
    { documento: '12.345.678/0001-00', tipo_login: 'certificado' },
    { documento: '12345678000100', tipo_login: 'usuario_senha' },
  ]);
  assert.equal(duplicado, false);
});

test('rejeita o mesmo documento repetido dentro do mesmo tipo de acesso', () => {
  const duplicado = identidade.temClienteDuplicado?.([
    { documento: '12.345.678/0001-00', tipo_login: 'certificado' },
    { documento: '12345678000100', tipo_login: 'certificado' },
  ]);
  assert.equal(duplicado, true);
});

test('considera matriz e filial documentos distintos pelo CNPJ completo', () => {
  const duplicado = identidade.temClienteDuplicado?.([
    { documento: '12.345.678/0001-00', tipo_login: 'certificado' },
    { documento: '12.345.678/0002-90', tipo_login: 'certificado' },
  ]);
  assert.equal(duplicado, false);
});
