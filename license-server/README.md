# Servidor de Licenças — TáBatido (Vercel + Upstash Redis)

Produto ZNX. Valida as licenças do app, oferece um **painel admin** para
gerenciar clientes (criar/revogar chaves, limite de máquinas, validade, ver
quem está ativo) e uma camada de **cobrança via PIX direto no Itaú** (API PIX
Cobrança, padrão Banco Central) com dois caminhos de entrada de cliente:
venda manual (você cria a licença no painel) e autoatendimento (escritório se
cadastra e paga via PIX sozinho em `/assinar.html`).

- `GET /` → site de vendas (landing page pública, preços, CTA)
- `GET /admin.html` → painel admin (protegido por usuario/senha)
- `GET /assinar.html` → página pública de autoatendimento (gera a cobrança PIX
  e mostra o "copia e cola" na hora, sem redirecionar); aceita `?plano=basico|pro`
  pra vir pré-selecionado a partir da landing page
- `POST /api/validate` → usado pelo app (`{key, machine_id}` → `{valid, message}`)
- `POST /api/clientes` → clientes salvos por licenca + maquina (`op: "get"|"save"`)
- `POST /api/admin` → API de gerenciamento (headers `x-admin-user` e `x-admin-pass`)
- `POST /api/signup` → autoatendimento: gera a 1ª cobrança PIX no Itaú e
  devolve o "copia e cola" (chamado pela página `/assinar.html`)
- `POST /api/itau-webhook/<ITAU_WEBHOOK_TOKEN>` → recebido do Itaú quando um
  PIX é recebido; cria/renova a licença automaticamente (ver seção abaixo)
- `GET /api/billing-run` → disparado pelo Cron da Vercel todo dia; gera
  cobranças PIX de renovação pra quem está perto de vencer

## Cobrança via PIX direto (Itaú)

> ⚠️ A implementação segue o padrão oficial do Banco Central para PIX
> Cobrança (`PUT /pix/v2/cob/{txid}`, `GET /pix/v2/cob/{txid}`), que a
> maioria dos bancos — Itaú incluso — segue de perto. **Confirme no Itaú
> Developers** se a base URL/paths exatos batem com o que foi liberado pra
> sua conta antes de ir pra produção; ajuste via `ITAU_API_BASE_URL` /
> `ITAU_TOKEN_PATH` se precisar.

1. No Itaú Developers, confirme o acesso liberado ao produto **PIX Cobrança**
   (client_id/secret + certificado mTLS aprovados) e a sua **chave PIX**
   recebedora.
2. Configure as variáveis de ambiente na Vercel:

   | Variável | Valor |
   |---|---|
   | `ITAU_API_BASE_URL` | base da API (sandbox ou produção, conforme o Itaú Developers) |
   | `ITAU_TOKEN_PATH` | (opcional) path do endpoint de token OAuth2; default `/api/oauth/token` |
   | `ITAU_CLIENT_ID` / `ITAU_CLIENT_SECRET` | credenciais OAuth2 (client_credentials) |
   | `ITAU_CERT_PEM` / `ITAU_KEY_PEM` | certificado + chave privada mTLS, em PEM (cole com `\n` escapado) |
   | `ITAU_PIX_KEY` | a chave PIX que recebe os pagamentos |
   | `ITAU_WEBHOOK_TOKEN` | token que você define; vira parte da própria URL do webhook (ver abaixo) |
   | `CRON_SECRET` | token que a Vercel usa sozinha pra autenticar o disparo do Cron — só defina, não precisa configurar em lugar nenhum |
   | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` | (opcional) para enviar a chave/cobrança por e-mail |
   | `INSTALLER_URL` | (opcional) link do instalador, incluído no e-mail |

3. No Itaú Developers, registre o webhook de notificação de PIX apontando
   para:
   ```
   https://SEU-PROJETO.vercel.app/api/itau-webhook/<ITAU_WEBHOOK_TOKEN>
   ```
   (o token fica na própria URL — o padrão PIX do Bacen não define um
   esquema de assinatura de payload tipo Stripe/Asaas). **Nota de segurança**:
   por ficar na URL, esse token tende a aparecer em logs de acesso (da
   própria Vercel, de qualquer proxy/CDN na frente) — mais exposto que um
   header. É a única opção viável dado o padrão PIX; trate os logs de
   acesso do projeto como sensíveis por causa disso.
4. Os planos (nome, preço, limite de máquinas) ficam definidos em
   `api/planos.js` (`PLANOS`) — usado tanto pelo signup quanto pela
   renovação automática; o valor cobrado nunca vem do formulário do cliente.
5. Fluxo (1ª cobrança): `assinar.html` → `POST /api/signup` (gera uma
   cobrança PIX via `api/itau-client.js`, guarda um registro "pending"
   ligado ao `txid`) → cliente paga colando o código no app do banco → Itaú
   chama `POST /api/itau-webhook/<token>` → a licença nasce de verdade.
6. Fluxo (renovação mensal): o Cron (`GET /api/billing-run`, todo dia às
   12h UTC) varre licenças com `billingProvider: "itau_pix"` e, pra quem
   está a **5 dias ou menos** do vencimento sem cobrança pendente ainda
   válida, gera uma nova cobrança PIX e manda por e-mail. Se passar do
   vencimento sem pagar, marca `billingStatus: "atrasado"`; depois de
   **~10 dias** de atraso (a checagem roda 1x/dia, então o corte exato varia
   um pouco conforme o horário do vencimento), bloqueia a licença
   (`blockedReason: "billing"` — só esse bloqueio é revertido
   automaticamente por um pagamento; bloqueio manual do admin nunca é
   revertido pelo webhook/cron). Todas as políticas (dias de antecedência,
   folga de atraso, validade da cobrança) ficam centralizadas em
   `api/planos.js` (`BILLING_POLICY`).
7. Venda manual (fora do autoatendimento): continue usando `op: "create"` no
   painel admin normalmente — essas licenças ficam com `billingStatus:
   "manual"` e nunca são tocadas pelo webhook/cron.

---

## 1. Criar o banco (Upstash Redis — grátis)

No painel do **Vercel** → seu projeto → aba **Storage** → **Create Database** →
**Upstash for Redis** → plano **Free**. Ao conectar ao projeto, o Vercel cria
automaticamente as variáveis:

```
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
```

(Alternativa: criar em upstash.com e copiar as duas variáveis manualmente.)

## 2. Variáveis de ambiente (Vercel → Settings → Environment Variables)

| Variável | Valor |
|---|---|
| `ADMIN_USER` | usuario do painel admin |
| `ADMIN_PASS` | senha forte do painel admin |
| `ADMIN_TOKEN` | legado/opcional: ainda aceito por compatibilidade com integracoes antigas |
| `CLIENT_SECRET` | **mesmo** valor de `DEFAULT_CLIENT_SECRET` em `electron/license-client.js` |
| `VALID_KEYS` | (opcional) chaves antigas separadas por vírgula |
| `ALLOW_LEGACY_KEYS` | defina `1` apenas se precisar aceitar `VALID_KEYS` sem vínculo de máquina |
| `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` | criadas no passo 1 |

## 3. Deploy

```bash
cd license-server
npm install
vercel --prod
```

O app aponta por padrão para:

```
https://license-server-sigma-topaz.vercel.app
```

O Electron envia esta configuração para o backend Python via variáveis de
ambiente:

```
NFSE_LICENSE_SERVER_URL
NFSE_LICENSE_VALIDATE_URL
NFSE_LICENSE_ADMIN_URL
NFSE_CLIENTES_URL
NFSE_CLIENT_SECRET
```

Para trocar de domínio, altere `DEFAULT_LICENSE_SERVER_URL` em
`electron/license-client.js` ou defina `NFSE_LICENSE_SERVER_URL` no ambiente
antes de iniciar o app. O backend Python (`api/license.py`) usa a mesma
configuração recebida do Electron.

## 4. Usar o painel

Abra `https://SEU-PROJETO.vercel.app/admin.html` → informe usuario e senha →
crie licenças (gera a chave e copia), defina máquinas/validade, bloqueie/exclua.
(A raiz `/` agora é a landing page pública de vendas — o painel administrativo
mudou para `/admin.html`.)

O usuario definido em `ADMIN_USER` / `ADMIN_PASS` na Vercel funciona como acesso
root/de recuperacao. Pelo painel, use a secao **Usuarios admin** para criar
outros usuarios; eles ficam salvos no Redis com senha hashada.

---

## Como funciona a validação

- Chave **existe no banco**: confere `status` (ativa/bloqueada), `validade` e
  **vínculo de máquina** (registra até `maxMachines` máquinas ativas; além disso, recusa).
  Máquina bloqueada não se recadastra automaticamente. Atualiza `lastSeen` a cada verificação.
- Chave **não está no banco** mas está em `VALID_KEYS`: só aceita se `ALLOW_LEGACY_KEYS=1`.
- Banco indisponível: só cai no `VALID_KEYS` se `ALLOW_LEGACY_KEYS=1`.

## Modelo de dados (Redis)

- `lic:index` (set) → todas as chaves
- `lic:<CHAVE>` (json) → `{ clientName, status, maxMachines, machines, expiresAt,
  createdAt, lastSeen, notes, blockedReason?, plano, billingStatus,
  billingProvider, itauPayerDoc, email, pendingChargeTxid?, pendingChargeAt?,
  lastPaidTxid?, lastPaidAt? }`
- `clientes:<HASH_CHAVE>:<MACHINE_ID>` (json) → `{ clientes, updatedAt }`
- `itau:txid:<TXID>` (string) → chave da licença (índice txid → licença)
- `itau:pending:<TXID>` (json) → dados do cadastro aguardando confirmação do 1º pagamento
- `itau:event:<EVENT_ID>` (string, TTL 7 dias) → dedupe de notificações reentregues pelo Itaú
