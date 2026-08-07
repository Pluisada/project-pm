# 🧪 Como Testar o Projeto Management MVP

## ⚠️ Pré-requisitos

Você precisa ter:
- **Python 3.11+** instalado
- **Node.js 18+** instalado
- **npm** ou **yarn**
- **OpenRouter API Key** (gratuita em https://openrouter.ai/)

## 🔑 Obter API Key do OpenRouter

1. Acesse https://openrouter.ai/
2. Crie uma conta (grátis)
3. Vá para "Keys" na sidebar
4. Copie sua API Key

## 🚀 Começar a Testar (Método Rápido)

### 1️⃣ Clonar/Entrar no Projeto

```bash
cd /Users/pauloluisada/Library/Mobile\ Documents/com~apple~CloudDocs/CloudDocs/IA/Agentic\ Code\ Course/Projects/pm
```

### 2️⃣ Configurar Backend

```bash
# Entrar na pasta backend
cd backend

# Instalar dependências
pip install -r requirements.txt
# OU se tiver requirements.txt faltando, instale via pyproject.toml:
pip install -e .

# Configurar variável de ambiente com sua API key
export OPENROUTER_API_KEY="sua_api_key_aqui"

# Iniciar servidor
python -m uvicorn main:app --reload --port 8000
```

**Você verá:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

### 3️⃣ Configurar Frontend (Em outro terminal)

```bash
# Voltar para a raiz do projeto
cd /Users/pauloluisada/Library/Mobile\ Documents/com~apple~CloudDocs/CloudDocs/IA/Agentic\ Code\ Course/Projects/pm

# Entrar na pasta frontend
cd frontend

# Instalar dependências (se não estiverem já instaladas)
npm install

# Iniciar servidor de desenvolvimento
npm run dev
```

**Você verá:**
```
  ▲ Next.js 16.1.6
  - Local:        http://localhost:3000
```

### 4️⃣ Abrir no Browser

Acesse: **http://localhost:3000**

---

## 📝 Fluxo de Teste Passo-a-Passo

### TESTE 1: Login & Autenticação

1. **Página inicial** aparece com formulário de login
2. Digite qualquer **username** (ex: "paulo")
3. Digite qualquer **password** (ex: "teste123")
4. Clique em **"Sign In"**
5. ✅ Você deve ser logado e ver o Kanban board

**O que está sendo testado:**
- ✅ JWT authentication
- ✅ Token storage em localStorage
- ✅ Protected routes

---

### TESTE 2: Kanban Board Básico

1. Veja o **Kanban board** com 5 colunas padrão:
   - To Do
   - In Progress
   - In Review
   - Done
   - Backlog

2. Observe **8 cards de exemplo** distribuídos

3. **Teste renomear coluna:**
   - Clique no título de qualquer coluna
   - Digite novo nome
   - Pressione Enter
   - ✅ Título deve atualizar

**O que está sendo testado:**
- ✅ API GET /boards
- ✅ API PUT /columns/{id}
- ✅ Board state management
- ✅ Otimistic updates

---

### TESTE 3: Drag & Drop de Cards

1. **Arraste um card** de uma coluna para outra
2. Solte o card
3. ✅ Card deve aparecer na nova coluna
4. **Recarregue a página** (Cmd+R)
5. ✅ Card deve estar na mesma posição (persistido no DB)

**O que está sendo testado:**
- ✅ @dnd-kit drag-drop
- ✅ API PUT /cards/{id}/position
- ✅ Database persistence
- ✅ Otimistic UI updates

---

### TESTE 4: Criar e Deletar Cards

1. **Criar novo card:**
   - Clique em qualquer coluna
   - Clique no botão "+ Add Card"
   - Digite título (ex: "Testar AI")
   - Digite detalhes (ex: "Chat com AI")
   - Clique "Add"
   - ✅ Card aparece na coluna

2. **Deletar card:**
   - Hover sobre um card
   - Clique no ícone de lixeira
   - ✅ Card desaparece

**O que está sendo testado:**
- ✅ API POST /cards
- ✅ API DELETE /cards/{id}
- ✅ Cascade deletes
- ✅ Component updates

---

### TESTE 5: AI Chat Sidebar 🤖

Este é o teste mais interessante!

#### 5A: Abrir Sidebar de Chat

1. Na **direita da tela**, veja o **"AI Assistant"** sidebar
2. Observe a mensagem de boas-vindas com exemplos
3. ✅ Sidebar carrega corretamente

#### 5B: Enviar Mensagem Simples

1. Clique no **campo de input** no fundo do sidebar
2. Digite: `"What tasks are in the backlog?"`
3. Clique **"Send"** ou pressione **Enter**
4. ✅ Você vê o loading indicator (3 pontos animados)
5. ✅ IA responde descrevendo as tasks

**O que está sendo testado:**
- ✅ API POST /boards/{id}/ai
- ✅ Board context loading
- ✅ Conversation history retrieval
- ✅ OpenRouter AI integration
- ✅ Message display

#### 5C: IA Cria um Card (Teste Principal!)

1. Digite no sidebar: `"Create a new task called 'Test Automation' in the To Do column"`
2. Clique **Send**
3. ✅ IA responde que criou o task
4. ✅ **Kanban board auto-refresh**
5. ✅ **Novo card aparece na coluna To Do**

**O que está sendo testado:**
- ✅ Structured outputs parsing
- ✅ AI action application
- ✅ Card creation via AI
- ✅ Board refresh signal
- ✅ Conversation persistence

#### 5D: IA Move Cardss

1. Digite: `"Move the 'Implement API' card to 'In Progress'"`
2. Clique **Send**
3. ✅ IA confirma o movimento
4. ✅ Card move para a coluna In Progress
5. ✅ Kanban atualiza automaticamente

#### 5E: Testar Histórico de Conversa

1. Digite: `"What have you done so far?"`
2. Clique **Send**
3. ✅ IA lembra das ações anteriores
4. ✅ Mensagem anterior aparece na conversa
5. ✅ IA faz referência aos tasks criados

**O que está sendo testado:**
- ✅ Conversation history persistence
- ✅ Multi-turn conversations
- ✅ Context awareness

#### 5F: Testar Clear History

1. Clique no botão **"CLEAR HISTORY"** no sidebar
2. ✅ Todas as mensagens desaparecem
3. ✅ Sidebar volta ao estado vazio

---

### TESTE 6: Testes de Erro

#### 6A: Deslogar e Reconectar

1. Clique no botão **"Logout"** no canto superior direito
2. ✅ Você volta à página de login
3. Login novamente
4. ✅ Seu board está intacto

#### 6B: Testar Desconexão de Internet

1. Abra DevTools (F12)
2. Vá para Network
3. Marque **"Offline"**
4. Tente enviar mensagem no chat
5. ✅ Erro é exibido apropriadamente
6. Desmarque **"Offline"**
7. Tente novamente
8. ✅ Mensagem é enviada com sucesso

**O que está sendo testado:**
- ✅ Network error handling
- ✅ Error messages
- ✅ Reconnection logic

---

## 🧬 Testar API Diretamente (com cURL)

Se quiser testar a API sem usar a UI:

### Login e Obter Token

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"paulo","password":"teste123"}'
```

**Resposta:**
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer"
}
```

### Listar Boards

```bash
TOKEN="seu_token_aqui"
curl http://localhost:8000/api/boards \
  -H "Authorization: Bearer $TOKEN"
```

### Enviar Mensagem para AI

```bash
TOKEN="seu_token_aqui"
BOARD_ID="1"

curl -X POST http://localhost:8000/api/boards/$BOARD_ID/ai \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message":"Create a task called Test"}'
```

**Resposta:**
```json
{
  "response": "I've created a task called 'Test' in the To Do column.",
  "actions": [
    {
      "type": "create",
      "column_id": 1,
      "title": "Test",
      "details": ""
    }
  ],
  "actions_applied": {
    "successful": [{"type": "create", "card_id": 42}],
    "failed": []
  },
  "confidence": 0.95,
  "tokens_used": 1200
}
```

---

## 🧪 Executar Testes Automatizados

### Backend Tests

```bash
cd backend

# Rodar todos os testes
pytest

# Com mais detalhes
pytest -v

# Apenas testes de AI
pytest test_ai.py -v

# Apenas testes de auth
pytest test_auth.py -v

# Com coverage
pytest --cov=. --cov-report=html
```

### Frontend Tests

```bash
cd frontend

# Unit tests
npm run test

# E2E tests com Playwright
npm run test:e2e

# Com interface visual
npm run test:e2e -- --ui
```

---

## 🐳 Testar com Docker (Opcional)

Se preferir rodar tudo containerizado:

```bash
# Na raiz do projeto
docker-compose up

# Frontend: http://localhost:3000
# Backend: http://localhost:8000
```

---

## 🔍 Checklist de Testes Completo

- [ ] **Login/Logout**
  - [ ] Login com qualquer username
  - [ ] Token salvo em localStorage
  - [ ] Logout limpa o token
  - [ ] Protected route redireciona sem token

- [ ] **Kanban Board**
  - [ ] Carrega 5 colunas padrão
  - [ ] Carrega 8 cards de exemplo
  - [ ] Renomear coluna funciona
  - [ ] Adicionar card funciona
  - [ ] Deletar card funciona

- [ ] **Drag & Drop**
  - [ ] Arrastar card entre colunas
  - [ ] Posição persiste após reload
  - [ ] Visual feedback correto
  - [ ] Ordem de cards mantida

- [ ] **AI Chat**
  - [ ] Sidebar aparece
  - [ ] Enviar mensagem funciona
  - [ ] IA responde corretamente
  - [ ] **IA cria cards** ⭐
  - [ ] **IA move cards** ⭐
  - [ ] **Kanban auto-refresh** ⭐
  - [ ] Histórico persiste
  - [ ] Clear history funciona

- [ ] **Tratamento de Erros**
  - [ ] Mensagens de erro claras
  - [ ] Reconexão automática
  - [ ] Otimistic updates revertem
  - [ ] Offline mode testado

---

## 🐛 Se Algo Não Funcionar

### Backend não inicia?

```bash
# Verificar se Python está instalado
python --version

# Verificar se porta 8000 está em uso
lsof -i :8000

# Matar processo na porta
kill -9 <PID>

# Tentar novamente
python -m uvicorn main:app --reload --port 8000
```

### Frontend não carrega?

```bash
# Limpar cache Next.js
rm -rf frontend/.next

# Reinstalar dependências
cd frontend
rm -rf node_modules package-lock.json
npm install

# Rodar novamente
npm run dev
```

### AI não responde?

```bash
# Verificar se API key está configurada
echo $OPENROUTER_API_KEY

# Testar conectividade
curl -X POST http://localhost:8000/api/ai/test
```

### Banco de dados vazio?

O banco é criado automaticamente com dados de exemplo. Se quiser resetar:

```bash
# Deletar banco
rm backend/pm.db

# Reiniciar backend - vai recriado
python -m uvicorn main:app --reload
```

---

## 📱 Fluxo de Teste Recomendado (10 minutos)

1. **Login** (30 segundos)
2. **Explorar Kanban** (2 minutos)
3. **Arrastar card** (1 minuto)
4. **Criar card manualmente** (1 minuto)
5. **Testar AI simples** (2 minutos): `"What tasks are in the backlog?"`
6. **Testar AI criando card** (2 minutos): `"Create a task called AI Test in To Do"`
7. **Testar AI movendo card** (1 minuto): `"Move AI Test to In Progress"`
8. **Deletar e fazer logout** (1 minuto)

**Total: ~10 minutos para validar tudo funcionando!**

---

## ✨ Recursos para Testar

Aqui estão prompts úteis para testar a IA:

### Simples (Leitura)
- `"What tasks are in my backlog?"`
- `"How many cards are in the In Progress column?"`
- `"Summarize my current board state"`

### Intermediário (Criação)
- `"Create a new task called 'Code Review' in To Do"`
- `"Add a bug fix task to the Backlog"`
- `"I need a new card for team meeting"`

### Avançado (Múltiplas Ações)
- `"Create three new tasks: Design Update, Bug Fix, and Testing, all in To Do"`
- `"Move all cards from Backlog to To Do"`
- `"Update the Test Automation card title to Implement Testing Framework"`

### Conversação
- `"What did I ask you to create earlier?"`
- `"Show me what's changed on my board since we started chatting"`

---

## 🎯 Validar Estrutura de Resposta AI

A resposta do AI deve ter este formato:

```json
{
  "response": "string com a mensagem para o usuário",
  "actions": [
    {
      "type": "create|update|move|delete",
      "card_id": 1,
      "column_id": 1,
      "title": "string",
      "details": "string",
      "position": 0
    }
  ],
  "actions_applied": {
    "successful": [...],
    "failed": [...]
  },
  "confidence": 0.0-1.0,
  "tokens_used": 1200
}
```

---

## 📞 Precisa de Ajuda?

Se algo não funcionar:

1. Verifique os **console logs** (F12)
2. Verifique os **backend logs** (terminal onde rodou uvicorn)
3. Confira se **API Key está configurada**
4. Tente **resetar o banco de dados**
5. Verifique se **portas 3000 e 8000 não estão em uso**

---

**Bom teste! 🚀**
