// Cliente mTLS + OAuth2 da API "Recebimentos Pix" do Itau (confirmado no
// Itau Developers, produto regulatorio-pix-v2):
//   producao: https://pix-pj.api.itau.com/regulatorio-pix/v2
//   sandbox:  https://sandbox.devportal.itau.com.br/itau-ep9-api-regulatorio-pix-v2-externo/v2
// Os paths abaixo (/cob, /cob/{txid}) sao relativos a essa base — ela ja
// termina em '/v2', entao NAO prefixe com '/pix/v2' de novo.
//
// Toda chamada (inclusive o token) usa o certificado mTLS — bancos que
// seguem o padrao PIX do Bacen exigem isso em todo o fluxo, nao so no login.
//
// Variaveis de ambiente:
//   ITAU_API_BASE_URL   - uma das duas base URLs acima (sandbox ou producao)
//   ITAU_TOKEN_PATH      - path do endpoint de token — AINDA NAO CONFIRMADO
//                           contra a doc real (ver "como comecar" no Itau
//                           Developers); default abaixo e um palpite, ajuste
//                           se o login falhar
//   ITAU_CLIENT_ID / ITAU_CLIENT_SECRET
//   ITAU_CERT_PEM / ITAU_KEY_PEM  - certificado + chave privada em PEM (cole
//                                    o conteudo com quebras de linha como
//                                    "\n" escapado na env var da Vercel)
//   ITAU_PIX_KEY         - a chave PIX que recebe os pagamentos
import https from 'node:https';

const BASE_URL = String(process.env.ITAU_API_BASE_URL || '').replace(/\/+$/, '');
const TOKEN_PATH = String(process.env.ITAU_TOKEN_PATH || '/api/oauth/token');

function pem(envVar) {
  const raw = process.env[envVar] || '';
  if (!raw) throw new Error(`${envVar} nao configurada (necessaria para mTLS).`);
  return raw.includes('\\n') ? raw.replace(/\\n/g, '\n') : raw;
}

function httpsJson({ method, path, headers = {}, body, form }) {
  return new Promise((resolve, reject) => {
    if (!BASE_URL) return reject(new Error('ITAU_API_BASE_URL nao configurada.'));
    let base;
    try {
      base = new URL(BASE_URL);
    } catch (err) {
      return reject(new Error(`ITAU_API_BASE_URL invalida: ${err.message}`));
    }
    // Concatena 'path' depois do pathname da base MANUALMENTE — 'new URL(path,
    // base)' descartaria qualquer sub-path que a base tenha, ja que 'path'
    // sempre comeca com '/' (regra do WHATWG URL que pegaria isso de surpresa
    // se a base for algo como 'https://host/pix-cob-gtw').
    const fullPath = `${base.pathname.replace(/\/+$/, '')}${path}`;
    const payload = form
      ? new URLSearchParams(form).toString()
      : (body !== undefined ? JSON.stringify(body) : undefined);

    const req = https.request({
      hostname: base.hostname,
      port: base.port || 443,
      path: fullPath,
      method,
      cert: pem('ITAU_CERT_PEM'),
      key: pem('ITAU_KEY_PEM'),
      headers: {
        'Content-Type': form ? 'application/x-www-form-urlencoded' : 'application/json',
        ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
        ...headers,
      },
    }, (res) => {
      let raw = '';
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        let data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch { data = { raw }; }
        if (res.statusCode >= 200 && res.statusCode < 300) return resolve(data);
        reject(new Error(
          data?.message || data?.error_description || data?.detail
            || `Itau HTTP ${res.statusCode}: ${raw.slice(0, 300)}`,
        ));
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

// Cache o token entre invocacoes "mornas" da funcao serverless — evita
// reautenticar (handshake mTLS + OAuth) a cada chamada.
let _token = null; // { value, expiresAt }

export async function getAccessToken() {
  if (_token && _token.expiresAt > Date.now() + 5000) return _token.value;

  const clientId = process.env.ITAU_CLIENT_ID || '';
  const clientSecret = process.env.ITAU_CLIENT_SECRET || '';
  if (!clientId || !clientSecret) {
    throw new Error('ITAU_CLIENT_ID/ITAU_CLIENT_SECRET nao configurados.');
  }

  const data = await httpsJson({
    method: 'POST',
    path: TOKEN_PATH,
    form: { grant_type: 'client_credentials', client_id: clientId, client_secret: clientSecret },
  });

  _token = {
    value: data.access_token,
    // Renova 30s antes de expirar de verdade, com um piso de 30s de folga.
    expiresAt: Date.now() + Math.max(30, Number(data.expires_in || 300) - 30) * 1000,
  };
  return _token.value;
}

async function authedRequest(opts) {
  const token = await getAccessToken();
  return httpsJson({ ...opts, headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) } });
}

// Cria (ou atualiza, se o txid ja existir e ainda nao tiver sido paga) uma
// cobranca PIX imediata. Retorna o objeto da cobranca (inclui
// 'pixCopiaECola' — o BR Code que o pagador cola no app do banco).
export async function criarCobranca({ txid, valor, cpfCnpj, nome, expiracaoSegundos = 3600 }) {
  const doc = String(cpfCnpj || '').replace(/\D/g, '');
  const devedor = doc.length === 14 ? { cnpj: doc, nome } : { cpf: doc, nome };

  const cobranca = await authedRequest({
    method: 'PUT',
    path: `/cob/${encodeURIComponent(txid)}`,
    body: {
      calendario: { expiracao: expiracaoSegundos },
      devedor,
      valor: { original: Number(valor).toFixed(2) },
      chave: String(process.env.ITAU_PIX_KEY || ''),
    },
  });

  // Falha alto e cedo se a resposta real da Itau nao trouxer o codigo PIX no
  // formato assumido — melhor um erro claro aqui do que um checkout/e-mail
  // "com sucesso" mas sem nenhum jeito de pagar.
  if (!cobranca?.pixCopiaECola) {
    console.error('Itau: resposta da cobranca sem pixCopiaECola. Payload recebido:', JSON.stringify(cobranca).slice(0, 500));
    throw new Error(
      'A resposta da Itau nao trouxe o codigo PIX (pixCopiaECola). Confira o '
      + 'formato real da resposta no Itau Developers e ajuste itau-client.js se o campo tiver outro nome.',
    );
  }
  return cobranca;
}

export async function consultarCobranca(txid) {
  return authedRequest({ method: 'GET', path: `/cob/${encodeURIComponent(txid)}` });
}

// txid: 26 a 35 caracteres alfanumericos (regra do Bacen para PIX Cobranca).
export function gerarTxid() {
  const alf = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let s = '';
  for (let i = 0; i < 32; i += 1) s += alf[Math.floor(Math.random() * alf.length)];
  return s;
}
