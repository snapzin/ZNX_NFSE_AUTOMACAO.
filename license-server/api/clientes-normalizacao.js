export const MAX_CLIENTES = 5000;

export function normalizeTipoLogin(value) {
  return value === 'usuario_senha' ? 'usuario_senha' : 'certificado';
}

export function validarClientes(items) {
  if (!Array.isArray(items)) return 'Clientes deve ser uma lista.';
  if (items.length > MAX_CLIENTES) {
    return `O limite e de ${MAX_CLIENTES} clientes por maquina.`;
  }
  const vistos = new Set();
  let indice = 0;
  for (const item of items) {
    indice += 1;
    if (!item || typeof item !== 'object') return `Cliente ${indice} possui formato invalido.`;
    const documento = String(item.documento || '').replace(/\D/g, '');
    const nome = String(item.nome || '').trim();
    const tipoLogin = normalizeTipoLogin(item.tipo_login);
    if (![11, 14].includes(documento.length)) {
      return `Cliente ${indice}: CPF/CNPJ deve possuir 11 ou 14 digitos.`;
    }
    if (!nome) return `Cliente ${indice}: nome e obrigatorio.`;
    const chave = `${tipoLogin}:${documento}`;
    if (vistos.has(chave)) return 'CPF/CNPJ repetido no mesmo tipo de acesso.';
    vistos.add(chave);
  }
  return null;
}

export function normalizeClientes(items) {
  const out = [];
  const vistos = new Set();
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item !== 'object') continue;
    const documento = String(item.documento || '').replace(/\D/g, '').slice(0, 32);
    const nome = String(item.nome || '').trim().slice(0, 200);
    const ativo = item.ativo === undefined ? true : Boolean(item.ativo);
    const tipo_login = normalizeTipoLogin(item.tipo_login);
    const chave = `${tipo_login}:${documento}`;
    if ((documento || nome) && !vistos.has(chave)) {
      vistos.add(chave);
      out.push({ documento, nome, ativo, tipo_login });
    }
    if (out.length >= MAX_CLIENTES) break;
  }
  return out;
}
