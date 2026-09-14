import assert from 'node:assert/strict';
import test from 'node:test';

let normalizacao = {};
try {
  normalizacao = await import('../api/clientes-normalizacao.js');
} catch {
  // O primeiro ciclo TDD deve falhar pelo comportamento ainda inexistente.
}

test('servidor preserva o tipo de acesso para documentos iguais', () => {
  const clientes = normalizacao.normalizeClientes?.([
    { documento: '12.345.678/0001-00', nome: 'Certificado', tipo_login: 'certificado' },
    { documento: '12345678000100', nome: 'Usuario', tipo_login: 'usuario_senha' },
  ]);
  assert.deepEqual(clientes?.map((c) => c.tipo_login), ['certificado', 'usuario_senha']);
});

test('servidor corrige tipo de acesso desconhecido para certificado', () => {
  const clientes = normalizacao.normalizeClientes?.([
    { documento: '12345678000100', nome: 'Cliente', tipo_login: 'desconhecido' },
  ]);
  assert.equal(clientes?.[0]?.tipo_login, 'certificado');
});

test('servidor remove repeticao apenas dentro do mesmo tipo de acesso', () => {
  const clientes = normalizacao.normalizeClientes?.([
    { documento: '12345678000100', nome: 'Cert 1', tipo_login: 'certificado' },
    { documento: '12345678000100', nome: 'Cert 2', tipo_login: 'certificado' },
    { documento: '12345678000100', nome: 'Usuario', tipo_login: 'usuario_senha' },
  ]);
  assert.deepEqual(
    clientes?.map((c) => [c.nome, c.tipo_login]),
    [['Cert 1', 'certificado'], ['Usuario', 'usuario_senha']],
  );
});

test('validacao rejeita documento invalido e repeticao na mesma aba', () => {
  assert.match(
    normalizacao.validarClientes?.([
      { documento: '123', nome: 'Cliente', tipo_login: 'certificado' },
    ]) || '',
    /11 ou 14 digitos/i,
  );
  assert.match(
    normalizacao.validarClientes?.([
      { documento: '12345678000100', nome: 'Cliente 1', tipo_login: 'certificado' },
      { documento: '12345678000100', nome: 'Cliente 2', tipo_login: 'certificado' },
    ]) || '',
    /repetido/i,
  );
  assert.equal(normalizacao.validarClientes?.([
    { documento: '12345678000100', nome: 'Cert', tipo_login: 'certificado' },
    { documento: '12345678000100', nome: 'Usuario', tipo_login: 'usuario_senha' },
  ]), null);
});

test('validacao rejeita payload que apagaria ou truncaria clientes', () => {
  assert.match(normalizacao.validarClientes?.({ cliente: 'invalido' }) || '', /lista/i);
  assert.match(normalizacao.validarClientes?.(null) || '', /lista/i);
  const muitos = Array.from({ length: 5001 }, (_, indice) => ({
    documento: String(indice).padStart(11, '0'),
    nome: `Cliente ${indice}`,
    tipo_login: 'certificado',
  }));
  assert.match(normalizacao.validarClientes?.(muitos) || '', /5000/);
});
