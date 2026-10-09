# Comandos de voz no ditador (wake-word + cascata local → JEV)

Data: 2026-10-09. Status: desenho aprovado pelo dono; aguarda revisão desta spec
e `OPENROUTER_API_KEY` válida para a parte JEV.

## Intenção

Ditar `"computador, pausar música"` executa a ação em vez de digitar.
v1 = controle de mídia + abrir sites/apps pelo nome. Todo o resto continua
ditado normal. O que o dono disse vs. premissas: fail-safe (comando duvidoso
vira texto digitado, nunca ação errada); sem key/rede, os comandos locais de
mídia seguem offline.

## Arquitetura

Um módulo novo (`comandos.py`) plugado num único ponto do fluxo existente:
`vosk_global.bombeia`, ramo `"texto"`, depois de `pontuar`, antes de `deliver`.
Nenhuma mudança no engine, no VAD ou na colagem.

```
frase pontuada → começa com wake? ─não→ deliver (como hoje)
        ─sim→ tira prefixo → match local de mídia? ─sim→ executa
        ─não→ tem OPENROUTER_API_KEY? ─não→ deliver sem o prefixo
        ─sim→ JEV Choice (comandos + ditado) → confiança ≥ limiar? ─sim→ executa
        ─não→ deliver sem o prefixo
```

## Componentes (`comandos.py`, único arquivo novo)

- `WAKE`: palavra de ativação, padrão `"computador"`, configurável via
  `COMANDO_WAKE` no `.env`. Detecção: prefixo case-insensitive seguido de
  vírgula/espaço; o prefixo é removido antes de qualquer caminho.
- `LOCAIS`: dict palavra-chave → ação de mídia via `keyboard.send`
  (dependência já usada): pausar/continuar (`play/pause media`),
  próxima/anterior (`next/track`, `prev/track`), volume ±/mudo. Zero rede.
- `decidir(frase, chave)`: um `POST` ao Decisions API
  (`https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`,
  `state={frase}`, uma pergunta `Choice` com opções = comandos v1 + `ditado`).
  Retorna `(comando, confiança, custo)` ou `None` em qualquer falha.
  Retry como `_groq_post`: 1 retentativa rápida; depois, falha aberta.
- `executar(comando)`: media keys; mapa fechado nome → destino
  (`youtube` → `https://www.youtube.com`, etc. via `webbrowser` stdlib;
  programas locais via `os.startfile`). Sem shell arbitrário, sem URL fora
  de `https:`, lista de comandos fechada no código.
- `tratar(frase) -> bool`: orquestra a cascata acima; `True` = executou.

## Fluxo de dados

Texto só sai da máquina em dois pontos já existentes (transcrição local) mais
um novo: a frase com wake vai ao Decisions API (state mínimo: a frase).
Resposta JEV: `choice` + `confidence` + `usage.cost` (logado no console).

## Erros

Sem key, timeout, 429/5xx ou resposta fora do schema → `None` → ditado
normal. 4xx definitivo (key/modelo) → 1 aviso no console, depois silencioso
(falha aberta permanente até reiniciar). O ditado nunca quebra por causa do
cérebro. Key via `.env` (`OPENROUTER_API_KEY`, gitignored como as demais).

## Limiar

Confiança JEV ≥ 0.6 executa (inicial; calibrar depois com amostra rotulada,
como manda o cookbook do JEV). Abaixo disso, ditado. Direção do erro é
segura: comando perdido vira texto; ação errada exige confiança alta.

## Testes

Suite offline existente (`python -m unittest discover -s tests`): strip do
wake, match local PT (incl. variações "pausar música"/"pausa a música"),
fallback sem key (sempre ditado), `decidir` com HTTP mockado (200/401/timeout),
`deliver` intacto. JEV ao vivo só em teste manual com key válida
(`jev_probe.py`-style em `/tmp`, nunca no repo).

## Fora do escopo v1

Relaxar a trava de título da colagem; confirmação falada; parâmetros livres
("volume 30", "abre X" fora do mapa); encadear comandos; 9router como
transporte (fala só chat-completions; o OpenRouter recusa JEV nesse
protocolo — verificado ao vivo em 2026-10-09).

## v2 (aprovado 2026-10-09)

- **tocar_musica**: sem API key — `yt-dlp "ytsearch1:X" --flat-playlist
  --print id` resolve busca→ID; `mpv --no-video --idle` com IPC
  (`--input-ipc-server`) toca em segundo plano e vira fila
  (`loadfile ... append-play`). Falha na extração → abre a página de busca
  no navegador (fail-open). Latência esperada ~3-6s.
- **Ponte de pause**: com o demônio mpv vivo, pausar/continuar/próxima/
  anterior roteiam para o IPC (`cycle pause`, `playlist-next/prev`); sem
  ele, media keys como hoje. "Toca X" e depois "pausar música" funciona.
- **Respostas faladas**: Edge-TTS (`pt-BR-AntonioNeural`, já instalado) +
  `playsound` (1 dep nova) em thread daemon; fala o status de cada comando
  e as respostas; `COMANDO_VOZ=0` desliga. SAPI descartado (só vozes EN).
- **abrir_programa**: mapa nome→executável + `os.startfile`
  (notepad/calc/mspaint); miss → ditado.
- **dizer_horas** (hora local, fala+status) e **pesquisar_web** (resto →
  busca Google).
- JEV: +4 opções (`tocar_musica`, `abrir_programa`, `dizer_horas`,
  `pesquisar_web`); limiar 0.6 mantido; query/parâmetro sai do resto por
  regex, nunca do JEV (JEV não extrai entidades).
- Fora do v2: Apple Music/Spotify (sem API prática no Windows sem conta
  de dev paga/OAuth); YouTube Data API dispensada (yt-dlp keyless).
