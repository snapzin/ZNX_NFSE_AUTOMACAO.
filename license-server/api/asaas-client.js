// Wrapper fino da API REST da Asaas (https://docs.asaas.com/reference).
// Usado pelo signup (autoatendimento) para criar customer + subscription.
// A confirmacao de pagamento chega depois via webhook (ver asaas-webhook.js).
const BASE_URL = {
  sandbox: 'https://sandbox.asaas.com/api/v3',
  production: 'https://api.asaas.com/v3',
};

function baseUrl() {
  const env = String(process.env.ASAAS_ENV || 'sandbox').trim().toLowerCase();
  return BASE_URL[env] || BASE_URL.sandbox;
}

function apiKey() {
  const key = String(process.env.ASAAS_API_KEY || '').trim();
  if (!key) throw new Error('ASAAS_API_KEY nao configurada.');
  return key;
}

async function request(path, options = {}) {
  const resp = await fetch(`${baseUrl()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      access_token: apiKey(),
      ...(options.headers || {}),
    },
  });
  let data = {};
  try { data = await resp.json(); } catch { data = {}; }
  if (!resp.ok) {
    const msg = data?.errors?.[0]?.description || data?.message || `Asaas respondeu HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

// { name, cpfCnpj, email, phone } -> { id, ... }
export async function createCustomer({ name, cpfCnpj, email, phone }) {
  return request('/customers', {
    method: 'POST',
    body: JSON.stringify({
      name: String(name || '').trim(),
      cpfCnpj: String(cpfCnpj || '').replace(/\D/g, ''),
      email: String(email || '').trim(),
      phone: String(phone || '').replace(/\D/g, '') || undefined,
    }),
  });
}

// { customerId, value, cycle, billingType, description } -> { id, invoiceUrl?, ... }
// billingType 'UNDEFINED' deixa o cliente escolher boleto/PIX/cartao na fatura da Asaas.
export async function createSubscription({
  customerId,
  value,
  cycle = 'MONTHLY',
  billingType = 'UNDEFINED',
  description = '',
}) {
  return request('/subscriptions', {
    method: 'POST',
    body: JSON.stringify({
      customer: customerId,
      billingType,
      value: Number(value),
      cycle,
      description,
      nextDueDate: new Date().toISOString().slice(0, 10),
    }),
  });
}

// Busca a 1a cobranca (payment) de uma subscription, para pegar o link de
// pagamento (invoiceUrl) a mostrar ao escritorio logo apos o signup.
export async function getFirstPayment(subscriptionId) {
  const data = await request(`/payments?subscription=${encodeURIComponent(subscriptionId)}&limit=1`);
  return (data.data && data.data[0]) || null;
}
