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

export const clientesPorCertificado = (clientes) => {
  const unicos = new Map();
  const preferidos = new Set();
  for (const c of clientes || []) {
    const documento = normalizarDocumento(c.documento);
    const nome = String(c.nome || documento).trim();
    const ativo = typeof c.ativo === 'string'
      ? !['nao', 'não', 'false', '0', 'n', ''].includes(c.ativo.trim().toLowerCase())
      : c.ativo !== false;
    const certificado = (c.tipo_login || 'certificado') === 'certificado';
    const anterior = unicos.get(documento);
    if (anterior) {
      anterior.ativo ||= ativo;
      if (certificado && !preferidos.has(documento)) anterior.nome = nome;
    } else {
      unicos.set(documento, { documento, nome, ativo, tipo_login: 'certificado' });
    }
    if (certificado) preferidos.add(documento);
  }
  return [...unicos.values()];
};
