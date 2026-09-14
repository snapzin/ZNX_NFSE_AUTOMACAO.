# Fluxo NFS-e por usuario e senha sem certificado

Data: 14/09/2026  
Status: aprovado em conversa; aguardando revisao final da especificacao

## Problema

O aplicativo apresenta a opcao `usuario_senha`, mas o fluxo atual nao e de
fato independente de certificado. Ele abre um navegador temporario, valida as
credenciais, fecha a sessao e depois tenta executar o download pela API ADN com
um arquivo `.pfx`. Alem disso, `executar_local` procura o certificado antes de
despachar o cliente para qualquer motor. O resultado e uma combinacao de erros
de login e mensagens como `Nenhum certificado .pfx encontrado` mesmo quando o
operador escolheu `Somente usuario e senha`.

O Portal Nacional permite autenticacao por CPF/CNPJ e senha no Emissor Web,
consulta de notas emitidas e recebidas e download individual de XML. A API ADN
de distribuicao, por outro lado, exige autenticacao mutua com certificado. Nao
ha uma API publica vigente para terceiros autenticarem a distribuicao com a
senha do portal.

## Objetivo

Criar um conector de portal completamente separado do conector ADN:

- `certificado` continua usando ADN/mTLS;
- `usuario_senha` usa somente usuario e senha no Portal Nacional;
- `ambos` encaminha cada cadastro ao conector correspondente;
- o fluxo por senha nunca procura `.pfx`, nunca recebe `CertificadoInfo` e
  nunca chama `adn_client`;
- a abertura do aplicativo e a verificacao inicial de caminhos tambem nao
  varrem `.pfx` quando certificados nao foram solicitados;
- login, consulta e download por senha usam a mesma sessao do navegador;
- CAPTCHA, quando apresentado pelo portal, e resolvido manualmente pelo
  operador, sem tentativa de contorno automatico.

## Restricoes e nao objetivos

- Nao sera criada nem simulada uma API privada de autenticacao por senha.
- Nao sera automatizada, terceirizada ou burlada a resolucao de CAPTCHA.
- O conector por senha nao promete operacao desassistida quando o portal
  exigir confirmacao humana.
- A extensao de terceiros `Baixar NFSe` nao sera dependencia de execucao. A
  versao atual da extensao usa certificado para XML em lote e o antigo fluxo
  do repositorio ficou obsoleto apos mudancas no painel da extensao.
- O motor ADN existente nao sera reescrito. Ele apenas recebera clientes do
  tipo correto.
- Os clientes continuam sendo processados um por vez, conforme definido pelo
  usuario.
- A empresa deve usar somente credenciais e documentos para os quais possua
  autorizacao de acesso.

## Arquitetura

```text
Clientes ativos + escolha da tela
              |
              v
      Despacho por tipo_login
       /                  \
certificado             usuario_senha
     |                        |
carregar/parear PFX      PortalSenhaRunner
     |                        |
  ADN/mTLS            Chrome visivel e isolado
                              |
                    login -> listar -> baixar XML
                              |
                      CAPTCHA assistido, se houver
```

O despacho acontece antes de qualquer leitura da pasta de certificados. Se a
execucao contiver apenas clientes por senha, `listar_certificados` e
`indexar_certificados_por_cnpj` nao serao chamados. No modo `ambos`, a pasta de
certificados sera lida uma unica vez e usada exclusivamente para o subconjunto
`certificado`.

A ordem original dos clientes sera preservada. Cada resultado continuara sendo
um `ResultadoCNPJ`, permitindo que relatorio, importacao no Dominio e interface
consumam ambos os conectores sem conhecer seus detalhes internos. Resultados do
portal terao `thumbprint` vazio e uma conferencia identificada com origem
`portal_usuario_senha`.

### Descoberta de caminhos e onboarding

`config_defaults.py`, `path_finder.py`, `/paths/status` e o onboarding deixarao
de considerar a pasta de certificados um requisito global. A pasta de saida
pode continuar sendo resolvida na abertura, mas a busca, contagem e sugestao de
`.pfx` serao lazy e executadas somente quando o operador escolher
`certificado` ou `ambos`, abrir explicitamente o diagnostico de certificados ou
solicitar essa configuracao.

O status de caminhos passara a distinguir `saida` obrigatoria de
`certificados` condicionais. Assim, uma instalacao destinada apenas a
usuario/senha pode abrir, cadastrar clientes e executar sem configurar ou
possuir uma pasta de certificados. Ao escolher um modo que contenha clientes
por certificado, a mesma interface solicitara a configuracao antes de iniciar.

## Componentes

### `app/automation/portal_auth.py`

Continuara responsavel apenas por credenciais e autenticacao. O login sera
fortalecido com os seletores atuais e escopados ao formulario:

- formulario `form[action*='/EmissorNacional/Login']`;
- usuario `#Inscricao` ou `input[name='Inscricao']`;
- senha `#Senha` ou `input[name='Senha'][type='password']`;
- envio pelo `button[type='submit']` do formulario.

Sucesso nao sera inferido apenas pela URL. Depois do POST, o codigo abrira uma
rota autenticada e exigira simultaneamente que a URL tenha saido de `/Login` e
que exista um marcador autenticado, como o menu de Emitidas/Recebidas ou a
tabela de consulta. Erros de validacao serao lidos de `.field-validation-error`,
`.validation-summary-errors` e `.alert-danger`, com normalizacao Unicode para
evitar o problema atual de textos corrompidos.

`login_usuario_senha(page, ...)` sera chamado pelo novo runner dentro da sessao
que permanecera aberta. `validar_login_portal` podera ser mantido apenas para
compatibilidade/testes, mas deixara de fazer parte da execucao principal.

### `app/automation/portal_download.py`

Novo modulo com responsabilidade unica pelo conector Web. A interface principal
sera equivalente a:

```python
PortalSenhaRunner(cancel_event).processar_cliente(cliente, params)
```

Ela nao aceitara certificado. O modulo sera dividido internamente em unidades
testaveis para:

1. abrir e fechar um `BrowserContext` isolado;
2. autenticar e confirmar a sessao;
3. listar notas e paginar;
4. baixar um XML individual;
5. detectar/aguardar CAPTCHA;
6. validar e persistir o XML atomicamente.

O retorno sera um objeto proprio contendo contagens, pastas, chaves listadas,
chaves confirmadas e falhas por nota. O orquestrador o convertera para
`ResultadoCNPJ`. Se alguma nota listada continuar sem XML valido depois dos
retries, o cliente sera marcado como erro para impedir um falso positivo, mas
os XMLs corretos ja obtidos e a conferencia parcial serao preservados.

O navegador sera visivel para permitir intervencao humana. A ordem de tentativa
para inicializacao sera: executavel configurado, canal Chrome configurado e,
como fallback no Windows, Microsoft Edge. Uma falha em todos os navegadores
produzira mensagem objetiva, sem expor credenciais.

Cada cliente recebera um contexto novo com cookies, cache e armazenamento
isolados. O contexto permanecera vivo desde o login ate o ultimo download e
sera fechado no `finally`, inclusive em cancelamento ou erro. Nao sera usado o
perfil da extensao nem um perfil pessoal do operador.

### `app/automation/nfse_automacao.py`

`executar_local` se tornara o orquestrador dos dois conectores. Ele:

- normaliza a lista de clientes sem colapsar documento + tipo de login;
- cria o indice de certificados somente se houver cliente `certificado`;
- encaminha certificado para `NFSeRunner`/ADN;
- encaminha usuario/senha para `PortalSenhaRunner` sem resolver PFX;
- mantem pasta, relatorios, importacao e resultado final comuns;
- aplica o intervalo do ADN somente entre chamadas ADN, nunca como requisito
  do fluxo de portal.

O log inicial de cada cliente passara a informar o tipo de acesso. Ele deixara
de dizer `buscando .pfx` para clientes por senha.

### `api/server.py` e interface Electron

O handler de logs preservara metadados estruturados opcionais da automacao. Os
eventos necessarios sao:

- `cliente_iniciado`;
- `nota_progresso`;
- `captcha_aguardando`;
- `captcha_resolvido`;
- `cliente_concluido`;
- `cliente_erro`.

A tela Executar continuara usando o job atual, mas mostrara a fase do cliente.
Durante CAPTCHA, exibira `Aguardando confirmacao humana`, manterá o botao
Cancelar ativo e mostrara uma notificacao orientando o operador a concluir a
verificacao na janela do Chrome. A senha nunca fara parte desses eventos.

### `app/reports/relatorio.py`

A conferencia passara a reconhecer a origem. Para ADN, permanecem os campos e
textos de NSU atuais. Para `portal_usuario_senha`, o relatorio mostrara total
listado no portal, XMLs validos confirmados, arquivos reaproveitados e chaves
faltantes. Ele nunca chamara uma conferencia de portal de `caixa ADN`.

Relatorios de conferencia poderao ser gerados para resultado parcial com erro,
mas a importacao no Dominio continuara restrita a clientes completos. Isso
preserva evidencia do que foi obtido sem importar um lote incompleto.

## Fluxo detalhado do portal

### 1. Preparacao

O runner cria `PASTA_SAIDA/<cliente>` e as subpastas solicitadas `Emitidas`
e/ou `Recebidas`. Se matriz e filial tiverem o mesmo nome, conserva-se a regra
existente de acrescentar o CNPJ completo para impedir mistura de arquivos.

### 2. Login

O navegador acessa `NFSE_LOGIN_URL`, preenche o usuario exatamente como
cadastrado e envia a senha. Credenciais invalidas encerram somente o cliente e
nao geram repeticoes automaticas, evitando bloqueio da conta. Uma sessao so e
considerada valida depois da verificacao composta em uma rota autenticada.

### 3. Consulta e paginacao

Para cada tipo solicitado, a navegacao usa as rotas oficiais configuradas para
Emitidas e Recebidas e o periodo `DD/MM/YYYY`. A consulta percorre a paginacao
ate nao existir proxima pagina. A implementacao usa marcadores estruturais da
tabela e links de acao, com seletores centralizados para que uma mudanca futura
do portal seja corrigida em um unico lugar.

Cada linha gera um registro minimo: chave de acesso, tipo, URL de XML e dados
disponiveis para diagnostico. A chave de acesso e a identidade da nota. Um
conjunto de chaves visitadas elimina duplicidades e um limite defensivo de
paginas/repeticao de URL impede loop infinito se a paginacao vier defeituosa.

### 4. Download individual

As notas sao baixadas sequencialmente pelo link oficial de XML da propria
linha. Antes da acao, o runner arma a captura de download e cria um destino
temporario exclusivo. Um HTTP 200 ou um evento de download, isoladamente, nao
significa sucesso: o conteudo sera validado antes de entrar na pasta final.

Se o portal devolver HTML com hCaptcha no lugar do XML, o runner:

1. emite `captcha_aguardando`;
2. traz a pagina correspondente para frente;
3. pausa sem prazo fixo, verificando periodicamente cancelamento;
4. deixa toda interacao do desafio para o operador;
5. aguarda o download valido; se o desafio for concluido sem iniciar o arquivo,
   repete apenas o link da nota atual;
6. emite `captcha_resolvido` e continua da mesma nota.

Se o portal exigir novo CAPTCHA em outra nota, o processo se repete. Nenhum
servico externo de resolucao ou manipulacao do desafio sera usado.

### 5. Validacao, CNPJ completo e idempotencia

O arquivo temporario so sera aceito quando:

- nao estiver vazio;
- nao for HTML ou pagina de CAPTCHA;
- puder ser analisado como XML;
- contiver uma NFS-e e uma chave coerente com a linha solicitada;
- corresponder ao documento completo do cliente no papel correto: prestador
  para Emitidas e tomador para Recebidas.

A comparacao de documento usa todos os 11 ou 14 digitos. Para CNPJ, matriz e
filial nunca serao consideradas iguais apenas pelos oito primeiros digitos.

O nome final sera derivado da chave, por exemplo `<chave>.xml`. Isso torna a
execucao idempotente: se um XML valido com a mesma chave ja existir, ele sera
validado e contabilizado sem novo download. Arquivos parciais usam extensao
temporaria e so entram no destino por renomeacao atomica apos validacao.

## Recuperacao de falhas

| Situacao | Comportamento |
| --- | --- |
| Usuario ou senha invalidos | Falha somente o cliente; sem repeticao cega |
| Timeout, HTTP 5xx ou falha transitoria | Ate 3 tentativas com espera crescente |
| Sessao expirada durante consulta/download | Refaz login uma vez e retoma a chave pendente |
| CAPTCHA | Pausa assistida ate resolver ou cancelar |
| Pagina sem notas | Sucesso com contagem zero |
| XML invalido/HTML | Descarta temporario e tenta novamente dentro do limite |
| Chave ou CNPJ divergente | Nunca salva; registra erro seguro da nota |
| Parte das notas falha | Preserva XMLs e conferencia, marca cliente incompleto |
| Seletor/layout alterado | Falha explicativa e diagnostico local sanitizado |
| Erro de um cliente | Registra resultado e segue para o proximo |
| Cancelamento | Interrompe espera/navegacao, fecha contexto e marca job cancelado |

Os retries se aplicam somente a falhas transitorias. Credencial invalida,
documento divergente e mudanca estrutural nao serao mascarados por repeticoes.

## Diagnosticos e privacidade

- Senha nao pode aparecer em logs, excecoes, URLs, nomes de arquivo,
  screenshots ou relatorios.
- Usuario e documento podem aparecer apenas onde ja sao necessarios para o
  operador identificar o cliente.
- Credenciais continuam locais na maquina e nao sao enviadas ao servidor de
  licencas ou ao GitHub.
- Em mudanca de layout, o app podera salvar screenshot e HTML somente depois
  de limpar campos de senha, tokens antiforgery, cookies e cabecalhos sensiveis.
- Mensagens ao usuario descrevem a acao necessaria; detalhes tecnicos ficam no
  modo Desenvolvedor.

## Testes

### Unidade e contrato local

- somente `usuario_senha`: prova que leitura/indexacao de certificados e ADN
  nao sao chamados;
- abertura/status de caminhos no uso por senha: prova que nao procura nem conta
  `.pfx` antes de uma acao explicita de certificado;
- somente `certificado`: preserva o comportamento ADN atual;
- `ambos`: cada linha usa exatamente seu conector e a ordem original;
- mesmo documento nos dois tipos: ambos sao processados sem colisao;
- matriz e filial: comparacao e pastas usam o CNPJ completo;
- login: seletores exatos, Unicode correto, credencial invalida e confirmacao
  composta de sessao;
- consulta: emitidas, recebidas, ambas, zero notas, varias paginas, pagina
  repetida e deduplicacao por chave;
- download: XML valido, HTML de CAPTCHA, arquivo vazio, chave divergente,
  documento divergente, arquivo ja existente e escrita atomica;
- recuperacao: falha transitoria, sessao expirada, limite de retry, CAPTCHA
  resolvido e cancelamento durante CAPTCHA;
- logs: eventos estruturados e ausencia da senha em mensagens/excecoes;
- relatorio: rotulos e conferencia distintos para ADN e portal, inclusive lote
  parcial; importacao bloqueada quando a conferencia do portal estiver
  incompleta.

Os testes de portal usarao paginas e downloads falsos minimos. Nenhuma
credencial real sera incluida no repositorio ou usada em CI.

### Interface, regressao e empacotamento

- parser de progresso exibe fase e CAPTCHA sem marcar o cliente como erro;
- botao Cancelar continua disponivel durante espera humana;
- suite Python existente permanece verde;
- testes Electron existentes e novos permanecem verdes;
- build do renderer e analise do backend PyInstaller incluem o novo modulo;
- smoke test inicia o backend empacotado e valida health/auth local.

### Validacao na empresa

Como esta maquina nao possui as credenciais reais nem representa o ambiente de
execucao, a entrega incluira um roteiro curto de smoke test no computador da
empresa: um cliente por senha, um periodo pequeno, Emitidas e Recebidas, com e
sem CAPTCHA. Caso o portal apresente uma variante que nao exista nos fixtures,
o diagnostico sanitizado devera indicar o seletor/estado sem revelar senha.

## Criterios de aceitacao

1. Abrir e executar `Somente usuario e senha` sem nenhum `.pfx` disponivel nao
   procura certificados, nao bloqueia o onboarding e chega ao portal.
2. Nenhuma funcao do ADN e chamada para um cliente `usuario_senha`.
3. A sessao do login permanece ativa ate terminar consultas e downloads.
4. Emitidas, Recebidas e Ambas respeitam o periodo selecionado e percorrem toda
   a paginacao.
5. Um CAPTCHA pausa a automacao, avisa o operador e retoma apos resolucao, sem
   qualquer tentativa de burla.
6. Somente XML valido, da chave e do documento completo esperados, e salvo.
7. Reexecutar o mesmo periodo nao duplica XMLs validos.
8. Matriz e filial permanecem separadas por CNPJ completo.
9. Falha em um cliente nao impede o processamento dos demais.
10. Cancelamento funciona durante navegacao, retry e espera de CAPTCHA.
11. Senhas nao aparecem em logs, relatorios, diagnosticos ou eventos da UI.
12. Uma coleta parcial e indicada como incompleta, preserva os arquivos validos
    e nao e importada silenciosamente no Dominio.
13. Suites Python/Electron e empacotamento permanecem funcionais.

## Referencias tecnicas verificadas

- [Guia vigente do Emissor Publico Nacional Web](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/guia-emissorpubliconacionalweb_snnfse-ern-v12.pdf):
  login por usuario/senha, consultas de Emitidas/Recebidas e acoes individuais
  de XML/PDF.
- [Manual vigente das APIs ADN para contribuintes](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/manual-contribuintes-apis-adn-sistema-nacional-nfse.pdf):
  distribuicao vinculada ao certificado da conexao.
- [Portal publico atual](https://www.nfse.gov.br/EmissorNacional/Login?ReturnUrl=%2FEmissorNacional):
  campos `Inscricao` e `Senha`, token antiforgery e rotas autenticadas com
  redirecionamento para Login.
- [Extensao na Chrome Web Store](https://chromewebstore.google.com/detail/baixar-nfse-nota-fiscal-d/enehmclajcndmgefbmjhecccoegbdgea?hl=pt-BR):
  seletores atuais da listagem e confirmacao de que o modo de XML em lote sem
  CAPTCHA usa certificado/ADN.
