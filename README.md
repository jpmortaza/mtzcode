# mtzcode

> Assistente de código no terminal, **100% local por padrão**, com agent loop e tool calling.
> Sem APIs externas obrigatórias, sem custos por uso, sem código saindo da sua máquina.

```
███╗   ███╗████████╗███████╗ ██████╗ ██████╗ ██████╗ ███████╗
████╗ ████║╚══██╔══╝╚══███╔╝██╔════╝██╔═══██╗██╔══██╗██╔════╝
██╔████╔██║   ██║     ███╔╝ ██║     ██║   ██║██║  ██║█████╗
██║╚██╔╝██║   ██║    ███╔╝  ██║     ██║   ██║██║  ██║██╔══╝
██║ ╚═╝ ██║   ██║   ███████╗╚██████╗╚██████╔╝██████╔╝███████╗
╚═╝     ╚═╝   ╚═╝   ╚══════╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
                              by Jean Mortaza
```

## O que é

Um clone enxuto do estilo Claude Code, mas que conversa com um modelo rodando **localmente** via [Ollama](https://ollama.com) — usando a capacidade de processamento do seu próprio notebook. Por padrão, **nada sai da sua máquina**.

Tem um sistema de **profiles** que permite trocar entre modelos locais e (opcionalmente) cloud (Groq) sem reiniciar.

### Capacidades

- ✅ REPL interativo no terminal com UI bonita (Rich)
- ✅ **Agent loop** com tool calling iterativo (estilo Claude Code)
- ✅ **6 tools nativas:** `read`, `write`, `edit`, `bash`, `glob`, `grep`
- ✅ **Múltiplos perfis de modelo** com troca quente via `/modelo`
- ✅ Funciona offline com modelos open-source quantizados
- ✅ Fallback de parsing de tool calls (compatível com modelos Q4 que não usam tags estruturadas)
- ✅ Validação de argumentos via Pydantic
- ✅ Mensagens em **português brasileiro**

## Por que existe

Pra ter um assistente de código que:

- 🔒 Não consome créditos de API paga (Anthropic, OpenAI)
- 🌐 Funciona **offline** — só precisa do Ollama rodando local
- 🤐 Mantém todo o código privado — nada vai pra cloud por padrão
- 🎓 Te ensina como esses sistemas funcionam por dentro

---

## Pré-requisitos

- **macOS com Apple Silicon** (testado em M4, 16 GB) — funciona em outros Macs e Linux com pequenas adaptações
- [Homebrew](https://brew.sh)
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (gerenciador de pacotes Python)

> 💡 **Hardware mínimo recomendado:** 16 GB RAM pra rodar Qwen 2.5 Coder 14B confortavelmente. Com 8 GB use o profile `qwen-7b`.

## Instalação

### 1. Instale Ollama, ripgrep e baixe os modelos

```bash
brew install ollama ripgrep
brew services start ollama
ollama pull qwen2.5-coder:14b   # ~9 GB — modelo principal
ollama pull qwen2.5-coder:7b    # ~5 GB — alternativa leve (opcional)
```

### 2. Clone e instale o mtzcode

```bash
git clone https://github.com/jpmortaza/mtzcode.git ~/mtzcode
cd ~/mtzcode
uv venv
uv pip install -e .
```

### 3. Teste

A partir do diretório do projeto:

```bash
./bin/mtzcode
```

Vai abrir o REPL interativo. Digite uma pergunta:

```
você › liste os arquivos .py deste diretório
```

### 4. (Opcional) Adicione ao PATH

Pra chamar `mtzcode` de qualquer lugar:

```bash
echo 'export PATH="$HOME/mtzcode/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
mtzcode
```

> ℹ️ **Por que o wrapper `bin/mtzcode`?** O cpython distribuído pelo `uv` não processa
> arquivos `.pth` de instalações editáveis no startup, então o entry-point gerado
> pelo `uv pip install -e .` quebra. O wrapper injeta `PYTHONPATH` e chama
> `python -m mtzcode`, contornando o problema.

---

## Uso

### Comandos do REPL

| Comando | Ação |
|---|---|
| `/sair` | Encerra a sessão |
| `/limpar` | Zera o histórico da conversa |
| `/modelo` | Abre menu pra trocar de perfil de modelo (troca quente, mantém histórico) |
| `/ajuda` | Mostra ajuda e tools disponíveis |
| `Ctrl+C` ou `Ctrl+D` | Encerra |

### Subcomandos da CLI

```bash
mtzcode                       # abre o REPL com o profile padrão
mtzcode chat                  # idem
mtzcode chat -p qwen-7b       # abre o REPL com profile específico
mtzcode profiles              # lista todos os perfis disponíveis
mtzcode version               # mostra a versão
```

### Exemplos de pedido

```
você › leia o arquivo mtzcode/agent.py e me explique o que faz

você › encontre todos os arquivos com a palavra "TODO"

você › crie um arquivo hello.py com uma função fizzbuzz

você › rode o comando "git status" e me diga o que tá pendente

você › qual o maior arquivo .py deste projeto?
```

O agent decide sozinho que tools usar, executa, processa o resultado e responde.

---

## Perfis de modelo

| Perfil | Backend | Modelo | Tipo | Uso recomendado |
|---|---|---|---|---|
| `qwen-14b` | Ollama (local) | qwen2.5-coder:14b | 🟢 local | **Default.** Melhor qualidade. ~25 tok/s no M4. |
| `qwen-7b` | Ollama (local) | qwen2.5-coder:7b | 🟢 local | Mais rápido (~50 tok/s), bom pra tarefas simples. |
| `groq-llama` | Groq cloud | llama-3.3-70b-versatile | 🔴 cloud | Super rápido (~300 tok/s). **Atenção: dados saem da máquina.** |

### Trocar de perfil dentro do REPL

```
você › /modelo

Perfis disponíveis:
  ● 1. Qwen 2.5 Coder 14B (local)
      Modelo grande local. Melhor qualidade.
    2. Qwen 2.5 Coder 7B (local, leve)
      Modelo leve local. Mais rápido.
    3. Llama 3.3 70B (Groq cloud)
      Cloud, super rápido. ATENÇÃO: dados saem da máquina.

escolha um perfil ›  2
✓ agora usando Qwen 2.5 Coder 7B (histórico preservado)
```

### Usar Groq (cloud, opcional)

Pra usar o profile `groq-llama` você precisa de uma API key gratuita do Groq:

1. Crie uma conta em [console.groq.com](https://console.groq.com)
2. Gere uma API key
3. Adicione ao `~/.zshrc`:
   ```bash
   export GROQ_API_KEY=gsk_...
   ```
4. Reinicie o terminal e use `mtzcode chat -p groq-llama`

---

## Configuração

Variáveis de ambiente:

| Var | Padrão | Descrição |
|---|---|---|
| `MTZCODE_PROFILE` | `qwen-14b` | Perfil padrão (use `mtzcode profiles` pra ver opções) |
| `MTZCODE_TIMEOUT` | `300` | Timeout HTTP em segundos |
| `GROQ_API_KEY` | — | Necessária pro perfil `groq-llama` |

---

## Estrutura do projeto

```
mtzcode/
├── bin/mtzcode              # wrapper bash (PATH-friendly)
├── pyproject.toml
├── README.md
├── .python-version
├── mtzcode/
│   ├── __init__.py
│   ├── __main__.py          # python -m mtzcode
│   ├── cli.py               # Typer + Rich REPL
│   ├── agent.py             # agent loop com tool calling
│   ├── client.py            # ChatClient (OpenAI-compat, Ollama + Groq)
│   ├── config.py            # carregamento de config
│   ├── profiles.py          # perfis de modelo pré-definidos
│   ├── prompts/
│   │   └── system.md        # system prompt em português
│   └── tools/
│       ├── base.py          # Tool ABC + ToolRegistry
│       ├── read.py
│       ├── write.py
│       ├── edit.py
│       ├── bash.py
│       ├── glob.py
│       └── grep.py
└── tests/
```

---

## Roadmap

- [x] **Fase 0** — Pré-requisitos (Ollama, modelo)
- [x] **Fase 1** — Scaffold + chat REPL
- [x] **Fase 2** — Tools básicas (read, write, edit, bash, glob, grep)
- [x] **Fase 3** — Agent loop com tool calling
- [x] **Profiles** — Múltiplos modelos com `/modelo`
- [ ] **Fase 4** — Streaming de tokens, diffs coloridos, confirmação destrutiva
- [ ] **Fase 5** — Slash commands customizados, plan mode, MCP client
- [ ] **Fase 6** — RAG sobre o projeto (embeddings locais + sqlite-vec)
- [ ] **Fase 7** — LoRA fine-tuning sobre logs de uso (especialização)

---

## Limitações conhecidas

- Modelos open-source quantizados (Q4) podem ser inconsistentes em tool calling complexo. O fallback parser cobre os casos comuns.
- Sem streaming de tokens ainda — você espera a resposta completa.
- Sem persistência de histórico entre sessões.
- Tool `bash` não tem confirmação antes de executar — cuidado com comandos destrutivos.

## Contribuindo

Issues e PRs são bem-vindos. O projeto é minimalista por design — features grandes deveriam ser discutidas em issue antes.

---

**Construído por [Jean Mortaza](https://github.com/jpmortaza).**
