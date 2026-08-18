# Colocar o CardioLaudo no ar — GitHub Pages, custo zero

A versão que roda **inteiramente no navegador** de quem acessa. Sem servidor,
sem cartão de crédito, sem dormir por inatividade, e o exame nunca sai do
computador do usuário.

> **Já feito:** o repositório local está criado, o commit verificado (nenhuma
> chave ou dado sensível entrou), e a versão web foi construída e testada no
> navegador — análise de imagem em 1,7 s e laudo em PDF gerado com sucesso.

---

## Como funciona

```
navegador do usuário
   ├── baixa o Pyodide + bibliotecas do CDN (≈30 MB, só na 1ª visita)
   ├── baixa cardiolaudo.zip (680 KB) — o mesmo motor Python do servidor
   └── analisa o ECG localmente — nada é enviado a lugar nenhum
```

O motor é **o mesmo código** validado contra o PTB-XL. Verificado: as medidas
(FC, PR, QRS, QT, QTc, eixo) e a lista de achados saem **numericamente iguais**
às do servidor.

---

## 1. Enviar para o GitHub

Crie um repositório em <https://github.com/new>. Ele precisa ser **público**
para o GitHub Pages funcionar no plano gratuito.

```bash
git remote add origin https://github.com/SEU-USUARIO/cardiolaudo.git
```

```bash
git branch -M main && git push -u origin main
```

---

## 2. Ativar o GitHub Pages

No repositório: **Settings → Pages → Source: GitHub Actions**.

O arquivo `.github/workflows/deploy-web.yml` já está no projeto. Ele reconstrói
o pacote do motor a partir do código-fonte a cada push — assim a versão web
nunca fica defasada em relação ao `backend/`.

O primeiro deploy leva 2–3 minutos. O endereço sai como
`https://SEU-USUARIO.github.io/cardiolaudo/`.

---

## 3. Apontar o seu domínio

No repositório: **Settings → Pages → Custom domain**, informe
`ecg.henrique.rezends.com.br` e salve. Isso cria um arquivo `CNAME` no repo.

No Cloudflare, no DNS de `henrique.rezends.com.br`:

| Tipo | Nome | Destino | Proxy |
|---|---|---|---|
| CNAME | `ecg` | `SEU-USUARIO.github.io` | **DNS only** (nuvem cinza) |

> Mantenha o proxy **desligado**: com a nuvem laranja o GitHub não consegue
> emitir o certificado. Depois que o HTTPS estiver ativo, marque
> **Enforce HTTPS** nas configurações do Pages.

---

## 4. Conferir

Abra `https://ecg.henrique.rezends.com.br`. A primeira carga demora (baixa as
bibliotecas); depois fica em cache. Teste com os exemplos embutidos — há um
laudo de 12 derivações e três sinais digitais prontos.

---

## Atualizar depois

```bash
git add . && git commit -m "descrição" && git push
```

O GitHub Actions reconstrói e republica sozinho.

---

## O que esta versão tem — e o que não tem

**Tem:** análise completa de sinal digital e de imagem (layout 3×4 de 12
derivações), todas as medidas, os achados clínicos, o traçado desenhado e o
laudo em PDF para baixar.

**Não tem:** login, histórico de exames salvos e trilha de auditoria — tudo
isso exige servidor. Como nada é armazenado, cada análise vive só enquanto a
página estiver aberta.

**Não tem deep learning.** O `onnxruntime` não existe para WebAssembly, então o
classificador de IA não roda aqui. Na prática isso não muda nada para exames em
imagem — o modelo já era pulado neles, porque cada derivação do laudo cobre só
2,5 s e ele foi treinado em 10 s simultâneos. Para sinal digital, perde-se
apenas as cinco probabilidades por superclasse; todas as medidas e achados
continuam, porque vêm do motor de critérios clínicos, que não é IA.

---

## Se preferir a versão com servidor

O `Dockerfile` e o `render.yaml` continuam no repositório. A versão com servidor
tem login, laudos salvos, auditoria e o classificador de IA — em troca de
depender de hospedagem que dorme por inatividade e apaga o banco a cada deploy
no plano gratuito. As duas versões convivem no mesmo repositório e usam o mesmo
motor de análise.
