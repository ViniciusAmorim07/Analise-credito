# 🛡️ Sistema de Análise de Crédito e Risco Judicial

Plataforma Full-Stack de análise financeira e antecendentes jurídicos. O sistema contém uma página de login com cadastro de usuario e administrador, sistema com a consulta automática via API com o site Relinq, restrição de
consulta de 60 dias, logs de consulta por usuario. 

## 🎯 O Problema Resolvido
Muitos clientes devedores, fizemos uma análise com os dados dos devedores e criamos filtros baseados em notas.

Este sistema centraliza a operação em uma interface web responsiva que consulta dados cadastrais, financeiros e judiciais em tempo real, aplicando automaticamente a política de risco da empresa em poucos segundos.

---
## 🏗️ Arquitetura e Tecnologias

* **Frontend:** Flutter Web (Dart) — hospedado na **Vercel**
* **Backend:** Python + FastAPI (requisições assíncronas com `httpx`) — hospedado no **Render**
* **Banco de Dados & Logs:** PostgreSQL via **Supabase**
* **Integração Externa:** API REST de dados cadastrais, birô de crédito e processos judiciais

**REPOSITÓRIO DO BACKEND - PYTHON**
