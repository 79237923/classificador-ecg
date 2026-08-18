# CardioLaudo — Análise automatizada de eletrocardiograma

Software online de leitura e classificação de ECG para **apoio à decisão clínica**.
Aceita **sinal digital** (CSV/TXT, WFDB/PhysioNet em .zip) e **imagem do traçado**
(PNG/JPG/PDF, com digitalização automática da faixa de ritmo).

> ⚠ **Status regulatório:** ferramenta em desenvolvimento/validação. Não é
> registrada na ANVISA como dispositivo médico e **não substitui laudo médico**.
> Para uso clínico real será necessário: validação clínica documentada,
> gestão de risco (ISO 14971), ciclo de vida de software (IEC 62304) e registro
> na ANVISA como SaMD (RDC 657/2022).

## Como executar

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\manage_keys.py gerar
.venv\Scripts\python scripts\manage_users.py criar
.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
```

Em desenvolvimento, `CARDIOLAUDO_ENV=dev` gera a chave automaticamente em
`secrets/cardiolaudo.key` e dispensa o primeiro comando.

Acesse **http://localhost:8000** e entre com a conta criada. O acesso é
restrito: todas as rotas que tocam dado clínico exigem sessão.

### Contas de profissionais

Há duas formas de administrar contas: a **linha de comando** e o **painel de
administração na interface** (para contas com papel de administrador).

```bash
.venv\Scripts\python scripts\manage_users.py criar      # cria conta (senha sem eco)
.venv\Scripts\python scripts\manage_users.py listar
.venv\Scripts\python scripts\manage_users.py senha medico@clinica.br     # reset de senha
.venv\Scripts\python scripts\manage_users.py desativar medico@clinica.br
.venv\Scripts\python scripts\manage_users.py papel medico@clinica.br admin  # promove a admin
```

A senha é lida sem eco e nunca aceita por argumento de linha de comando
(argumentos ficam no histórico do shell e na lista de processos). Desativar uma
conta encerra imediatamente todas as sessões abertas dela.

### Papéis e painel de administração

Cada conta tem papel `medico` (padrão) ou `admin`. O **primeiro administrador**
é criado pela linha de comando (`manage_users.py papel <email> admin`) — não há
autopromoção pela interface. Um administrador vê o botão **"Administração"** na
barra superior, que abre um painel para cadastrar contas, redefinir senhas,
desativar/reativar e ver o histórico de acesso, sem precisar do terminal.

As rotas `/api/admin/*` exigem papel de administrador (um médico comum recebe
403). Um admin não pode desativar nem rebaixar a própria conta (evita ficar sem
nenhum administrador por engano).

### Troca de senha

Qualquer usuário logado vê o botão **"Alterar senha"**. A troca exige a senha
atual (impede que uma sessão sequestrada a altere sem conhecê-la), recusa senha
igual à anterior, e **encerra as demais sessões** do usuário — se a conta
estava comprometida, os outros acessos caem. Verificado por
`scripts/test_admin.py` (20 asserções).

```bash
.venv\Scripts\python scripts\test_admin.py
```

### Antes de publicar em rede

| Variável | Padrão | Para quê |
|---|---|---|
| `CARDIOLAUDO_KEY` | — | **Obrigatória em produção.** Chave de cifragem em base64 (32 bytes). Sem ela o servidor não sobe. Gere com `manage_keys.py gerar` e guarde em cofre de segredos. |
| `CARDIOLAUDO_ENV` | `producao` | Em `producao`, o cookie exige HTTPS e a chave de cifragem é obrigatória. Use `dev` **apenas** em desenvolvimento local. |
| `CARDIOLAUDO_ORIGINS` | localhost:8000 | Origens do frontend, separadas por vírgula. Nunca use `*`. |
| `CARDIOLAUDO_TRUST_PROXY` | `0` | Defina `1` somente com proxy reverso à frente, para ler o IP real de `X-Forwarded-For`. Sem proxy, o cabeçalho é forjável. |

O padrão é seguro: o cookie só sai por HTTPS a menos que você desligue
explicitamente. O inverso transferiria a quem faz o deploy a chance de esquecer
e publicar sessões de dado de saúde em texto claro.

Gerar exames sintéticos de teste e rodar o teste de fumaça:

```bash
.venv\Scripts\python scripts\make_synthetic_ecg.py
.venv\Scripts\python scripts\smoke_test.py
```

## Arquitetura

```
backend/app/
  auth/         crypto.py (AES-256-GCM, índice cego, gestão da chave)
                db.py (SQLite: contas, sessões, laudos) · security.py (scrypt, tokens)
                service.py (contas, sessões, posse dos laudos) · routes.py (login/logout/me)
  ingestion/    loaders.py (CSV/TXT/WFDB)
                image_digitizer.py (imagem/PDF → sinal): calibração pela grade +
                largura de papel, segmentação de faixas, layout 3×4 → 12 derivações,
                rastreamento de traçado por continuidade
  processing/   qrs_bounds.py — QRS global por velocidade espacial das 12 derivações
                analysis.py — picos R, onda P e fim da T (NeuroKit2), FC, RR,
                RMSSD, PR, QRS, QT/QTc (Bazett e Fridericia), eixo,
                Sokolow-Lyon, ST medido a partir do ponto J real
  classification/
                rules.py — critérios AHA/ACC/HRS (bradi/taquicardia, possível FA,
                BAV 1º, PR curto, QRS largo e muito largo, QTc longo/curto,
                desvios de ST, desvio de eixo incluindo eixo extremo, HVE)
                deep_model.py — ResNet 1D 12 derivações (superclasses PTB-XL),
                ativado automaticamente se existir models/ptbxl_resnet.pt
  reporting/    report.py — laudo PDF (ReportLab) + trilha de auditoria cifrada
  retention.py  políticas de retenção, piso legal e limites do disjuntor
  purge.py      execução do expurgo (simula por padrão) + retenção legal
  scheduler.py  agendador diário em duas camadas + disjuntor de segurança
  main.py       API FastAPI + servidor do frontend
frontend/       SPA (HTML/CSS/JS) — upload, traçado em canvas com grade de ECG,
                medidas, achados por severidade, laudo PDF
scripts/        manage_users.py ............. cria, lista, desativa, senha, papel
                test_admin.py ............... testes de admin e troca de senha
                manage_keys.py .............. chave de cifragem e auditoria
                migrate_encrypt.py .......... converte banco anterior à cifragem
                manage_retention.py ......... retenção, expurgo e retenção legal
                test_auth.py ................ testes de acesso e isolamento
                test_crypto.py .............. testes de cifragem em repouso
                test_retention.py ........... testes de retenção e expurgo
                test_scheduler.py ........... testes do agendador e do disjuntor
                train_ptbxl.py .............. treino do classificador DL
                validate_measurements.py .... valida medidas contra PTB-XL
                tune_qrs.py ................. calibra o limiar do QRS global
                tune_afib.py ................ calibra a regra de fibrilação atrial
                calibrate_delineation.py .... compara métodos de delineação
                make_synthetic_ecg.py ....... gera exames de teste
                smoke_test.py ............... teste de fumaça da API
data/audit/     trilha de auditoria (1 JSON por análise, hash SHA-256 do arquivo)
```

## API

| Rota | Sessão | Descrição |
|---|---|---|
| `POST /api/auth/login` | — | `{email, senha}` → abre sessão em cookie httponly |
| `POST /api/auth/logout` | — | encerra a sessão atual |
| `GET /api/auth/me` | exigida | dados do profissional autenticado |
| `POST /api/analyze` | **exigida** | multipart: `file` + `sampling_rate`, `age`, `sex` → medidas, achados e resumo |
| `GET /api/report/{id}/pdf` | **exigida** | laudo em PDF — apenas do próprio autor |
| `GET /api/analyses` | **exigida** | histórico de exames do usuário |
| `POST /api/auth/senha` | **exigida** | troca a própria senha (senha atual + nova) |
| `GET/POST /api/admin/usuarios` | **admin** | lista / cadastra contas |
| `POST /api/admin/usuarios/reset-senha` | **admin** | redefine a senha de uma conta |
| `POST /api/admin/usuarios/{email}/desativar` | **admin** | desativa / reativa uma conta |
| `GET /api/health` | — | status e disponibilidade do modelo DL |

Clientes não-navegador podem enviar `Authorization: Bearer <token>` em vez do cookie.

## Autenticação e rastreabilidade

- **Senhas com scrypt** (memória-dura, resistente a GPU/ASIC), mínimo de 12
  caracteres combinando letras e números.
- **Sessões opacas revogáveis**, guardadas no banco apenas como hash, válidas por
  12 h. Escolhidas em vez de JWT porque desligar um profissional precisa invalidar
  o acesso imediatamente — um JWT autocontido não permite isso sem lista de revogação.
- **Cookie httponly + samesite=strict**: inacessível a JavaScript (reduz impacto de
  XSS) e não acompanha requisições de outros sites (CSRF).
- **Isolamento por posse**: cada análise é gravada com o `user_id` de quem a criou, e
  a consulta do laudo filtra por esse campo. O identificador da análise não é
  credencial — outro usuário não recupera o exame mesmo conhecendo o ID.
- **Tentativas de login sem dado em claro**: a tabela de força bruta guarda
  apenas o índice cego (HMAC) do e-mail e do IP. Contar tentativas exige um
  identificador estável, não o dado em si — gravá-lo em texto puro ali anularia
  a cifragem do resto do banco.
- **Proteção contra força bruta sem bloqueio de conta**: quem apresenta a senha
  correta entra sempre. Bloquear a conta permitiria a quem apenas conhece o
  e-mail de um médico mantê-lo fora do sistema durante um atendimento, bastando
  errar a senha de propósito — o NIST SP 800-63B desaconselha esse bloqueio. O
  custo recai sobre quem erra, via atraso progressivo (até 4 s).
- **Bloqueio de origem por contas distintas, não por volume**: uma clínica
  inteira costuma sair por um único IP público, e barrar por número de erros
  deixaria todos de fora por causa de alguns enganos de digitação. Varrer 10
  contas diferentes a partir da mesma origem é assinatura de password spraying
  e não acontece por acaso.
- **Tempo de resposta constante no login**: a verificação de senha roda mesmo
  quando a conta não existe (contra um hash descartável), para que a duração da
  resposta não revele quais e-mails estão cadastrados.
- **Identificação do operador** no laudo e na trilha de auditoria (nome, registro
  profissional, e-mail), atendendo ao requisito de rastreabilidade de SaMD.

Verificado por `scripts/test_auth.py` (21 asserções), que inclui a tentativa de
um usuário baixar o laudo de outro, a prova de que a senha correta nunca é
negada, e a detecção de password spraying.

```bash
.venv\Scripts\python scripts\test_auth.py
```

## Cifragem em repouso

Dado de saúde é gravado cifrado com **AES-256-GCM** — cifragem autenticada, que
além de tornar o conteúdo ilegível detecta adulteração: um laudo alterado
diretamente no banco falha na verificação em vez de ser aceito.

**O que fica cifrado**: laudos completos (medidas, achados, dados do paciente),
e-mail, nome e registro profissional das contas, e a trilha de auditoria inteira.
As senhas já estavam protegidas por scrypt.

**O que permanece legível, por necessidade funcional**: identificadores de
análise, carimbos de tempo e o vínculo análise→usuário — necessários para a
consulta e para o controle de posse. Quem obtiver o arquivo saberá *quantos*
exames existem e *quando*, mas não de quem nem o conteúdo clínico.

**Busca sem expor o e-mail**: um valor cifrado com nonce aleatório muda a cada
gravação e não serve de chave de busca. O login localiza a conta por um *índice
cego* — HMAC-SHA256 do e-mail com chave derivada. É determinístico o bastante
para a consulta, mas sem a chave não permite testar se um e-mail está cadastrado.

**A chave nunca fica em `data/`**, que é justamente o diretório que se copia num
backup. Em produção vem de `CARDIOLAUDO_KEY`; em desenvolvimento, de
`secrets/cardiolaudo.key` (fora do controle de versão).

```bash
.venv\Scripts\python scripts\manage_keys.py conferir
```

| Comando | O que faz |
|---|---|
| `manage_keys.py gerar` | Gera uma chave nova. Não altera nada. |
| `manage_keys.py conferir` | Diz qual chave está em uso e se o banco está integralmente cifrado. |
| `manage_keys.py ler-auditoria` | Decifra e exibe a trilha de auditoria. |
| `manage_keys.py rodar --nova-chave <b64>` | Regrava contas, laudos e auditoria com uma chave nova. |
| `migrate_encrypt.py --backup` | Converte um banco anterior à cifragem. |

A rotação troca o arquivo de chave **sozinha e só após a regravação dar certo**,
guardando cópia da anterior. Atualizar a chave por fora, antes de confirmar o
sucesso, é o caminho mais curto para perder o histórico clínico: se a rotação
falhar no meio, chave e dados divergem e não há recuperação.

A migração roda `VACUUM` ao final. Sem isso a cifragem seria aparente: o SQLite
não zera páginas liberadas, e o texto puro da tabela antiga continuaria legível
no arquivo com um editor hexadecimal.

Verificado por `scripts/test_crypto.py` (24 asserções), que lê os bytes do banco
e da trilha para confirmar que nenhum dado clínico aparece em texto puro.

```bash
.venv\Scripts\python scripts\test_crypto.py
```

> **Perder a chave significa perder os exames.** Não existe recuperação — é o que
> torna a cifragem eficaz. Faça backup da chave em local separado do banco.

## Retenção e expurgo

Em software médico, **apagar cedo demais é tão irregular quanto guardar para
sempre**. A Resolução CFM 1.821/2007 (art. 8º) exige guarda do prontuário por no
mínimo 20 anos a partir do último registro, e a LGPD (art. 16, I) ressalva
justamente o cumprimento de obrigação legal de guarda ao tratar da eliminação.
Por isso o dado clínico tem prazo longo e protegido, enquanto o dado operacional
é descartado em dias.

| Categoria | Prazo padrão | Fundamento |
|---|---|---|
| Laudos de ECG | 20 anos | CFM 1.821/2007 art. 8º; LGPD art. 16, I |
| Trilha de auditoria | 20 anos | Acompanha o laudo — sem ela não há rastreabilidade |
| Sessões expiradas | 1 dia | LGPD art. 15, I — término do tratamento |
| Tentativas de login | 1 dia | Só servem à janela de proteção contra força bruta |
| Backups pré-cifragem | 7 dias | Estão em **texto puro** — LGPD art. 46 |

```bash
.venv\Scripts\python scripts\manage_retention.py status
```

| Comando | O que faz |
|---|---|
| `manage_retention.py status` | Políticas em vigor, inventário e o que está vencido. |
| `manage_retention.py expurgar` | **Simula** — relata sem apagar nada. |
| `manage_retention.py expurgar --confirmar` | Executa. Exige digitar `APAGAR LAUDOS` quando houver prontuário no lote. |
| `manage_retention.py reter <id> --motivo "..."` | Retenção legal: torna o laudo imune ao expurgo. |
| `manage_retention.py liberar <id>` | Remove a retenção legal. |
| `manage_retention.py historico` | Exclusões já realizadas (metadados, sem conteúdo clínico). |
| `manage_retention.py ciclo` | Um ciclo automático — ponto de entrada do agendador do SO. Saída 2 se o disjuntor disparar. |
| `manage_retention.py agendar` | Mostra o comando para registrar no agendador do SO. |

### Três salvaguardas contra apagar o que não se deve

1. **Piso legal**: configurar os laudos abaixo de 20 anos é recusado, citando a
   resolução. Só passa com `CARDIOLAUDO_RETENCAO_ACEITA_RISCO=1` — assumir a
   decisão precisa ser um ato explícito, não um descuido de configuração.
2. **Retenção legal** (*legal hold*): um laudo marcado sobrevive ao expurgo
   independentemente da idade — necessário em litígio ou auditoria em curso.
3. **Simulação por padrão**: nada é apagado sem `--confirmar`, e a exclusão de
   prontuário ainda pede confirmação digitada.

### Expurgo automático, em duas camadas

O servidor executa o expurgo **diariamente às 3h** (hora local) enquanto estiver
no ar, mais um ciclo ao subir — o serviço pode ter ficado dias parado.

| Camada | Comportamento |
|---|---|
| **Dado operacional** (sessões, tentativas, backups vencidos) | Sempre automático. Nenhum tem valor clínico e todos se refazem sozinhos; deixá-los acumular é que seria o risco. |
| **Laudos** | Só com `CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS=1`. Desligado por padrão: dado operacional apagado por engano se refaz; um prontuário, não. |

**Disjuntor de segurança.** Mesmo com os laudos automatizados, o ciclo é
interrompido quando o volume não corresponde a envelhecimento natural — o
sintoma de prazo mal configurado, relógio do servidor errado ou data
corrompida. Ele dispara acima de 50 laudos por execução, ou acima de 10% do
acervo (nunca abaixo de um piso de 5, para que uma clínica pequena não receba
alarme falso no envelhecimento normal). Quando dispara, **nada é apagado**,
registra-se um erro no log e o expurgo de laudos fica parado até alguém
verificar a configuração. A execução manual não passa pelo disjuntor: ali há um
humano vendo a prévia e confirmando.

```bash
.venv\Scripts\python scripts\manage_retention.py agendar
```

Esse comando mostra como registrar o ciclo no agendador do sistema operacional
(`schtasks` no Windows, `crontab` no Linux/macOS) — necessário se o servidor não
ficar sempre ligado. Com várias instâncias, use o agendador do sistema e desligue
o interno com `CARDIOLAUDO_EXPURGO_AUTOMATICO=0` para não concorrerem.

Verificado por `scripts/test_scheduler.py` (24 asserções), sendo as principais a
prova de que o padrão não apaga laudo nenhum e de que o disjuntor **impede**
exclusão em massa.

```bash
.venv\Scripts\python scripts\test_scheduler.py
```

Cada exclusão de laudo é registrada em `deletions` com categoria, prazo aplicado
e executor — nunca o conteúdo clínico. Apagar prontuário sem deixar rastro
destruiria a rastreabilidade que a própria guarda pretende assegurar. O expurgo
roda `VACUUM` ao final: sem isso o registro sairia das consultas mas continuaria
nas páginas liberadas do arquivo.

Verificado por `scripts/test_retention.py` (24 asserções), incluindo a prova de
que o registro cifrado está no arquivo antes do expurgo e sai depois.

```bash
.venv\Scripts\python scripts\test_retention.py
```

| Variável | Padrão | Ajusta |
|---|---|---|
| `CARDIOLAUDO_RETENCAO_LAUDOS_DIAS` | 7305 (20 anos) | Guarda dos laudos |
| `CARDIOLAUDO_RETENCAO_AUDITORIA_DIAS` | 7305 | Guarda da trilha de auditoria |
| `CARDIOLAUDO_RETENCAO_SESSOES_DIAS` | 1 | Sessões expiradas |
| `CARDIOLAUDO_RETENCAO_TENTATIVAS_DIAS` | 1 | Tentativas de login |
| `CARDIOLAUDO_RETENCAO_BACKUPS_DIAS` | 7 | Backups anteriores à cifragem |
| `CARDIOLAUDO_RETENCAO_ACEITA_RISCO` | `0` | Libera prazo de laudo abaixo do piso legal |
| `CARDIOLAUDO_EXPURGO_AUTOMATICO` | `1` | Liga/desliga o agendador interno |
| `CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS` | `0` | Permite o agendador apagar prontuário |
| `CARDIOLAUDO_EXPURGO_HORA` | `3` | Hora local da execução diária |
| `CARDIOLAUDO_EXPURGO_LIMITE` | `50` | Disjuntor: máximo de laudos por execução |
| `CARDIOLAUDO_EXPURGO_LIMITE_FRACAO` | `0.10` | Disjuntor: fração máxima do acervo |
| `CARDIOLAUDO_EXPURGO_PISO` | `5` | Disjuntor: quantidade sempre tolerada |

## Os dois motores: critérios clínicos e deep learning

O sistema tem **dois motores independentes**, e só um é IA:

- **Motor de critérios clínicos** (`processing/` + `classification/rules.py`) —
  não é IA. Processamento de sinal (detecção de picos R, delimitação do QRS por
  velocidade espacial, delineação de ondas) mais limiares determinísticos das
  diretrizes. **É ele que produz todas as medidas e todos os achados do laudo**,
  incluindo a detecção de supra de ST.
- **Classificador de deep learning** (`classification/deep_model.py`) —
  acrescenta cinco probabilidades por superclasse do PTB-XL. É **opcional** e
  **não roda em exames enviados como imagem** (cada derivação do laudo cobre só
  2,5 s; o modelo foi treinado em 10 s simultâneos e produziria ruído).

Sem o modelo de IA o sistema funciona normalmente — perde apenas a seção de
probabilidades nos exames em sinal digital.

### Inferência por ONNX Runtime, não PyTorch

O servidor usa **ONNX Runtime (~25 MB)**, não PyTorch (~2,8 GB instalado). O
PyTorch existe apenas para treinar e exportar, na máquina de desenvolvimento.
Isso reduz a instalação de **3.495 MB para 657 MB (−81%)** e a memória em
repouso de **763 MB para 88 MB**, o que viabiliza hospedagem gratuita.

```bash
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python scripts\train_ptbxl.py --epochs 30   # treina (precisa do PTB-XL)
.venv\Scripts\python scripts\export_onnx.py               # converte e VALIDA
```

`export_onnx.py` não apenas converte: compara as probabilidades dos dois motores
em entradas aleatórias e **falha se divergirem** além de 1e-4 (na conversão atual
a maior divergência foi 2,4e-07). Um modelo exportado que produzisse saídas
diferentes mudaria o laudo silenciosamente. Ele também embute os pesos num
arquivo único — o exportador do PyTorch os separa em `.onnx.data`, e um deploy
que copiasse só o `.onnx` carregaria um modelo sem pesos.

Para treinar: baixe o [PTB-XL](https://physionet.org/content/ptb-xl/1.0.3/) e
extraia em `data/ptbxl/`. O arquivo `models/ptbxl_resnet.onnx` é detectado
automaticamente pela API.

## Desempenho medido (PTB-XL, ECGs reais)

Reproduza com `scripts/validate_measurements.py --n 200` e `scripts/train_ptbxl.py`.

**Medidas em 200 ECGs rotulados NORM** — percentual dentro da faixa fisiológica:

| Medida | Mediana | Dentro da faixa | Faixa de referência |
|---|---|---|---|
| Frequência cardíaca | 67,9 bpm | 96% | 50–100 bpm |
| Intervalo PR | 160 ms | 92% | 120–200 ms |
| Duração do QRS | 94 ms | 88% | 60–110 ms |
| Intervalo QT | 378 ms | 93% | 320–450 ms |
| QTc (Fridericia) | 393 ms | 97% | 340–460 ms |
| Eixo elétrico | 25,5° | 94% | −30° a +90° |

**Detecção de fibrilação atrial** (200 AFIB + 200 NORM): sensibilidade **87%**,
especificidade **98,5%** (3 falsos positivos em 200 normais).

**Classificador de deep learning** (fold 10 oficial, 2.158 registros):
AUC macro **0,900** — NORM 0,930 · MI 0,911 · STTC 0,931 · CD 0,915 · HYP 0,814.

### Decisões de método que sustentam esses números

- **QRS global por velocidade espacial** (`processing/qrs_bounds.py`) em vez de
  delineação por derivação isolada. A delineação do NeuroKit2 (dwt) media QRS
  com mediana de 177 ms em ECGs normais (apenas 7% na faixa correta); o método
  atual mede 94 ms com 88% de acerto. O limiar (5% do envelope) foi calibrado
  contra 80 ECGs normais e 80 com bloqueio de ramo, priorizando a sensibilidade
  ao alargamento patológico (98,7% dos bloqueios detectados).
- **Fibrilação atrial por irregularidade RR**, não por ausência de onda P. A
  detecção automática de onda P não discrimina: `p_wave_ratio` tem mediana 1,00
  tanto na FA quanto no ritmo sinusal. A regra anterior, que exigia ausência de
  P, tinha 2% de sensibilidade.
- **Busca de derivação sem sensibilidade a maiúsculas**: o PTB-XL grava
  `AVF`/`AVR` e a convenção clínica usa `aVF`/`aVR`; a comparação exata fazia o
  eixo elétrico falhar silenciosamente em 100% dos registros reais.
- **Nunca afirmar normalidade sobre medida ausente**: quando QRS, PR ou QT não
  puderam ser medidos, o laudo diz "ANÁLISE INCOMPLETA", não "normal".
- **ST territorial, não por derivação isolada**: o supra de ST só vira achado
  quando ≥2 derivações **anatomicamente adjacentes** do mesmo território
  (anterior V1–V4, lateral I/aVL/V5/V6, inferior II/III/aVF) concordam — o
  critério da 4ª Definição Universal de IAM. Medido no ponto J (não J+60, que
  cai na onda T e superestimava V2–V3), com limiares por derivação (V2–V3
  exigem 200 µV; 150 µV em mulheres). Isso levou a especificidade em ECGs NORM
  digitais a **98%**. Desvios isolados viram nota "confirmar no traçado", não
  alarme — são os mais prováveis de serem artefato.

## Limitações conhecidas (v0.1.0)

- **O agendador interno depende do servidor estar no ar**: se o serviço fica
  parado por longos períodos, use o agendador do sistema operacional
  (`manage_retention.py agendar`).
- **O disparo do disjuntor só vai para o log** — não há alerta por e-mail ou
  webhook. Numa implantação séria, monitore o log para o texto
  `EXPURGO DE LAUDOS INTERROMPIDO`.
- **Sem atendimento automatizado ao direito de eliminação** (LGPD art. 18, VI):
  pedidos do titular precisam ser avaliados manualmente contra a obrigação legal
  de guarda de 20 anos, que na maioria dos casos prevalece.
- **Chave única, sem cofre gerenciado**: adequada para instalação própria, mas um
  serviço multi-instituição deveria usar KMS/HSM e chave por instituição.
- **Sem identificação do paciente** no laudo — só idade e sexo. Nome ou número de
  prontuário precisam ser capturados e exibidos para uso assistencial real.
- **Sem expiração/rotação obrigatória de credencial**: a senha não vence sozinha
  (a troca é possível a qualquer momento, mas não é forçada periodicamente).
- **Contadores de força bruta são por instância**: a proteção vive no banco local,
  então uma implantação com múltiplos servidores precisa de armazenamento
  compartilhado (Redis) ou de limitação no proxy reverso.
- Análise de **imagem** reconhece o layout clínico 3×4 (três linhas de quatro
  derivações + tira de ritmo) e extrai as 12 derivações com boa fidelidade de
  morfologia (correlação 0,7–0,99 contra o sinal digital de origem). Mas cada
  derivação do layout cobre só 2,5 s e a **amplitude de ST por imagem é
  imprecisa** (ruído de ±100–340 µV vs. limiar diagnóstico de 100 µV): por isso
  o supra de ST detectado por imagem é sinalizado como *"possível — ler o
  traçado"* (anormal), não como STEMI confirmado (crítico), reservado ao sinal
  digital. A separação traçado/grade depende de grade colorida (vermelha/rosa) e
  não corrige perspectiva de fotos.
- O **classificador de deep learning** roda apenas em sinal digital de 12
  derivações completo e simultâneo; é automaticamente pulado em imagens (cujas
  derivações são parciais e não simultâneas).
- **Ritmo é reportado como "regular", não "sinusal"**: a origem do ritmo não é
  determinável sem análise confiável da onda P. Flutter atrial com condução fixa
  e ritmo juncional produzem RR regular e não são distinguidos.
- Morfologia de BRD/BRE, ondas delta, pré-excitação e isquemia territorial ainda
  não são classificadas — dependem de análise multiderivação dedicada.
- Limiar de desvio de ST uniforme (0,1 mV), sem ajuste por derivação, sexo ou
  idade como recomenda a 4ª Definição Universal de Infarto.
