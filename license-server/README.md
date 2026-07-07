# Servidor de Licenças — TáBatido (Vercel + Upstash Redis)

Produto ZNX. Valida as licenças do app, oferece um **painel admin** para
gerenciar clientes (criar/revogar chaves, limite de máquinas, validade, ver
quem está ativo) e uma camada de **cobrança recorrente via Asaas** com dois
caminhos de entrada de cliente: venda manual (você cria a licença no painel)
e autoatendimento (escritório se cadastra e paga sozinho em `/assinar.html`).

- `GET /` → site de vendas (landing page pública, preços, CTA)
- `GET /admin.html` → painel admin (protegido por usuario/senha)
- `GET /assinar.html` → página pública de autoatendimento (signup + checkout Asaas); aceita `?plano=basico|pro` pra vir pré-selecionado a partir da landing page
- `POST /api/validate` → usado pelo app (`{key, machine_id}` → `{valid, message}`)
- `POST /api/clientes` → clientes salvos por licenca + maquina (`op: "get"|"save"`)
- `POST /api/admin` → API de gerenciamento (headers `x-admin-user` e `x-admin-pass`)
- `POST /api/signup` → autoatendimento: cria customer+subscription na Asaas e
  devolve o link de pagamento (chamado pela página `/assinar.html`)
- `POST /api/asaas-webhook` → recebido da Asaas quando um pagamento muda de
  status; cria/renova/bloqueia a licença automaticamente (ver seção abaixo)

## Cobrança recorrente (Asaas)

1. Crie uma conta na Asaas (sandbox para testar: https://sandbox.asaas.com) e
   gere uma API Key em Configurações → Integrações.
2. Configure as variáveis de ambiente na Vercel:

   | Variável | Valor |
   |---|---|
   | `ASAAS_API_KEY` | API key da Asaas (sandbox ou produção) |
   | `ASAAS_ENV` | `sandbox` ou `production` |
   | `ASAAS_WEBHOOK_TOKEN` | token que você define; deve ser o mesmo cadastrado no webhook da Asaas |
   | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` | (opcional) para enviar a chave por e-mail quando a licença é criada |
   | `INSTALLER_URL` | (opcional) link do instalador, incluído no e-mail |
   | `ASAAS_BILLING_TYPE` | (opcional) forma de cobrança do checkout. Padrão `PIX` — a conta Asaas normalmente libera cartão só depois da verificação de documentos; quando isso acontecer, mude para `UNDEFINED` (deixa o cliente escolher PIX/boleto/cartão na fatura) sem precisar mexer em código |

3. No painel da Asaas: Integrações → Webhooks → nova URL apontando para
   `https://SEU-PROJETO.vercel.app/api/asaas-webhook`, com o **Token de
   autenticação** igual a `ASAAS_WEBHOOK_TOKEN`. Marque pelo menos os eventos
   `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`, `PAYMENT_OVERDUE`,
   `SUBSCRIPTION_DELETED`, `PAYMENT_DELETED`, `PAYMENT_REFUNDED`.
4. Os planos (nome, preço, limite de máquinas) ficam definidos em
   `api/signup.js` (`PLANOS`) — o valor cobrado nunca vem do formulário do
   cliente, sempre do servidor.
5. Fluxo: `assinar.html` → `POST /api/signup` (cria customer+subscription na
   Asaas, guarda um registro "pending" ligado ao `subscriptionId`) → cliente
   paga no link da Asaas → Asaas chama `POST /api/asaas-webhook` →
   `PAYMENT_CONFIRMED`/`PAYMENT_RECEIVED` gera a licença de verdade (ou renova
   uma existente), `PAYMENT_OVERDUE` marca `billingStatus: "atrasado"` (sem
   bloquear — é uma folga), `SUBSCRIPTION_DELETED`/`PAYMENT_DELETED`/
   `PAYMENT_REFUNDED` bloqueia a licença.
6. Venda manual (fora do autoatendimento): continue usando `op: "create"` no
   painel admin normalmente — essas licenças ficam com `billingStatus:
   "manual"` e nunca são tocadas pelo webhook.

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

Abra `https://SEU-PROJETO.vercel.app/` → informe usuario e senha → 
crie licenças (gera a chave e copia), defina máquinas/validade, bloqueie/exclua.

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
- `lic:<CHAVE>` (json) → `{ clientName, status, maxMachines, machines, expiresAt, createdAt, lastSeen, notes }`
- `clientes:<HASH_CHAVE>:<MACHINE_ID>` (json) → `{ clientes, updatedAt }`
