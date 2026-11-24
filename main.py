import os
import hmac
import json
import time
import base64
import hashlib
import unicodedata
import httpx
from fastapi import FastAPI, Body, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RELINQ_URL = (os.getenv("RELINQ_URL") or "").strip()
RELINQ_TOKEN = (os.getenv("RELINQ_TOKEN") or "").strip()
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip()
AUTH_SECRET = (os.getenv("AUTH_SECRET") or "chave_padrao_trocar_no_render").encode("utf-8")


# ---------------------------------------------------------
# FUNÇÕES DE TOKEN E AUDITORIA (SUPABASE)
# ---------------------------------------------------------
def gerar_token_sessao(dados_usuario: dict, validade_horas: int = 12) -> str:
    payload = {
        "login": dados_usuario["login"],
        "nome": dados_usuario["nome"],
        "perfil": dados_usuario.get("perfil", "vendedor"),
        "exp": int(time.time()) + (validade_horas * 3600),
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    assinatura = hmac.new(AUTH_SECRET, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{assinatura}"


def validar_token_sessao(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sessão expirada ou não autenticada. Faça login.")
    
    token = authorization.replace("Bearer ", "").strip()
    try:
        payload_b64, assinatura_recebida = token.split(".")
        assinatura_esperada = hmac.new(AUTH_SECRET, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(assinatura_recebida, assinatura_esperada):
            raise HTTPException(status_code=401, detail="Token inválido.")
            
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            raise HTTPException(status_code=401, detail="Sessão expirada. Faça login novamente.")
            
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Falha na validação do acesso.")


def supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def registrar_log_consulta(usuario: dict, cpf: str, status_geral: str, pontos: float):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    registro = {
        "usuario_login": usuario["login"],
        "usuario_nome": usuario["nome"],
        "cpf_consultado": cpf,
        "status_geral": status_geral,
        "pontuacao_financeira": pontos,
        "custo_consulta": 10.49,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/logs_consultas",
                headers=supabase_headers(),
                json=registro,
            )
        except Exception as e:
            print(f"Aviso: falha ao gravar log no Supabase: {e}")

async def buscar_consulta_recente_60d(cpf: str) -> dict | None:
    """
    Verifica no Supabase se existe uma consulta paga (API_RELINQ) para este CPF
    nos últimos 60 dias.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    limite_60d = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/logs_consultas",
                headers=supabase_headers(),
                params={
                    "cpf_consultado": f"eq.{cpf}",
                    "origem": "eq.API_RELINQ",
                    "consultado_em": f"gte.{limite_60d}",
                    "select": "id,usuario_login,usuario_nome,status_geral,pontuacao_financeira,consultado_em,resultado_json",
                    "order": "consultado_em.desc",
                    "limit": "1",
                },
            )
            registros = resp.json() if resp.status_code == 200 else []
            return registros[0] if registros else None
        except Exception as e:
            print(f"Aviso: falha ao verificar cache de 60 dias no Supabase: {e}")
            return None


async def registrar_log_consulta(
    usuario: dict,
    cpf: str,
    status_geral: str,
    pontos: float,
    custo: float = 10.49,
    origem: str = "API_RELINQ",
    resultado_json: dict | None = None,
):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    registro = {
        "usuario_login": usuario["login"],
        "usuario_nome": usuario["nome"],
        "cpf_consultado": cpf,
        "status_geral": status_geral,
        "pontuacao_financeira": pontos,
        "custo_consulta": custo,
        "origem": origem,
        "resultado_json": resultado_json,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/logs_consultas",
                headers=supabase_headers(),
                json=registro,
            )
        except Exception as e:
            print(f"Aviso: falha ao gravar log no Supabase: {e}")            


# ---------------------------------------------------------
# ROTA DE LOGIN
# ---------------------------------------------------------
@app.post("/login/")
async def login_endpoint(dados: dict = Body(...)):
    login_input = str(dados.get("login", "")).strip().lower()
    senha_input = str(dados.get("senha", "")).strip()

    if not login_input or not senha_input:
        raise HTTPException(status_code=400, detail="Informe login e senha.")

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Banco de autenticação não configurado no servidor.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers=supabase_headers(),
            params={"login": f"eq.{login_input}", "select": "id,nome,login,senha,perfil,ativo"},
        )

    usuarios = resp.json() if resp.status_code == 200 else []
    if not usuarios:
        raise HTTPException(status_code=401, detail="Login ou senha incorretos.")

    user = usuarios[0]
    if not user.get("ativo"):
        raise HTTPException(status_code=403, detail="Usuário bloqueado. Contate o administrador.")

    if user.get("senha") != senha_input:
        raise HTTPException(status_code=401, detail="Login ou senha incorretos.")

    token = gerar_token_sessao(user)
    return {
        "token": token,
        "usuario": {
            "nome": user["nome"],
            "login": user["login"],
            "perfil": user.get("perfil", "vendedor"),
        },
    }


# ---------------------------------------------------------
# ROTA DE CONSULTA DE LOGS (APENAS ADMIN)
# ---------------------------------------------------------
@app.get("/logs/")
async def listar_logs_endpoint(authorization: str | None = Header(default=None)):
    usuario = validar_token_sessao(authorization)
    if usuario.get("perfil") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/logs_consultas",
            headers=supabase_headers(),
            params={
                "select": "id,usuario_login,usuario_nome,cpf_consultado,status_geral,pontuacao_financeira,custo_consulta,origem,consultado_em",
                "order": "consultado_em.desc",
                "limit": "300",
            },
        )

    logs = resp.json() if resp.status_code == 200 else []
    consultas_pagas = [l for l in logs if l.get("origem") == "API_RELINQ"]
    consultas_bloqueadas = [l for l in logs if l.get("origem") == "BLOQUEIO_60D_CACHE"]

    total_gasto = round(sum(float(item.get("custo_consulta") or 0.0) for item in logs), 2)
    total_economizado = round(len(consultas_bloqueadas) * 10.49, 2)

    return {
        "total_consultas_pagas": len(consultas_pagas),
        "total_bloqueios_60d": len(consultas_bloqueadas),
        "custo_total_brl": total_gasto,
        "economia_total_brl": total_economizado,
        "logs": logs,
    }

def normalizar_texto(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.upper().strip()


def parse_data_flexivel(data_str: str):
    if not data_str:
        return None
    limpa = str(data_str).strip()
    formatos = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"]
    for fmt in formatos:
        try:
            return datetime.strptime(limpa, fmt)
        except ValueError:
            continue
    return None


def calcular_idade_divida(data_str: str) -> int:
    data_obj = parse_data_flexivel(data_str)
    if not data_obj:
        return 0
    return (datetime.now() - data_obj).days


def extrair_processos_unicos(dados_data: dict) -> list:
    
    processos = []
    vistos = set()

    def varrer(obj, caminho=""):
        if isinstance(obj, dict):
            if "crednet" in caminho:
                return

            num_unico = obj.get("numeroProcessoUnico") or obj.get("numeroProcesso")
            tem_metadados_proc = any(
                k in obj for k in ["assuntosCNJ", "classeProcessual", "statusPj", "area"]
            )

            if num_unico and tem_metadados_proc:
                chave_id = str(num_unico).strip()
                if chave_id not in vistos:
                    vistos.add(chave_id)
                    processos.append(obj)
                return

            for k, v in obj.items():
                varrer(v, f"{caminho}.{k}")

        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                varrer(item, f"{caminho}[{idx}]")

    varrer(dados_data)
    return processos


def classificar_processo_criminal(proc: dict, cpf_consultado: str) -> dict:
    """
    Classifica processos criminais com base na Tabela Unificada do CNJ,
    separando crimes imperdoáveis de apontamentos (Lei 9.099 / >5 anos).
    """
    # 1. Verifica se o consultado figura no polo passivo (réu/indiciado/investigado)
    infos_parte = proc.get("informacoesPartePessoaConsultada") or []
    tipos_parte = [normalizar_texto(t) for t in (proc.get("tipoPartePessoaConsultada") or [])]

    polos_passivos = {
        "PASSIVO", "REU", "INDICIADO", "INVESTIGADO",
        "DENUNCIADO", "ACUSADO", "AUTOR DO FATO", "FLAGRANTEADO"
    }
    eh_passivo = False
    eh_vitima_ou_outro = False

    for info in infos_parte:
        polo = normalizar_texto(info.get("polo", ""))
        tipo = normalizar_texto(info.get("tipo", ""))
        if polo == "PASSIVO" or tipo in polos_passivos:
            eh_passivo = True
        elif polo == "ATIVO" or tipo in {"VITIMA", "TESTEMUNHA", "OFENDIDO", "NOTICIANTE"}:
            eh_vitima_ou_outro = True

    if not eh_passivo and any(t in polos_passivos for t in tipos_parte):
        eh_passivo = True

    if not eh_passivo and eh_vitima_ou_outro:
        return {"ignorar": True}

    # 2. Extração dos assuntos CNJ, classe e órgão julgador
    assuntos = proc.get("assuntosCNJ") or []
    titulos_assuntos = [normalizar_texto(a.get("titulo", "")) for a in assuntos if a.get("titulo")]
    codigos_assuntos = {str(a.get("codigo", "")).strip() for a in assuntos if a.get("codigo")}

    assunto_principal = next(
        (a.get("titulo") for a in assuntos if a.get("principal") and a.get("titulo")),
        (assuntos[0].get("titulo") if assuntos else None)
        or (proc.get("classeProcessual") or {}).get("nome")
        or "Assunto não especificado",
    )

    classe_nome = normalizar_texto((proc.get("classeProcessual") or {}).get("nome", ""))
    orgao_julgador = normalizar_texto((proc.get("orgaoJulgador") or {}).get("nome", ""))
    texto_assuntos = " | ".join(titulos_assuntos) if titulos_assuntos else normalizar_texto(assunto_principal)
    base_completa = f"{texto_assuntos} {classe_nome} {orgao_julgador}"

    # 3. Classificação da Natureza Jurídica (apenas para exibição no relatório)
    termos_pessoa = [
        "CONTRA A PESSOA", "HOMICIDIO", "FEMINICIDIO", "LESAO CORPORAL",
        "AMEACA", "CALUNIA", "INJURIA", "DIFAMACAO", "SEQUESTRO",
        "CARCERE PRIVADO", "VIOLENCIA DOMESTICA", "MAUS TRATOS", "ABORTO", "INFANTICIDIO"
    ]
    termos_patrimonio = [
        "CONTRA O PATRIMONIO", "ROUBO", "FURTO", "ESTELIONATO",
        "APROPRIACAO INDEBITA", "RECEPTACAO", "EXTORSAO", "DANO"
    ]

    if "3369" in codigos_assuntos or any(t in texto_assuntos for t in termos_pessoa):
        natureza = "Contra a Pessoa"
    elif "3415" in codigos_assuntos or any(t in texto_assuntos for t in termos_patrimonio):
        natureza = "Contra o Patrimônio"
    else:
        natureza = "Outros Bens Jurídicos"

    # 4. Elemento Subjetivo (Doloso vs Culposo)
    elemento_subjetivo = "Culposo" if "CULPOS" in base_completa else "Doloso"

    # 5. Modalidade (Qualificado vs Simples)
    modalidade = "Qualificado" if "QUALIFICAD" in base_completa else "Simples"

    # 6. Lista de Crimes Imperdoáveis (Risco direto à vida, liberdade ou ao patrimônio/frota)
    crimes_imperdoaveis = [
        "HOMICIDIO", "FEMINICIDIO", "LATROCINIO", "SEQUESTRO", "CARCERE PRIVADO",
        "EXTORSAO MEDIANTE SEQUESTRO", "LESAO CORPORAL GRAVE", "LESAO CORPORAL GRAVISSIMA",
        "ESTUPRO", "ROUBO", "FURTO", "ESTELIONATO", "APROPRIACAO INDEBITA",
        "RECEPTACAO", "EXTORSAO", "ADULTERACAO DE SINAL IDENTIFICADOR",
        "TRAFICO DE DROGAS", "ORGANIZACAO CRIMINOSA", "ASSOCIACAO CRIMINOSA",
        "PORTE ILEGAL DE ARMA", "POSSE IRREGULAR DE ARMA", "LAVAGEM DE DINHEIRO"
    ]

    eh_crime_imperdoavel = (
        (elemento_subjetivo == "Doloso" and any(c in texto_assuntos for c in crimes_imperdoaveis))
        or modalidade == "Qualificado"
    )

    # 7. Verificação Lei nº 9.099/1995 (Infração de Menor Potencial Ofensivo)
    eh_lei_9099 = (
        not eh_crime_imperdoavel
        and (
            "JUIZADO ESPECIAL" in orgao_julgador
            or "JECRIM" in orgao_julgador
            or "TERMO CIRCUNSTANCIADO" in classe_nome
            or "MENOR POTENCIAL OFENSIVO" in base_completa
            or "9.099" in base_completa
            or "9099" in base_completa
        )
    )

    # 8. Verificação Temporal (> 5 anos) e Status (Extinto / Encerrado)
    status_pj = proc.get("statusPj") or {}
    status_proc = normalizar_texto(status_pj.get("statusProcesso", ""))
    status_obs = normalizar_texto(proc.get("statusObservacao", ""))
    status_unificado = f"{status_proc} {status_obs}".strip()

    termos_encerrado = [
        "EXTINT", "ARQUIVAD", "ENCERRAD", "BAIXAD", "PRESCRIT",
        "ABSOLVID", "CUMPRID", "TRANSITADO EM JULGADO"
    ]
    esta_encerrado = (
        any(t in status_unificado for t in termos_encerrado)
        and "TRAMITACAO" not in status_unificado
    )

    data_ref = parse_data_flexivel(proc.get("dataDistribuicao") or proc.get("dataAutuacao") or "")
    anos_decorridos = round((datetime.now() - data_ref).days / 365.25, 1) if data_ref else 0.0
    mais_de_5_anos = anos_decorridos > 5.0

    # 9. Decisão Final da Matriz Jurídica
    if eh_crime_imperdoavel:
        decisao = "REPROVADO"
        if mais_de_5_anos and esta_encerrado:
            motivo_regra = f"Crime grave/imperdoável ({assunto_principal}) — bloqueio mantido mesmo após 5 anos"
        else:
            motivo_regra = f"Crime grave/imperdoável ({assunto_principal}) — reprovação automática"
    elif eh_lei_9099:
        decisao = "APONTAMENTO"
        motivo_regra = f"Infração de menor potencial ofensivo / Lei 9.099 ({assunto_principal}) — apenas apontamento"
    elif mais_de_5_anos and esta_encerrado:
        decisao = "APONTAMENTO"
        motivo_regra = f"Processo encerrado há {anos_decorridos} anos ({assunto_principal}) — sem reprovação automática"
    else:
        decisao = "APONTAMENTO"
        motivo_regra = f"Ocorrência fora da lista de crimes graves ({assunto_principal}) — gerado apontamento para análise"

    # 10. Montagem do resumo e retorno
    resumo_classificacao = f"{assunto_principal} ({natureza} | {elemento_subjetivo} | {modalidade})"

    return {
        "ignorar": False,
        "assunto": assunto_principal,
        "natureza": natureza,
        "elemento_subjetivo": elemento_subjetivo,
        "modalidade": modalidade,
        "resumo_classificacao": resumo_classificacao,
        "status_processo": status_proc or "EM TRAMITACAO",
        "anos_decorridos": anos_decorridos,
        "decisao": decisao,
        "motivo_regra": motivo_regra,
    }

def analisar_processo_civel(proc: dict, cpf_consultado: str) -> dict:
    """
    Analisa processos cíveis para identificar polo passivo, status ativo
    e registros de Alienação Fiduciária distribuídos há menos de 5 anos.
    """
    status_pj = proc.get("statusPj") or {}
    status_proc = normalizar_texto(status_pj.get("statusProcesso", ""))
    status_obs = normalizar_texto(proc.get("statusObservacao", ""))
    status_unificado = f"{status_proc} {status_obs}".strip()

    esta_arquivado = (
        any(t in status_unificado for t in ["ARQUIVAD", "EXTINT", "BAIXAD", "ENCERRAD"])
        and "TRAMITACAO" not in status_unificado
    )

    # 1. Verifica se o consultado está no polo passivo (réu/devedor/requerido)
    infos_parte = proc.get("informacoesPartePessoaConsultada") or []
    tipos_parte = [normalizar_texto(t) for t in (proc.get("tipoPartePessoaConsultada") or [])]

    polos_passivos = {"PASSIVO", "REU", "EXECUTADO", "REQUERIDO", "DEVEDOR", "DEMANDADO"}
    eh_passivo = False
    eh_somente_ativo = False

    for info in infos_parte:
        polo = normalizar_texto(info.get("polo", ""))
        tipo = normalizar_texto(info.get("tipo", ""))
        if polo == "PASSIVO" or tipo in polos_passivos:
            eh_passivo = True
        elif polo == "ATIVO":
            eh_somente_ativo = True

    if not eh_passivo and any(t in polos_passivos for t in tipos_parte):
        eh_passivo = True

    
    if not eh_passivo and not eh_somente_ativo and not infos_parte and not tipos_parte:
        for parte in (proc.get("partes") or []):
            doc_parte = "".join(filter(str.isdigit, str(parte.get("cpf", ""))))
            if doc_parte == cpf_consultado:
                polo_p = normalizar_texto(parte.get("polo", ""))
                tipo_p = normalizar_texto(parte.get("tipo", ""))
                if polo_p == "PASSIVO" or tipo_p in polos_passivos:
                    eh_passivo = True
                elif polo_p == "ATIVO":
                    eh_somente_ativo = True

    # 2. Verifica Alienação Fiduciária
    assuntos = proc.get("assuntosCNJ") or []
    titulos_assuntos = [normalizar_texto(a.get("titulo", "")) for a in assuntos if a.get("titulo")]
    codigos_assuntos = {str(a.get("codigo", "")).strip() for a in assuntos if a.get("codigo")}
    classe_nome = normalizar_texto((proc.get("classeProcessual") or {}).get("nome", ""))

    texto_busca = f"{' | '.join(titulos_assuntos)} {classe_nome}"
    codigos_alienacao = {"9582", "10435"}  # Códigos CNJ de Alienação Fiduciária / Busca e Apreensão

    tem_termo_alienacao = (
        "ALIENACAO FIDUCIARIA" in texto_busca
        or bool(codigos_assuntos.intersection(codigos_alienacao))
    )

    data_ref = parse_data_flexivel(proc.get("dataDistribuicao") or proc.get("dataAutuacao") or "")
    anos_decorridos = round((datetime.now() - data_ref).days / 365.25, 1) if data_ref else 0.0
    menos_de_5_anos = anos_decorridos < 5.0

    # Gera apontamento se for Alienação Fiduciária < 5 anos e o cliente não for apenas o credor/autor
    apontar_alienacao = tem_termo_alienacao and menos_de_5_anos and (eh_passivo or not eh_somente_ativo)

    descricao_alienacao = None
    if apontar_alienacao:
        assunto_princ = next(
            (a.get("titulo") for a in assuntos if a.get("principal") and a.get("titulo")),
            (proc.get("classeProcessual") or {}).get("nome") or "Alienação Fiduciária",
        )
        status_legivel = status_proc or "EM TRAMITACAO"
        descricao_alienacao = (
            f"Alienação Fiduciária (< 5 anos): {assunto_princ} "
            f"({anos_decorridos} anos • {status_legivel})"
        )

    return {
        "esta_arquivado": esta_arquivado,
        "eh_passivo": eh_passivo,
        "apontar_alienacao": apontar_alienacao,
        "descricao_alienacao": descricao_alienacao,
    }


@app.post("/analisar-cpf/")
async def analisar_cpf_endpoint(
    dados: dict = Body(...),
    authorization: str | None = Header(default=None),
):
    usuario_logado = validar_token_sessao(authorization)
    cpf = "".join(filter(str.isdigit, str(dados.get("cpf", ""))))

    if not cpf:
        return {"erro": "CPF não informado."}

    # ---------------------------------------------------------
    # TRAVA DE 60 DIAS (Evita cobrar R$ 10,49 novamente na Relinq)
    # ---------------------------------------------------------
    consulta_anterior = await buscar_consulta_recente_60d(cpf)
    if consulta_anterior:
        dt_orig = datetime.fromisoformat(
            str(consulta_anterior["consultado_em"]).replace("Z", "+00:00")
        )
        dias_passados = (datetime.now(timezone.utc) - dt_orig).days
        dias_restantes = max(1, 60 - dias_passados)
        data_br = dt_orig.astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y às %H:%M")

        # Registra no log que o vendedor tentou consultar (com custo R$ 0,00)
        await registrar_log_consulta(
            usuario=usuario_logado,
            cpf=cpf,
            status_geral=consulta_anterior.get("status_geral", "N/A"),
            pontos=float(consulta_anterior.get("pontuacao_financeira") or 0.0),
            custo=0.00,
            origem="BLOQUEIO_60D_CACHE",
            resultado_json=None,
        )

        resultado_salvo = consulta_anterior.get("resultado_json") or {}
        if resultado_salvo:
            resultado_salvo["consultado_por"] = usuario_logado["nome"]
            resultado_salvo["bloqueio_60_dias"] = {
                "ativo": True,
                "mensagem": (
                    f"Nova consulta na Relinq bloqueada (regra de 60 dias). "
                    f"Exibindo análise realizada em {data_br} por {consulta_anterior['usuario_nome']}. "
                    f"Liberação para nova consulta em {dias_restantes} dia(s)."
                ),
                "data_original": data_br,
                "consultado_por_original": consulta_anterior["usuario_nome"],
                "dias_restantes": dias_restantes,
                "economia_brl": 10.49,
            }
            return resultado_salvo

        return {
            "erro": (
                f"CPF já consultado em {data_br} por {consulta_anterior['usuario_nome']} "
                f"(Resultado: {consulta_anterior.get('status_geral')}). "
                f"Nova consulta bloqueada por mais {dias_restantes} dia(s)."
            )
        }

    # ---------------------------------------------------------
    # SE NÃO HOUVE CONSULTA NOS ÚLTIMOS 60 DIAS, CHAMA A RELINQ
    # ---------------------------------------------------------
    if not RELINQ_URL.startswith("http"):
        return {"erro": "RELINQ_URL inválida ou não configurada no servidor."}

    if not RELINQ_TOKEN:
        return {"erro": "RELINQ_TOKEN não configurado no servidor."}

    headers = {
        "Authorization": f"Bearer {RELINQ_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url_final = (
        RELINQ_URL.replace("{cpf}", cpf)
        if "{cpf}" in RELINQ_URL
        else f"{RELINQ_URL.rstrip('/')}/{cpf}"
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(url_final, headers=headers)
            dados_api = response.json()
        except Exception as e:
            return {"erro": f"Falha de conexão com a Relinq: {str(e)}"}

    if "data" not in dados_api or not dados_api["data"]:
        return {"erro": f"A Relinq não retornou os dados esperados. Resposta bruta: {dados_api}"}

    data = dados_api.get("data") or {}
    crednet = data.get("crednet") or {}
    consulta_credito = crednet.get("consultaCredito") or {}
    resumo = consulta_credito.get("resumoConsulta") or {}

    # ---------------------------------------------------------
    # 1. EXTRAÇÃO E PONTUAÇÃO FINANCEIRA
    # ---------------------------------------------------------
    score_obj = consulta_credito.get("score") or {}
    score = score_obj.get("score", 0)

    pendencias_obj = resumo.get("pendenciasFinanceiras") or {}
    total_pendencias = pendencias_obj.get("valorTotal", 0.0)

    qtd_dividas_locadora = 0
    palavras_locadora = [
        "LOCAC", "LOCADOR", "ALUG", "RENT A CAR", "RENTACAR", "FROTAS", "MOBILIDADE",
        "MOTTU", "LOCALIZA", "MOVIDA", "UNIDAS", "VAMOS", "TURBI", "KAVAK",
        "VOU DE MOTO", "VOUDEMOTO", "RAPI", "LEVE MOTOS", "MOTO FACIL", "MOTOFACIL",
        "LOCA SIM", "LOCASIM", "LOCA9", "MOTOCA", "HERTZ", "AVIS", "BUDGET"
    ]
    divida_nu = 0.0

    lista_pendencias = consulta_credito.get("pendenciasFinanceiras") or []
    for p in lista_pendencias:
        informante = normalizar_texto(p.get("informante", ""))
        tipo = normalizar_texto(p.get("tipoPendencia", ""))
        valor = float(p.get("valor") or 0.0)

        if any(palavra in informante or palavra in tipo for palavra in palavras_locadora):
            qtd_dividas_locadora += 1

        if any(nu in informante for nu in ["NU ", "NUBANK", "NU PAGAMENTOS", "NU FINANCEIRA"]):
            divida_nu += valor

    teve_atraso_aluguel = qtd_dividas_locadora > 0

    lista_protestos = consulta_credito.get("protestos") or []
    qtd_protestos = len(lista_protestos)
    valor_protestos = sum(float(p.get("valor") or 0.0) for p in lista_protestos)

    pontos = 0.0

    # Score Serasa
    if score > 500:
        pontos += 2
    elif score >= 351:
        pontos += 1

    # Aluguel em Atraso
    if qtd_dividas_locadora == 0:
        pontos += 3

    # Pendências Financeiras Totais
    if total_pendencias <= 3144.96:
        pontos += 2
    elif total_pendencias <= 3500.00:
        pontos += 1

    # Nu Financeira
    if divida_nu <= 107.40:
        pontos += 1
    elif divida_nu <= 500.00:
        pontos += 0.5

    # Protestos (Quantidade e Antiguidade)
    if qtd_protestos == 0:
        pontos += 1
    elif qtd_protestos == 1:
        dias_protesto = calcular_idade_divida(lista_protestos[0].get("data", ""))
        if dias_protesto > 365:
            pontos += 0.5

    # Protestos (% sobre o Total de Pendências)
    if total_pendencias > 0:
        perc_protesto = (valor_protestos / total_pendencias) * 100
    else:
        perc_protesto = 100.0 if valor_protestos > 0 else 0.0

    if perc_protesto < 25.0:
        pontos += 1
    elif perc_protesto <= 35.0:
        pontos += 0.5

    reprovacao_locadora = qtd_dividas_locadora > 1
    status_financeiro = "REPROVADO" if (reprovacao_locadora or pontos < 5.0) else "APROVADO"

    # ---------------------------------------------------------
    # 2. ANÁLISE JUDICIAL E ANTECEDENTES CRIMINAIS
    # ---------------------------------------------------------
    todos_processos = extrair_processos_unicos(data)

    processos_civeis_ativos = []
    analises_criminais = []
    apontamentos_alienacao = []
    tem_polo_passivo_civel = False

    for proc in todos_processos:
        area = normalizar_texto(proc.get("area", ""))
        ramo = normalizar_texto((proc.get("statusPj") or {}).get("ramoDireito", ""))

        eh_criminal = "CRIMINAL" in area or "PENAL" in ramo

        if eh_criminal:
            resultado_crim = classificar_processo_criminal(proc, cpf)
            if not resultado_crim.get("ignorar"):
                analises_criminais.append(resultado_crim)
        else:
            resultado_civel = analisar_processo_civel(proc, cpf)
            if not resultado_civel["esta_arquivado"]:
                processos_civeis_ativos.append(proc)
                if resultado_civel["eh_passivo"]:
                    tem_polo_passivo_civel = True

            if resultado_civel["apontar_alienacao"] and resultado_civel["descricao_alienacao"]:
                apontamentos_alienacao.append(resultado_civel["descricao_alienacao"])

    reprovados_criminais = [c for c in analises_criminais if c["decisao"] == "REPROVADO"]
    apontamentos_criminais = [c for c in analises_criminais if c["decisao"] == "APONTAMENTO"]

    status_judicial = "REPROVADO" if len(reprovados_criminais) > 0 else "APROVADO"

    todos_apontamentos = [c["motivo_regra"] for c in apontamentos_criminais] + apontamentos_alienacao

    alienacao_texto = (
        f"Sim ({len(apontamentos_alienacao)} registro(s) < 5 anos)"
        if apontamentos_alienacao
        else "Não"
    )

    qtd_total_processos = len(processos_civeis_ativos) + len(analises_criminais)

    if reprovados_criminais:
        tem_criminal_texto = f"SIM ({len(reprovados_criminais)} Reprovado)"
    elif apontamentos_criminais:
        tem_criminal_texto = f"SIM ({len(apontamentos_criminais)} Apontamento)"
    else:
        tem_criminal_texto = "Não"

    if analises_criminais:
        mais_grave = reprovados_criminais[0] if reprovados_criminais else apontamentos_criminais[0]
        tempo_criminal_texto = f"{mais_grave['anos_decorridos']} anos ({mais_grave['status_processo']})"
        tipos_listados = [c["resumo_classificacao"] for c in analises_criminais]
        if processos_civeis_ativos:
            tipos_listados.append(f"Cível ({len(processos_civeis_ativos)} ativos)")
        tipos_processos_texto = " | ".join(tipos_listados)
    elif processos_civeis_ativos:
        tempo_criminal_texto = "N/A"
        tipos_processos_texto = f"Ações Cíveis ({len(processos_civeis_ativos)} ativos)"
    else:
        tempo_criminal_texto = "N/A"
        tipos_processos_texto = "Nenhum"

    polaridade = (
        "Passivo (Réu)"
        if (len(analises_criminais) > 0 or tem_polo_passivo_civel)
        else ("Ativo" if processos_civeis_ativos else "Nenhum")
    )
    # ---------------------------------------------------------
    # 3. PARECER FINAL
    # ---------------------------------------------------------
    status_geral = (
        "REPROVADO"
        if (status_financeiro == "REPROVADO" or status_judicial == "REPROVADO")
        else "APROVADO"
    )
   
        
    resposta_final = {
        "status_geral": status_geral,
        "consultado_por": usuario_logado["nome"],
        "bloqueio_60_dias": {
            "ativo": False,
            "mensagem": "Consulta nova realizada na base Relinq.",
        },
        "serasa": {
            "status": status_financeiro,
            "pontuacao_total": pontos,
            "detalhes": {
                "score": score,
                "teve_atraso_aluguel": teve_atraso_aluguel,
                "divida_nu_financeira": round(divida_nu, 2),
                "qtd_protestos": qtd_protestos,
                "total_pendencias": round(total_pendencias, 2),
            },
        },
        "detetive": {
            "status": status_judicial,
            "detalhes": {
                "qtd_processos": qtd_total_processos,
                "tipos_processos": tipos_processos_texto,
                "polaridade_processual": polaridade,
                "alienacao_fiduciaria": alienacao_texto,
                "debitos_aluguel": f"Sim ({qtd_dividas_locadora} registros)" if teve_atraso_aluguel else "Não",
                "tem_processo_criminal": tem_criminal_texto,
                "tempo_processo_criminal": tempo_criminal_texto,
                "apontamentos_judiciais": todos_apontamentos,
                "classificacao_criminal": analises_criminais,
            },
        },
    }

    # Salva no Supabase com o JSON completo para reaproveitar pelos próximos 60 dias
    await registrar_log_consulta(
        usuario=usuario_logado,
        cpf=cpf,
        status_geral=status_geral,
        pontos=pontos,
        custo=10.49,
        origem="API_RELINQ",
        resultado_json=resposta_final,
    )

    return resposta_final

  