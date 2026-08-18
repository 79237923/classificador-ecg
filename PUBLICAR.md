# Colocar o CardioLaudo no ar — ambiente de teste, custo zero

Guia para publicar em `ecg.henrique.rezends.com.br` usando **Render** (servidor,
gratuito) + **Cloudflare** (domínio e HTTPS, que você já tem).

Nada é instalado na sua máquina: o Render constrói e roda a imagem a partir do
GitHub.

---

## O que você vai precisar fazer (e o que já está pronto)

| Etapa | Quem faz |
|---|---|
| Dockerfile, blueprint, semeadura da conta | ✅ já pronto no repositório |
| Criar o repositório no GitHub e enviar o código | você (comandos abaixo) |
| Criar o serviço no Render conectando o repositório | você (painel web) |
| Preencher as variáveis secretas no Render | você (painel web) |
| Apontar o subdomínio no Cloudflare | você (painel web) |

---

## 1. Gerar a chave de cifragem

Guarde o valor: ele vai para o Render no passo 4 e **não deve ser commitado**.

```bash
.venv\Scripts\python scripts\manage_keys.py gerar
```

---

## 2. Enviar o código para o GitHub

O `.gitignore` já bloqueia `secrets/`, `data/` e os pesos do PyTorch. O modelo
`models/ptbxl_resnet.onnx` (8 MB) **vai junto** — é ele que o servidor carrega.

```bash
git init
```

```bash
git add . && git commit -m "CardioLaudo: analise automatizada de ECG"
```

Crie um repositório vazio em <https://github.com/new> (pode ser privado) e:

```bash
git remote add origin https://github.com/SEU-USUARIO/cardiolaudo.git
```

```bash
git branch -M main && git push -u origin main
```

**Antes de enviar, confirme que a chave não vai junto:**

```bash
git status --porcelain --ignored | findstr secrets
```

Deve aparecer `!! secrets/` (ignorado). Se aparecer sem o `!!`, pare e me avise.

---

## 3. Criar o serviço no Render

1. Entre em <https://render.com> com a conta do GitHub.
2. **New → Blueprint**, escolha o repositório. O Render lê o `render.yaml` e já
   configura o serviço (Docker, plano gratuito, health check).
3. Ele vai pedir as variáveis marcadas como secretas — preencha no passo seguinte.

O Render pede um cartão para liberar serviços web (bloqueio de US$ 1, devolvido).
Se preferir não cadastrar cartão, veja a alternativa Koyeb no fim deste guia.

---

## 4. Preencher as variáveis no Render

Em **Environment** do serviço:

| Variável | Valor |
|---|---|
| `CARDIOLAUDO_KEY` | a chave gerada no passo 1 |
| `CARDIOLAUDO_ADMIN_EMAIL` | seu e-mail (vira a conta administradora) |
| `CARDIOLAUDO_ADMIN_SENHA` | senha forte (mín. 12 caracteres, letras e números) |
| `CARDIOLAUDO_ORIGINS` | `https://ecg.henrique.rezends.com.br` |

As demais já vêm preenchidas pelo `render.yaml`.

---

## 5. Apontar o domínio

No Render, em **Settings → Custom Domains**, adicione
`ecg.henrique.rezends.com.br`. Ele mostrará um destino do tipo
`cardiolaudo.onrender.com`.

No Cloudflare, no DNS de `henrique.rezends.com.br`:

| Tipo | Nome | Destino | Proxy |
|---|---|---|---|
| CNAME | `ecg` | `cardiolaudo.onrender.com` | **DNS only** (nuvem cinza) |

> Deixe o proxy **desligado** na primeira validação: com a nuvem laranja o
> Render não consegue emitir o certificado. Depois de o HTTPS funcionar, você
> pode religar o proxy se quiser a proteção do Cloudflare.

---

## 6. Conferir

```
https://ecg.henrique.rezends.com.br/api/health
```

Deve responder `{"status":"ok", ...}`. Entre com o e-mail e a senha do passo 4.

---

## Limitações desta hospedagem (aceitáveis para teste)

- **O serviço dorme após 15 minutos sem uso.** O primeiro acesso depois disso
  demora cerca de um minuto para responder. Avise quem for testar.
- **O disco é efêmero.** A cada novo deploy (ou reinício), o banco é recriado:
  contas cadastradas e laudos salvos se perdem. A conta administradora volta
  sozinha, semeada pelas variáveis de ambiente — por isso o sistema continua
  utilizável. Para persistência real seria preciso um plano pago ou uma VM.
- **512 MB de RAM e CPU compartilhada.** O sistema usa 88 MB em repouso; uma
  análise de imagem pode levar alguns segundos a mais que localmente.

---

## Alternativa sem cartão de crédito: Koyeb

Mesmo repositório e mesmo Dockerfile. Em <https://koyeb.com>: **Create Service →
GitHub**, escolha o repositório, instância **Free**, e informe as mesmas
variáveis de ambiente. Diferenças: dorme após 1 hora (em vez de 15 min) e a CPU
é mais lenta (0,1 vCPU), então as análises demoram mais.

---

## Atualizar depois

Qualquer alteração no código:

```bash
git add . && git commit -m "descrição da mudança" && git push
```

O Render reconstrói e publica sozinho. Lembre que **cada deploy zera o banco** —
a conta administradora é recriada, as demais não.
