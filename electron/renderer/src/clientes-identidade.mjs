export const normalizarDocumento = (valor) => String(valor || '').replace(/\D/g, '');

export const normalizarTipoLogin = (valor) => (
  valor === 'usuario_senha' ? 'usuario_senha' : 'certificado'
);

export const chaveCliente = (cliente) => (
  `${normalizarTipoLogin(cliente?.tipo_login)}:${normalizarDocumento(cliente?.documento)}`
);

export const temClienteDuplicado = (clientes) => {
  const chaves = (clientes || []).map(chaveCliente);
  return new Set(chaves).size !== chaves.length;
};
