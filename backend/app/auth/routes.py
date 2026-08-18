"""Rotas de sessão e dependência de autorização."""
from __future__ import annotations

import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import service
from .service import AuthError, User

COOKIE_NAME = "cardiolaudo_session"
# Seguro por padrão: exige HTTPS. O desenvolvimento local sobre HTTP precisa
# desligar explicitamente com CARDIOLAUDO_ENV=dev — o inverso (inseguro por
# padrão, com opt-in para produção) transfere para quem faz o deploy a chance de
# esquecer e publicar sessões de dado de saúde em texto claro.
COOKIE_SECURE = os.getenv("CARDIOLAUDO_ENV", "producao").lower() != "dev"

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    senha: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    email: str
    full_name: str
    professional_id: str | None = None
    role: str


def _token_da_requisicao(request: Request, cookie: str | None) -> str | None:
    if cookie:
        return cookie
    # Alternativa para clientes não-navegador (scripts, integrações).
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def usuario_atual(
    request: Request,
    cardiolaudo_session: str | None = Cookie(default=None),
) -> User:
    """Exige sessão válida. Use como dependência nas rotas com dado clínico."""
    user = service.usuario_por_token(_token_da_requisicao(request, cardiolaudo_session))
    if not user:
        raise HTTPException(401, "Sessão ausente ou expirada. Faça login novamente.")
    return user


def usuario_admin(user: User = Depends(usuario_atual)) -> User:
    """Exige papel de administrador. Um médico comum recebe 403."""
    if user.role != "admin":
        raise HTTPException(403, "Acesso restrito a administradores.")
    return user


def _ip_do_cliente(request: Request) -> str:
    """IP de origem, considerando proxy reverso.

    Confia em X-Forwarded-For apenas quando CARDIOLAUDO_TRUST_PROXY=1: aceitá-lo
    sem proxy à frente permitiria forjar a origem e escapar do limite por IP.
    """
    if os.getenv("CARDIOLAUDO_TRUST_PROXY", "0") == "1":
        encaminhado = request.headers.get("x-forwarded-for", "")
        if encaminhado:
            return encaminhado.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


@router.post("/login", response_model=UserOut)
def login(dados: LoginIn, request: Request, response: Response):
    try:
        user, token, expira = service.autenticar(
            dados.email, dados.senha, ip=_ip_do_cliente(request))
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc

    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,          # inacessível a JavaScript: reduz impacto de XSS
        samesite="strict",      # não acompanha requisições de outros sites (CSRF)
        secure=COOKIE_SECURE,
        max_age=int(service.SESSION_TTL_HOURS * 3600),
        path="/",
    )
    return UserOut(email=user.email, full_name=user.full_name,
                   professional_id=user.professional_id, role=user.role)


@router.post("/logout")
def logout(response: Response,
           cardiolaudo_session: str | None = Cookie(default=None)):
    service.encerrar_sessao(cardiolaudo_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(usuario_atual)):
    return UserOut(email=user.email, full_name=user.full_name,
                   professional_id=user.professional_id, role=user.role)


class TrocaSenhaIn(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=256)
    senha_nova: str = Field(min_length=1, max_length=256)


@router.post("/senha")
def trocar_senha(dados: TrocaSenhaIn, request: Request,
                 cardiolaudo_session: str | None = Cookie(default=None),
                 user: User = Depends(usuario_atual)):
    """Troca a senha do próprio usuário, mantendo esta sessão e encerrando as
    demais."""
    token = _token_da_requisicao(request, cardiolaudo_session)
    try:
        service.alterar_senha(user.id, dados.senha_atual, dados.senha_nova,
                              manter_token=token)
    except AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "mensagem": "Senha alterada. As outras sessões foram encerradas."}


# ------------------------------------------------------- administração de contas
admin_router = APIRouter(prefix="/api/admin", tags=["admin"],
                         dependencies=[Depends(usuario_admin)])


class NovoUsuarioIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=200)
    senha: str = Field(min_length=1, max_length=256)
    professional_id: str | None = Field(default=None, max_length=60)
    role: str = Field(default="medico")


class ResetSenhaIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    senha_nova: str = Field(min_length=1, max_length=256)


@admin_router.get("/usuarios")
def admin_listar():
    return {"usuarios": service.listar_usuarios()}


@admin_router.post("/usuarios")
def admin_criar(dados: NovoUsuarioIn):
    if dados.role not in ("medico", "admin"):
        raise HTTPException(422, "Papel inválido (use 'medico' ou 'admin').")
    try:
        u = service.criar_usuario(dados.email, dados.full_name, dados.senha,
                                  professional_id=dados.professional_id, role=dados.role)
    except AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "id": u.id, "email": u.email}


@admin_router.post("/usuarios/reset-senha")
def admin_reset(dados: ResetSenhaIn, admin: User = Depends(usuario_admin)):
    try:
        ok = service.resetar_senha(dados.email, dados.senha_nova)
    except AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "Conta não encontrada.")
    return {"ok": True, "mensagem": "Senha redefinida; as sessões da conta foram encerradas."}


@admin_router.post("/usuarios/{email}/desativar")
def admin_desativar(email: str, admin: User = Depends(usuario_admin)):
    if email.strip().lower() == admin.email.strip().lower():
        raise HTTPException(400, "Você não pode desativar a própria conta.")
    if not service.desativar_usuario(email):
        raise HTTPException(404, "Conta não encontrada.")
    return {"ok": True}


@admin_router.post("/usuarios/{email}/reativar")
def admin_reativar(email: str):
    if not service.reativar_usuario(email):
        raise HTTPException(404, "Conta não encontrada.")
    return {"ok": True}


@admin_router.post("/usuarios/{email}/papel")
def admin_papel(email: str, role: str, admin: User = Depends(usuario_admin)):
    if email.strip().lower() == admin.email.strip().lower():
        raise HTTPException(400, "Você não pode alterar o próprio papel.")
    try:
        ok = service.definir_papel(email, role)
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "Conta não encontrada.")
    return {"ok": True}
