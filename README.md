# vosk-ditador

Ditado por voz e ata automática de reuniões — tudo local, em português, rodando na sua GPU NVIDIA.

Dois apps independentes:

- **Ditador** (`vosk_global.py`) — `Ctrl+Alt+E` ouve/pausa em qualquer app; ao pausar a fala,
  cola a frase já pontuada onde você está digitando. Um orbe translúcido dá feedback visual.
- **Modo reunião** (`reuniao.py`) — grava seu microfone e o áudio do sistema em canais separados;
  ao parar, produz sozinho a cadeia: áudio FLAC → **ata** com timestamps e falantes → **resumo
  executivo** por LLM (em português, mesmo se a reunião foi em outro idioma).

No caminho padrão o áudio **nunca sai do PC** — transcrição e diarização rodam na sua GPU.
Os únicos toques de nuvem são opcionais: o resumo (vai só o *texto* da ata) e a re-transcrição
via Groq.

## Instalação

```powershell
pip install -r requirements.txt
python setup_model.py        # small 32.5MB; --big p/ fb 1.6GB (melhor precisão)
cp .env.example .env         # e preencha as chaves que for usar (abaixo)
```

**GPU**: o ditador e o modo reunião usam faster-whisper (CUDA com fallback CPU automático).
A diarização exige PyTorch com kernels da sua placa — em RTX série 50 (Blackwell):

```powershell
pip install torch==2.14.1 torchvision==0.29.1 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130
```

**Diarização (uma vez)**: `huggingface-cli login` + aceitar os termos dos repositórios
[`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
e `pyannote/segmentation-3.0` (gratuito; os pesos ficam em cache local).

## Configuração (`.env`)

Toda a configuração vive no `.env` (gitignored) — `.env.example` é o mapa completo. Resumo:

| variável | usada por | obrigatória? |
|---|---|---|
| `QUEBRAGALHO_API_KEY` | resumo da ata | só p/ resumo (sem ela a ata sai igual) |
| `GROQ_API_KEY` | `transcrever --groq` | não (é opcional) |
| `QG_MODELO` | resumo | não (padrão `qwen3.8-omni-flash` + fallback `muse-spark-1.3-contributor`) |
| `REUNIAO_MIC` | gravar | não (padrão: mic padrão do Windows) |
| `REUNIAO_HOTKEY_GRAVAR` / `REUNIAO_HOTKEY_SAIR` | gravar | não (padrão Ctrl+Alt+R / Ctrl+Alt+W) |
| `VOSK_DEVICE` | ditador | não (padrão 21 = AirPods WASAPI 16k) |
| `VOSK_MODEL` | `vosk_live.py` | não |

## Uso — ditador

```powershell
python vosk_global.py             # Ctrl+Alt+E ouve/pausa, Ctrl+Alt+Q encerra
python vosk_global.py --preview   # só o orbe animado (12s), sem mic/modelo
python vosk_live.py               # transcrição no console (Vosk)
python test_pontuar.py            # check da heurística de "?"
```

A 1ª ativação baixa o Whisper medium da Hugging Face. Sem GPU, cai pra CPU int8 sozinho.

## Uso — modo reunião

```powershell
python reuniao.py                # Ctrl+Alt+R grava/pausa (bolinha vermelha), Ctrl+Alt+W encerra
python reuniao.py --teste-captura 10   # valida mic+loopback por 10s, sem GUI
```

Ao pausar: salva `reunioes/<data-hora>.flac` (~350 MB/h), transcreve (Whisper large-v3-turbo,
1h ≈ 1 min) e gera `<mesmo-nome>.md` + `<mesmo-nome>_resumo.md`. O encerramento (Ctrl+Alt+W)
espera a ata e o resumo terminarem — Ctrl+Alt+Q é do ditador de propósito, pra fechar um app
sem derrubar o outro.

Re-transcrição e opções, reunião por reunião:

```powershell
python reuniao.py transcrever <arquivo>                          # local, pt
python reuniao.py transcrever <arquivo> --diarizar               # separa remotos por voz
python reuniao.py transcrever <arquivo> --diarizar --idioma en   # áudio em outro idioma
python reuniao.py transcrever <arquivo> --groq                   # via nuvem
python reuniao.py resumir <ata.md>                               # refaz só o resumo
```

**Falantes**: canal esquerdo = "Você" (seu mic), direito = "Remotos" (loopback do sistema —
funciona com fone Bluetooth). Com `--diarizar`, os remotos viram Remoto 1, 2, ... por voz.
Se a diarização falhar, a ata sai com "Remotos" como sempre — nada quebra.

### De um vídeo/áudio existente

```powershell
yt-dlp -f bestaudio -o video.webm <URL>
ffmpeg -i video.webm -ar 48000 -ac 1 mono.wav
python -c "import soundfile as sf, numpy as np; m,sr=sf.read('mono.wav',dtype='int16'); e=np.zeros((len(m),2),dtype=np.int16); e[:,1]=m; sf.write('reunioes/video.flac',e,sr,subtype='PCM_16')"
python reuniao.py transcrever reunioes/video.flac --diarizar --idioma en
python reuniao.py resumir reunioes/video.md
```

## Como funciona

```
mic (L) ─┐
         ├─ estéreo 48k ── FLAC crash-safe ──> Whisper large-v3-turbo ──> ata .md ──> resumo LLM
sistema (R)─ loopback WASAPI ┘        (WAV incrementais)   + pyannote (--diarizar)     (qwen → muse)
```

- Gravação em WAV incrementais (~1s de perda máxima num crash), mesclados ao pausar.
  Ambos os canais seguem o **relógio de parede**: qualquer tempo morto (fone que dormiu,
  troca de dispositivo) vira silêncio compensatório, então os canais nunca dessincronizam.
- Escrita atômica dos `.md`; retentativas com backoff nas chamadas de rede.
- Resumo sai sempre em PT-BR (mesmo com ata em inglês), com decisões, encaminhamentos
  (responsável/prazo quando citados) e pontos em aberto — com instrução explícita de não inventar fatos.

## Testes

```powershell
python tests/test_reuniao.py     # funções puras: alinhamento de canais, diarização, mescla
```

## Privacidade

- **Áudio nunca sai do PC** no caminho padrão (transcrição e diarização 100% locais).
- O **texto** da ata vai para a API só no passo do resumo; sem chave, o resumo é pulado.
- `--groq` envia áudio para a nuvem — é exceção explícita, reunião por reunião.
- `.env`, `reunioes/`, `models/` e `teste.py` estão no `.gitignore`.

## Estado e limitações conhecidas

- O encerramento do gravador espera a ata/resumo terminarem (teto de 15 min; Ctrl+C no
  console força a saída — o FLAC fica pra rodar `transcrever` manual depois).
- O alinhamento por relógio de parede tem tolerância de ~0,2 s de deriva entre canais
  (irrelevante pra ata; relógios de dispositivos Bluetooth derivam mais que isso).
- Com `--groq` + `--diarizar` a separação de falantes é por trecho (a API do Groq não
  expõe timestamps por palavra) — menos fina que o caminho local.
- Nomes de remotos são genéricos (Remoto 1, 2, ... por tempo de fala); associar a pessoas
  reais é edição manual na ata.
- A validação ao vivo (hotkeys, bolinha, troca de fone em plena gravação) é manual —
  o processo em background desta máquina não expõe GUI/teclado global.

## Estrutura

| arquivo | papel |
|---|---|
| `vosk_global.py` | ditador global (orbe + hotkeys + Whisper medium) |
| `reuniao.py` | gravador de reuniões (hotkey + bolinha, mic + loopback, cadeia automática) |
| `reuniao_transcrever.py` | motor de ata: Whisper/Groq + diarização pyannote + Markdown |
| `reuniao_resumo.py` | resumo executivo da ata via LLM (quebragalho) |
| `tests/test_reuniao.py` | testes das funções puras (alinhamento, diarização, mescla) |
| `vosk_live.py`, `setup_model.py`, `test_pontuar.py` | utilitários Vosk (console/modelo/teste) |
| `.env.example` | mapa de toda a configuração |
