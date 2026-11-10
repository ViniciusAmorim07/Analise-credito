from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from analisador import extrair_dados_pdf
import shutil
import os

app = FastAPI()

# Permite que o frontend (Flutter) converse com essa API sem bloqueios do navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analisar-pdf/")
async def analisar_pdf(file: UploadFile = File(...)):
    # Salva o PDF temporariamente na pasta do projeto
    caminho_temp = f"temp_{file.filename}"
    with open(caminho_temp, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Manda o arquivo para o nosso analisador
        resultado = extrair_dados_pdf(caminho_temp)
    finally:
        # Exclui o arquivo temporário para não lotar o HD
        if os.path.exists(caminho_temp):
            os.remove(caminho_temp)
            
    return resultado