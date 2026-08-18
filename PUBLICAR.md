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

## Situação atual

| Etapa | Estado |
|---|---|
| Repositório no GitHub | ✅ <https://github.com/79237923/classificador-ecg> (público) |
| Código enviado | ✅ 92 arquivos, 10,2 MB |
| GitHub Pages ativado | ✅ origem: GitHub Actions |
| Site provisório | ✅ <https://79237923.github.io/classificador-ecg/> |
| Domínio próprio | ⏳ **depende de você** — passo abaixo |

---

## Apontar o seu domínio (único passo pendente)

Duas coisas, nesta ordem:

**1. No Cloudflare**, no DNS de `henrique.rezends.com.br`, crie:

| Tipo | Nome | Destino | Proxy |
|---|---|---|---|
| CNAME | `ecg` | `79237923.github.io` | **DNS only** (nuvem cinza) |

> A nuvem precisa ficar **cinza**. Com o proxy laranja o GitHub não consegue
> validar o domínio nem emitir o certificado. Depois que o HTTPS estiver
> funcionando, você pode religar o proxy se quiser.

**2. No GitHub**, em **Settings → Pages → Custom domain**, informe
`ecg.henrique.rezends.com.br` e salve. Aguarde a verificação e marque
**Enforce HTTPS** quando a opção ficar disponível (pode levar alguns minutos
até o certificado ser emitido).

Se preferir, dá para fazer o segundo passo por linha de comando:

```bash
gh api -X PUT repos/79237923/classificador-ecg/pages -f cname=ecg.henrique.rezends.com.br
```

---

## Conferir

Abra o site. A primeira carga demora (baixa as bibliotecas de cálculo do CDN);
depois fica em cache. Teste com os exemplos embutidos — há um laudo de 12
derivações e três sinais digitais prontos.

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
