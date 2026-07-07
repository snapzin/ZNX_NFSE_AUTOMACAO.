// Autoatendimento: escritorio de contabilidade se cadastra e assina sozinho.
// Cria customer + subscription na Asaas e devolve o link de pagamento
// (boleto/PIX/cartao, conforme o proprio Asaas oferece na fatura). A LICENCA
// so nasce quando o webhook (asaas-webhook.js) confirmar o 1o pagamento —
// aqui so guardamos os dados do cadastro em "pending", ligados ao subscription id.
import {
  setCors, setSecurityHeaders, rateLimit, clientIp, savePendingSignup,
} from './_lib.js';
import { createCustomer, createSubscription, getFirstPayment } from './asaas-client.js';

// Planos definidos no servidor (nao confiar em valor vindo do cliente).
// Ajuste os precos/limites conforme a estrategia comercial.
const PLANOS = {
  basico: { label: 'Basico', valor: 149.9, maxMachines: 1 },
  pro: { label: 'Pro', valor: 249.9, maxMachines: 3 },
};

// Metodo de cobranca aceito no checkout. Comeca so em PIX (cartao exige
// documentacao adicional aprovada na Asaas). Quando a conta Asaas estiver
// habilitada pra cartao, mude ASAAS_BILLING_TYPE=UNDEFINED na Vercel (sem
// precisar mexer em codigo) para liberar boleto/cartao tambem.
const BILLING_TYPE = String(process.env.ASAAS_BILLING_TYPE || 'PIX').trim().toUpperCase();

function apenasDigitos(v) {
  return String(v || '').replace(/\D/g, '');
}

export default async function handler(req, res) {
  setSecurityHeaders(res);
  setCors(req, res, 'POST, OPTIONS');
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, message: 'Metodo nao permitido' });
  }

  if (await rateLimit(`signup:ip:${clientIp(req)}`, 10, 300)) {
    return res.status(429).json({ ok: false, message: 'Muitas tentativas. Tente novamente mais tarde.' });
  }

  const body = req.body || {};
  const clientName = String(body.clientName || '').trim().slice(0, 200);
  const cnpj = apenasDigitos(body.cnpj);
  const email = String(body.email || '').trim().slice(0, 200);
  const phone = apenasDigitos(body.phone);
  const planoKey = String(body.plano || '').trim().toLowerCase();
  const plano = PLANOS[planoKey];

  if (!clientName || !email) {
    return res.status(400).json({ ok: false, message: 'Informe ao menos o nome do escritorio e o e-mail.' });
  }
  if (cnpj.length !== 14 && cnpj.length !== 11) {
    return res.status(400).json({ ok: false, message: 'CNPJ/CPF invalido.' });
  }
  if (!plano) {
    return res.status(400).json({ ok: false, message: `Plano invalido. Opcoes: ${Object.keys(PLANOS).join(', ')}` });
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ ok: false, message: 'E-mail invalido.' });
  }

  try {
    const customer = await createCustomer({ name: clientName, cpfCnpj: cnpj, email, phone });
    const subscription = await createSubscription({
      customerId: customer.id,
      value: plano.valor,
      cycle: 'MONTHLY',
      billingType: BILLING_TYPE,
      description: `TaBatido — plano ${plano.label}`,
    });

    await savePendingSignup(subscription.id, {
      clientName,
      email,
      maxMachines: plano.maxMachines,
      plano: planoKey,
      asaasCustomerId: customer.id,
      notes: `Signup autoatendimento (CNPJ/CPF ${cnpj})`,
      createdAt: new Date().toISOString(),
    });

    let invoiceUrl = subscription.invoiceUrl || '';
    if (!invoiceUrl) {
      const primeiraCobranca = await getFirstPayment(subscription.id).catch(() => null);
      invoiceUrl = primeiraCobranca?.invoiceUrl || '';
    }

    return res.status(200).json({
      ok: true,
      subscriptionId: subscription.id,
      invoiceUrl,
      message: 'Cadastro criado. Conclua o pagamento para ativar sua licenca.',
    });
  } catch (err) {
    console.error('signup error:', err?.message);
    return res.status(502).json({ ok: false, message: `Falha ao criar assinatura na Asaas: ${err?.message || err}` });
  }
}
