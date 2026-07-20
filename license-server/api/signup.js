// Autoatendimento: escritorio de contabilidade se cadastra e paga via PIX
// direto na nossa pagina (sem redirecionar pra fora do site). Gera a 1a
// cobranca PIX no Itau e devolve o codigo "copia e cola" na propria
// resposta. A LICENCA so nasce quando o webhook (itau-webhook/[token]/pix.js)
// confirmar o pagamento — aqui so guardamos os dados do cadastro em
// "pending", ligados ao txid da cobranca.
import QRCode from 'qrcode';
import {
  setCors, setSecurityHeaders, rateLimit, clientIp, savePendingCharge,
} from './_lib.js';
import { criarCobranca, gerarTxid } from './itau-client.js';
import { PLANOS } from './planos.js';

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
  const planoKey = String(body.plano || '').trim().toLowerCase();
  const plano = PLANOS[planoKey];

  if (!clientName) {
    return res.status(400).json({ ok: false, message: 'Informe o nome do escritorio.' });
  }
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ ok: false, message: 'E-mail invalido.' });
  }
  if (cnpj.length !== 14 && cnpj.length !== 11) {
    return res.status(400).json({ ok: false, message: 'CNPJ/CPF invalido.' });
  }
  if (!plano) {
    return res.status(400).json({ ok: false, message: `Plano invalido. Opcoes: ${Object.keys(PLANOS).join(', ')}` });
  }

  try {
    const txid = gerarTxid();
    const cobranca = await criarCobranca({
      txid,
      valor: plano.valor,
      cpfCnpj: cnpj,
      nome: clientName,
      expiracaoSegundos: 3600, // 1h pra pagar a 1a cobranca
    });

    await savePendingCharge(txid, {
      clientName,
      email,
      cpfCnpj: cnpj,
      maxMachines: plano.maxMachines,
      plano: planoKey,
      notes: `Signup autoatendimento (CNPJ/CPF ${cnpj})`,
      createdAt: new Date().toISOString(),
    });

    // QR code e so um "atalho pra escanear" o mesmo copia-e-cola — se a
    // geracao da imagem falhar por qualquer motivo, o pagamento continua
    // possivel colando o codigo, entao isso nunca derruba o signup.
    let qrCodeDataUrl = '';
    try {
      qrCodeDataUrl = await QRCode.toDataURL(cobranca.pixCopiaECola, { margin: 1, width: 240 });
    } catch (err) {
      console.warn('signup: falha ao gerar QR code (seguindo so com copia-e-cola):', err?.message);
    }

    return res.status(200).json({
      ok: true,
      txid,
      pixCopiaECola: cobranca.pixCopiaECola,
      qrCodeDataUrl,
      valor: plano.valor,
      message: 'Pague o PIX abaixo para ativar sua licenca — a chave chega por e-mail assim que confirmar.',
    });
  } catch (err) {
    console.error('signup error:', err?.message);
    return res.status(502).json({ ok: false, message: `Falha ao gerar cobranca PIX: ${err?.message || err}` });
  }
}
