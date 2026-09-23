# Resolved merge marker.
# Disparo Bot Facebook (Python)

Painel local em Python 3 para cadastrar grupos autorizados, organizar anúncios e controlar uma fila de publicação. O projeto usa Flask, SQLite, HTML/CSS/JavaScript, `requests` e `python-dotenv`.

## Limite oficial da Meta

A Meta removeu `publish_to_groups`, `groups_access_member_info` e a Groups API no Graph API v19. A remoção passou a valer para todas as versões em 22/04/2024. Uma URL ou ID de grupo não concede autorização para publicar via API.

Por isso, este projeto não inventa endpoints, não usa Selenium/Playwright e não automatiza navegador. Para grupos, a fila prepara o texto, registra o histórico e permite concluir manualmente. A publicação real só seria adicionada quando a Meta documentar um método oficialmente suportado para o destino.

Fontes oficiais:

- https://developers.facebook.com/docs/graph-api/changelog/version19.0/
- https://developers.facebook.com/docs/graph-api/overview/
- https://developers.facebook.com/devpolicy/
- https://developers.facebook.com/terms/

## Requisitos no Windows

Instale Python 3.10 ou superior em https://www.python.org/downloads/ e marque **Add Python to PATH** durante a instalação.

Verifique no PowerShell:

```powershell
python --version
```

## Instalação

Abra o PowerShell dentro de `bot-facebook`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

O arquivo `.env` está no `.gitignore`. Nunca coloque senha do Facebook, cookies copiados ou tokens diretamente no código.

## Configuração

O padrão seguro é:

```env
DRY_RUN=true
PUBLISH_INTERVAL_SECONDS=5
```

Com `DRY_RUN=true`, nenhuma publicação real é enviada. A fila simula o grupo, anúncio, horário e status, e grava `Publicação simulada com sucesso` no SQLite.

`META_ACCESS_TOKEN` permanece vazio neste fluxo de grupos. Se um destino oficialmente suportado for adicionado no futuro, use somente tokens obtidos por login oficial e permissões documentadas pela Meta.

O bot Telegram é iniciado na mesma execução do painel. Configure `TELEGRAM_BOT_TOKEN` e `PAINEL_URL` no ambiente, instale as dependências e execute `python bot.py`. O comando `/start` responde com o primeiro nome fornecido pelo Telegram e um botão para abrir o painel.

## Executar

Com o ambiente virtual ativo:

```powershell
python bot.py
```

Abra http://localhost:5000.

Em outro terminal, com o mesmo ambiente virtual ativo:

```powershell
python telegram_bot.py
```

A primeira execução cria automaticamente `database/bot.db`.

## Como usar

1. Em **Grupos**, cadastre o nome e a URL/ID de cada grupo onde você tem permissão.
2. Em **Anúncios**, informe título, descrição, preço, link e opcionalmente URL da imagem.
3. Adicione variações de texto para organizar versões diferentes.
4. Em **Fila**, selecione o anúncio e enfileire para os grupos ativos.
5. Use **Iniciar fila**, **Pausar**, **Continuar** e **Parar**.
6. Em um item aguardando, use **Preparar** para copiar o texto e abrir o destino para conclusão manual.

O intervalo controla apenas a agenda local da fila. Ele não é um mecanismo anti-spam, não contorna limites e não oculta automação.

## Teste sem publicar

Mantenha `DRY_RUN=true`, use referências de teste como `https://example.com/grupo` e rode:

```powershell
python -m unittest discover -s tests -v
```

Os testes verificam inicialização Flask, criação do banco, cadastro de grupo, cadastro de anúncio, enfileiramento, execução simulada e pausa/continuação.

## Estrutura

```text
bot-facebook/
  bot.py
  database.py
  meta_api.py
  queue_manager.py
  templates/
    index.html
    grupos.html
    anuncios.html
    fila.html
  static/
    style.css
    app.js
  database/
    bot.db              # criado na primeira execução
  uploads/
  .env
  .env.example
  .gitignore
  requirements.txt
  README.md
```

## Segurança

Não há bypass de CAPTCHA ou checkpoint, evasão de bloqueios, rotação de contas, cookies de navegador ou mecanismos para esconder automação da Meta. O sistema só organiza dados locais e, por padrão, simula a fila.
# Legacy duplicate documentation marker.
# Disparo Bot Facebook (Python)

Painel local em Python 3 para cadastrar grupos autorizados, organizar anúncios e controlar uma fila de publicação. O projeto usa Flask, SQLite, HTML/CSS/JavaScript, `requests` e `python-dotenv`.

## Limite oficial da Meta

A Meta removeu `publish_to_groups`, `groups_access_member_info` e a Groups API no Graph API v19. A remoção passou a valer para todas as versões em 22/04/2024. Uma URL ou ID de grupo não concede autorização para publicar via API.

Por isso, este projeto não inventa endpoints, não usa Selenium/Playwright e não automatiza navegador. Para grupos, a fila prepara o texto, registra o histórico e permite concluir manualmente. A publicação real só seria adicionada quando a Meta documentar um método oficialmente suportado para o destino.

Fontes oficiais:

- https://developers.facebook.com/docs/graph-api/changelog/version19.0/
- https://developers.facebook.com/docs/graph-api/overview/
- https://developers.facebook.com/devpolicy/
- https://developers.facebook.com/terms/

## Requisitos no Windows

Instale Python 3.10 ou superior em https://www.python.org/downloads/ e marque **Add Python to PATH** durante a instalação.

Verifique no PowerShell:

```powershell
python --version
```

## Instalação

Abra o PowerShell dentro de `bot-facebook`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

O arquivo `.env` está no `.gitignore`. Nunca coloque senha do Facebook, cookies copiados ou tokens diretamente no código.

## Configuração

O padrão seguro é:

```env
DRY_RUN=true
PUBLISH_INTERVAL_SECONDS=5
```

Com `DRY_RUN=true`, nenhuma publicação real é enviada. A fila simula o grupo, anúncio, horário e status, e grava `Publicação simulada com sucesso` no SQLite.

`META_ACCESS_TOKEN` permanece vazio neste fluxo de grupos. Se um destino oficialmente suportado for adicionado no futuro, use somente tokens obtidos por login oficial e permissões documentadas pela Meta.

O bot Telegram é executado em processo separado do painel. Configure `TELEGRAM_BOT_TOKEN` e `PAINEL_URL` no `.env`, instale as dependências e execute `python telegram_bot.py`. O comando `/start` responde com o primeiro nome fornecido pelo Telegram e um botão para abrir o painel.

## Executar

Com o ambiente virtual ativo:

```powershell
python bot.py
```

Abra http://localhost:5000.

Em outro terminal, com o mesmo ambiente virtual ativo:

```powershell
python telegram_bot.py
```

A primeira execução cria automaticamente `database/bot.db`.

## Como usar

1. Em **Grupos**, cadastre o nome e a URL/ID de cada grupo onde você tem permissão.
2. Em **Anúncios**, informe título, descrição, preço, link e opcionalmente URL da imagem.
3. Adicione variações de texto para organizar versões diferentes.
4. Em **Fila**, selecione o anúncio e enfileire para os grupos ativos.
5. Use **Iniciar fila**, **Pausar**, **Continuar** e **Parar**.
6. Em um item aguardando, use **Preparar** para copiar o texto e abrir o destino para conclusão manual.

O intervalo controla apenas a agenda local da fila. Ele não é um mecanismo anti-spam, não contorna limites e não oculta automação.

## Teste sem publicar

Mantenha `DRY_RUN=true`, use referências de teste como `https://example.com/grupo` e rode:

```powershell
python -m unittest discover -s tests -v
```

Os testes verificam inicialização Flask, criação do banco, cadastro de grupo, cadastro de anúncio, enfileiramento, execução simulada e pausa/continuação.

## Estrutura

```text
bot-facebook/
  bot.py
  database.py
  meta_api.py
  queue_manager.py
  templates/
    index.html
    grupos.html
    anuncios.html
    fila.html
  static/
    style.css
    app.js
  database/
    bot.db              # criado na primeira execução
  uploads/
  .env
  .env.example
  .gitignore
  requirements.txt
  README.md
```

## Segurança

Não há bypass de CAPTCHA ou checkpoint, evasão de bloqueios, rotação de contas, cookies de navegador ou mecanismos para esconder automação da Meta. O sistema só organiza dados locais e, por padrão, simula a fila.
# End legacy duplicate documentation.
