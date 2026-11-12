import httpx
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# Permissão para o Flutter (Vercel) conversar com o Python (Render)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Credenciais da API Relinq
RELINQ_URL = "https://painel.relinqscore.com.br/api/v2/cpf-relinq-plus/"
RELINQ_TOKEN = "rsk_a2458eac526b3efc2040758093de9b376b8651904ddf8318197e5e41c9f592f9"

class ConsultaCPF(BaseModel):
    cpf: str

@app.post("/analisar-cpf/")
async def analisar_cpf_endpoint(request: ConsultaCPF):
    cpf = request.cpf.strip()
    
    # 1. Fazendo a requisição oficial para a Relinq
    headers = {
        "Authorization": f"Bearer {RELINQ_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # Bate na API usando a URL e o Token fornecidos
            response = await client.get(f"{RELINQ_URL}{cpf}", headers=headers)
            dados_api = response.json()
        except Exception as e:
            return {"erro": "Falha ao se comunicar com a API da Relinq."}

    if dados_api.get("status") != "success":
        return {"erro": "CPF não encontrado ou erro na API da Relinq."}

    # 2. Navegando nas "gavetas" do JSON da Relinq
    credito = dados_api.get("data", {}).get("crednet", {}).get("consultaCredito", {})
    resumo = credito.get("resumoConsulta", {})
    
    # ---------------------------------------------------------
    # BLOCO FINANCEIRO (Regras de Crédito)
    # ---------------------------------------------------------
    score = credito.get("score", {}).get("score", 0)
    qtd_protestos = resumo.get("protestos", {}).get("quantidadeTotal", 0)
    total_pendencias = resumo.get("pendenciasFinanceiras", {}).get("valorTotal", 0)
    
    teve_atraso_aluguel = False
    divida_nu = 0.0
    
    # Analisa pendência por pendência procurando Locadoras Concorrentes e Nubank
    lista_pendencias = credito.get("pendenciasFinanceiras", [])
    for p in lista_pendencias:
        informante = str(p.get("informante", "")).upper()
        tipo = str(p.get("tipoPendencia", "")).upper()
        situacao = str(p.get("situacao", "")).upper()
        valor = float(p.get("valor", 0.0))
        
        # Filtro de reporvação de aluguel
        if "MOTTU" in informante or "ALUGUEL" in tipo or "LOCACAO" in tipo:
            teve_atraso_aluguel = True
            
        # Filtro de dívida Nu Financeira
        if "NU " in informante or "NUBANK" in informante:
            divida_nu += valor

    # Cálculo da pontuação financeira
    pontos = 0
    if score > 500: pontos += 2
    elif score >= 351: pontos += 1

    status_financeiro = "REPROVADO" if teve_atraso_aluguel else "APROVADO"

    # ---------------------------------------------------------
    # BLOCO JUDICIAL E ANTECEDENTES (Regras Forenses)
    # ---------------------------------------------------------
    qtd_civeis = resumo.get("acoesCiveis", {}).get("quantidadeTotal", 0)
    
    # Como a "Ana Exemplo" veio limpa, usamos .get para proteger o código caso a chave exista só para CPFs sujos
    qtd_criminais = resumo.get("processosCriminais", {}).get("quantidadeTotal", 0)
    
    tem_criminal = "SIM (ATENÇÃO)" if qtd_criminais > 0 else "Não"
    
    # Se houver ações cíveis cadastradas na Relinq, consideramos o polo Passivo (Réu)
    polaridade = "Passivo (Réu)" if qtd_civeis > 0 else "Nenhum"
    
    status_judicial = "REPROVADO" if qtd_criminais > 0 else "APROVADO"

    # ---------------------------------------------------------
    # PARECER FINAL
    # ---------------------------------------------------------
    status_geral = "APROVADO"
    if status_financeiro == "REPROVADO" or status_judicial == "REPROVADO":
        status_geral = "REPROVADO"

    # Devolvemos no EXATO formato que a tela do Flutter foi programada para receber
    return {
        "status_geral": status_geral,
        "serasa": {
            "status": status_financeiro,
            "pontuacao_total": pontos,
            "detalhes": {
                "score": score,
                "teve_atraso_aluguel": teve_atraso_aluguel,
                "divida_nu_financeira": round(divida_nu, 2),
                "qtd_protestos": qtd_protestos,
                "total_pendencias": round(total_pendencias, 2)
            }
        },
        "detetive": {
            "status": status_judicial,
            "detalhes": {
                "qtd_processos": qtd_civeis + qtd_criminais,
                "tipos_processos": "Ações Cíveis" if qtd_civeis > 0 else "Nenhum",
                "polaridade_processual": polaridade,
                "alienacao_fiduciaria": "Não consultado nesta base",
                "debitos_aluguel": "Sim" if teve_atraso_aluguel else "Não",
                "tem_processo_criminal": tem_criminal,
                "tempo_processo_criminal": "N/A"
            }
        }
    }