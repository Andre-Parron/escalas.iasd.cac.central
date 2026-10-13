# ⛪ Escalas de Voluntários

Sistema de escalas para os ministérios **Mídia**, **Transmissão** e **Sonoplastia** (cultos de domingo, quarta e sábado; Transmissão só aos sábados).

- **Sem login/senha:** voluntário se identifica só pelo nome (cadastro rápido se for novo).
- **Líder:** entra pelo nome, que precisa estar na tabela `admins`; o servidor devolve uma chave de sessão (12 h).
- **Confirmar/recusar:** somente pelo link com token gerado ao escalar (`/#t=<token>`).
- Alerta de conflito de horário, sugestão automática, rodízio com limite mensal, relatórios (CSV / PDF pelo navegador), `.ics` e Google Calendar, modo claro/escuro.

**Stack:** Python 3.10+ · Flask · SQLite (local) ou Postgres (produção/Vercel) · HTML/CSS/JS puro (sem build).

## Rodar localmente
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
ADMINS="Pastor João,Maria Líder" python app.py        # http://localhost:8000
python -m unittest discover -s tests -v               # testes da API
```
Variáveis de ambiente: `DATABASE_URL` (Postgres; sem ela usa SQLite), `ADMINS` (líderes iniciais, separados por vírgula — só vale na 1ª execução, depois edite na aba **Líderes**), `DB_PATH` (arquivo SQLite), `PORT`, `DEBUG`.
Sem `ADMINS`, o líder inicial é **"Líder Demo"** — troque antes de publicar.

## Enviar para o GitHub
```bash
git init -b main
git add .
git commit -m "Escalas de voluntários: versão inicial"
git remote add origin https://github.com/SEU-USUARIO/escalas-igreja.git   # crie o repositório vazio antes
git push -u origin main
```
(ou, com a GitHub CLI: `gh repo create escalas-igreja --private --source=. --push`)

## Avisar os voluntários de graça: fila de WhatsApp de 1 toque
Não precisa de número novo nem de cadastro: usa o **seu** WhatsApp. No painel do líder, em **Escalas**, o botão **📲 Fila de envio (n)** abre uma janela com um voluntário por vez. Toque em **Abrir WhatsApp e enviar** (a mensagem e o link pessoal já vêm prontos), envie no WhatsApp e volte: aparece o próximo. Quem já recebeu fica marcado como "📲 enviado (manual)". Também existe o botão **💬 WhatsApp** em cada escala.

## Receber avisos no seu Telegram (grátis)
Um bot **só seu** avisa no Telegram quando um voluntário **aceitar**, **recusar** ou **pedir troca** (para mudar, use `TELEGRAM_EVENTOS`, ex.: `aceito` para receber só os aceites). O bot não envia nada aos voluntários.
1. No Telegram, converse com **@BotFather**, envie `/newbot`, escolha um nome e guarde o **token**.
2. Na Vercel (**Settings → Environment Variables**) crie `TELEGRAM_BOT_TOKEN` com o token e faça **Redeploy**.
3. Abra o seu bot no Telegram e envie `/start`.
4. No painel do líder, aba **🔑 Líderes → Avisos no Telegram**, toque em **Conectar meu Telegram** e depois em **Enviar teste**.

| Variável | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token do @BotFather (obrigatória) |
| `TELEGRAM_CHAT_ID` | (opcional) fixa o chat; sem ela vale o botão "Conectar" |
| `TELEGRAM_EVENTOS` | (opcional) padrão `aceito,recusado,troca` |

## (Opcional, pago e exige número) WhatsApp automático: Cloud API da Meta
Se um dia quiser envio 100% automático, o sistema pode enviar o aviso **direto** pelo WhatsApp, com o link de confirmação. No painel do líder aparece "📲 enviado" (ou o motivo da falha) em cada escala, o botão **📲 Enviar** (reenvio) e a opção de envio automático ao escalar. Sem as variáveis abaixo, o envio fica desligado e continua o link manual (`wa.me`).

**Passo a passo (uma vez):**
1. Em [developers.facebook.com](https://developers.facebook.com) crie um app do tipo *Business* e adicione o produto **WhatsApp**. Use um número **dedicado** da igreja (não pode estar logado no app WhatsApp comum).
2. No **WhatsApp Manager → Modelos de mensagem**, crie o modelo (categoria **Utilidade**, idioma **Português (BR)**) com o nome `escala_voluntario` e este corpo, e aguarde a aprovação:
   ```
   Olá {{1}}! Você foi escalado(a) na igreja: {{2}}, {{3}}. Para confirmar ou recusar sua presença, acesse: {{4}}

   Obrigado por servir! 🙏
   ```
   (`{{1}}` nome · `{{2}}` ministério - função · `{{3}}` dia e horário · `{{4}}` link pessoal.) O corpo não pode começar nem terminar com variável.
3. Gere um **token permanente**: *Configurações da empresa → Usuários do sistema* → crie um usuário, atribua o app e o número, gere o token com a permissão `whatsapp_business_messaging`. (O token temporário do painel expira em 24 h.)
4. Na Vercel (**Settings → Environment Variables**) crie:

| Variável | Valor |
|---|---|
| `WHATSAPP_TOKEN` | token permanente do passo 3 |
| `WHATSAPP_PHONE_ID` | **ID do número de telefone** (não é o número em si), em *WhatsApp → Configuração da API* |
| `PUBLIC_URL` | endereço do site, ex.: `https://escalas-igreja-iasd-central-cac.vercel.app` |
| `WHATSAPP_TEMPLATE` | (opcional) nome do modelo, padrão `escala_voluntario` |
| `WHATSAPP_LANG` | (opcional) padrão `pt_BR` |
| `WHATSAPP_API_VERSION` | (opcional) padrão `v23.0` |

5. Faça **Redeploy**. Teste escalando você mesmo (cadastre seu WhatsApp como voluntário).

**Bom saber:** a Meta cobra por mensagem de modelo enviada (confira a tabela de preços atual do Brasil). Enquanto o número estiver em modo de teste, só envia para números cadastrados como destinatários de teste no painel da Meta. Avise os voluntários que o número informado será usado para avisos de escala (exigência de consentimento do WhatsApp). Não há recebimento de respostas por WhatsApp: a confirmação continua pelo link.

## Hospedar na Vercel
A Vercel é *serverless*: **não guarda arquivos**, então o SQLite lá é apagado a qualquer momento. Use Postgres:
1. Importe o repositório do GitHub na Vercel (framework detectado: Flask) — ou faça deploy pelo Claude/CLI.
2. No projeto: **Storage → Create Database → Neon (Postgres)** e conecte ao projeto (isso cria `DATABASE_URL`/`POSTGRES_URL` sozinho).
3. Em **Settings → Environment Variables**, crie `ADMINS` (ex.: `Pastor João,Maria Líder`) e faça **Redeploy**.
4. Em **Settings → Deployment Protection**, deixe a *Vercel Authentication* desligada, senão os voluntários precisarão de conta Vercel.
Sem `DATABASE_URL`, o painel do líder mostra o aviso "Banco temporário". O código de Postgres usa SQL portável, mas foi testado só com SQLite: ao conectar o Neon, teste um fluxo completo (cadastro → escala → link do token).

## Hospedar (outras opções)
É um app Flask comum; precisa de **disco persistente** para o arquivo SQLite.
- **Render / Railway / Fly.io:** conecte o repositório; comando de start `gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app:app` (já no `Procfile`); monte um volume e aponte `DB_PATH` para ele (ex.: `/data/escalas.db`).
- **Docker:** `docker build -t escalas . && docker run -p 8000:8000 -v escalas-data:/data -e ADMINS="Pastor João" escalas`
- Use **HTTPS** (as plataformas acima já fornecem) e mantenha **1 worker** (limite de tentativas fica em memória; SQLite atende bem a uma igreja).
- Faça **backup** do arquivo `.db` de vez em quando.

## Estrutura
```
app.py            API + regras (validação, conflito, tokens, sessão de líder)
public/index.html interface (calendário, área do voluntário, painel do líder)
tests/            testes automatizados da API
```
**Personalizar:** ministérios/funções ficam em **dois lugares** — `MINISTERIOS` em `app.py` e `const M` em `public/index.html`. Horários padrão dos cultos: `HORA` em `public/index.html`.

## API (resumo)
| Rota | Quem | O que faz |
|---|---|---|
| `GET /api/state` | público / líder | Dados públicos (sem telefones/tokens); com `X-Admin-Key`, dados completos |
| `POST /api/state` | líder | Salva alterações (controle de versão → `409` se outro alterou antes) |
| `POST /api/admin/login` | — | `{nome}` → chave de sessão |
| `POST /api/vol` · `POST /api/aviso` | público | Auto-cadastro · pedido de troca |
| `GET/POST /api/t/<token>` | link pessoal | Ver / confirmar / recusar escala |

## Segurança (leia)
Identificação só por nome é prática, mas fraca: quem souber o nome de um líder entra no painel. O login tem limite de tentativas, mas se quiser mais segurança, o próximo passo natural é um PIN por líder. Os nomes dos líderes não ficam expostos no site; telefones e tokens só aparecem para o líder.

## Ideias para evoluir
Envio automático de WhatsApp/e-mail (API oficial do WhatsApp ou SMTP), PIN para líderes, PDF formatado no servidor, lembretes automáticos antes do culto.
