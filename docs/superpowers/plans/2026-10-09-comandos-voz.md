# Comandos de voz Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ditar `"computador, pausar música"` executa a ação; resto segue ditado normal.

**Architecture:** Novo `comandos.py` (wake-strip → match local → JEV Choice → executar) plugado num único ponto: ramo `"texto"` do `bombeia` em `vosk_global.py`, depois de `pontuar`, antes de `deliver`. Falha sempre aberta para ditado.

**Tech Stack:** Python 3.13, stdlib (`webbrowser`, `unicodedata`) + deps já instaladas (`keyboard`, `requests`). Runner: `python -m unittest discover -s tests` na raiz do projeto.

**Spec:** `docs/superpowers/specs/2026-10-09-comandos-voz-design.md`

## Global Constraints

- Windows: media keys via `keyboard`, programas locais via `os.startfile`.
- Zero dependências novas (`requests`, `keyboard` já no projeto).
- `.env` é gitignored; segredos nunca commitados nem impressos.
- `load_settings()` com `override=False` (convenção do repo); env do processo vence `.env`.
- Falha de qualquer estágio do cérebro → ditado normal, nunca exceção no `bombeia`.
- Testes offline em `tests/test_comandos.py` (unittest, sem rede/mic/modelo).

## Review Focus

- `"Computador, pausar música."` (saída do `pontuar`: maiúscula + ponto final) ainda dispara comando — teste em Task 1.
- `"musica"` sem acento casa igual a `"música"` — teste em Task 1.
- JEV escolhe comando com confiança 0.4 → dita, não executa — teste em Task 3.
- Rede fora/timeout/401 → dita, sem exceção — teste em Task 2 e 3.
- `"computador, abrir banco"` (fora do mapa) → dita, nunca URL chutada — teste em Task 1 e 3.

---

### Task 1: Núcleo local puro (`tira_wake`, `normaliza`, `match_local`, `executar`)

**Files:**
- Create: `comandos.py`
- Test: `tests/test_comandos.py`

**Interfaces:**
- Consumes: nada (primeira task).
- Produces: `tira_wake(texto, wake="computador") -> str | None`; `normaliza(s) -> str`; `match_local(frase) -> str | None` (id do comando ou None); `executar(comando) -> str` (mensagem p/ bandeja); `COMANDOS: set[str]`; `ABRIR: dict[str, str]` (nome → URL https ou `app:<nome>`).

- [ ] **Step 0: Confirmar nomes das media keys (2 min)**

Run: `python -c "import keyboard; [print(k, keyboard.key_to_scan_codes(k)) for k in ['play/pause media','next track','previous track','volume up','volume down','volume mute']]"` na raiz do projeto.
Expected: imprime scancodes sem exceção. Se algum nome falhar, usa o nome aceito pelo `keyboard` e ajusta o mapa do Step 2 (anota o nome real no commit).

- [ ] **Step 1: Escrever os testes que falham**

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import comandos


class TestWake(unittest.TestCase):
    def test_pontuado_com_maiuscula_e_ponto(self):
        self.assertEqual(comandos.tira_wake("Computador, pausar música."), "pausar música.")

    def test_sem_wake_devolve_none(self):
        self.assertIsNone(comandos.tira_wake("preciso comprar pão."))

    def test_wake_customizado(self):
        self.assertEqual(comandos.tira_wake("Jarvis, próxima.", wake="jarvis"), "próxima.")


class TestMatchLocal(unittest.TestCase):
    def test_variacoes_pausar(self):
        for f in ("pausar música", "pausa a música", "pause", "para a música"):
            self.assertEqual(comandos.match_local(f), "pausar_musica", f)

    def test_sem_acento_casa(self):
        self.assertEqual(comandos.match_local("pausar musica"), "pausar_musica")
        self.assertEqual(comandos.match_local("proxima musica"), "proxima_faixa")

    def test_outros_de_midia(self):
        self.assertEqual(comandos.match_local("continuar"), "continuar_musica")
        self.assertEqual(comandos.match_local("faixa anterior"), "faixa_anterior")
        self.assertEqual(comandos.match_local("aumentar o volume"), "volume_mais")
        self.assertEqual(comandos.match_local("diminuir volume"), "volume_menos")
        self.assertEqual(comandos.match_local("deixar mudo"), "volume_mudo")

    def test_fora_do_mapa_local_e_none(self):
        self.assertIsNone(comandos.match_local("abrir o youtube"))
        self.assertIsNone(comandos.match_local("que horas são"))


class TestExecutar(unittest.TestCase):
    def test_midia_chama_keyboard(self):
        chamadas = []
        comandos._teclado = lambda k: chamadas.append(k)
        try:
            msg = comandos.executar("pausar_musica")
        finally:
            del comandos._teclado
        self.assertEqual(chamadas, ["play/pause media"])
        self.assertIn("paus", msg.lower())

    def test_abrir_youtube(self):
        abertas = []
        comandos._abrir_url = lambda u: abertas.append(u)
        try:
            comandos.executar("abrir_youtube")
        finally:
            del comandos._abrir_url
        self.assertEqual(abertas, ["https://www.youtube.com"])

    def test_comando_invalido_levanta(self):
        with self.assertRaises(ValueError):
            comandos.executar("abrir_banco")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m unittest discover -s tests` na raiz do projeto.
Expected: `ModuleNotFoundError: No module named 'comandos'`.

- [ ] **Step 3: Implementação mínima (`comandos.py`)**

```python
"""Comandos de voz: wake-word, match local de mídia, JEV (decisão) e execução.
Nada de rede, microfone ou janela no import; `requests` só dentro de `decidir`."""
import os
import unicodedata
import webbrowser

WAKE_PADRAO = "computador"
LIMIAR_PADRAO = 0.6

MIDIA = {
    "pausar_musica": ("play/pause media", "Música pausada"),
    "continuar_musica": ("play/pause media", "Música retomada"),
    "proxima_faixa": ("next track", "Próxima faixa"),
    "faixa_anterior": ("previous track", "Faixa anterior"),
    "volume_mais": ("volume up", "Volume +"),
    "volume_menos": ("volume down", "Volume −"),
    "volume_mudo": ("volume mute", "Mudo alternado"),
}
# ponytail: play/pause é alternância — "continuar" com música tocando pausa;
# o usuário repete o comando; estado absoluto do player fica pro v2.

ABRIR = {
    "abrir_youtube": "https://www.youtube.com",
    "abrir_gmail": "https://mail.google.com",
    "abrir_whatsapp": "https://web.whatsapp.com",
    "abrir_github": "https://github.com",
}

COMANDOS = set(MIDIA) | set(ABRIR) | {"ditado"}


def normaliza(texto):
    """Minúsculas, sem acento, espaços colapsados, sem pontuação de borda."""
    t = unicodedata.normalize("NFKD", texto or "").lower()
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = " ".join(t.strip(" \t\n.,;:!?").split())
    return t


def tira_wake(texto, wake=WAKE_PADRAO):
    """Devolve o resto após 'wake,' / 'wake ' (case-insensitive) ou None."""
    t = (texto or "").strip()
    if t.casefold().startswith(wake.casefold()):
        resto = t[len(wake):].lstrip(" \t,:")
        return resto or None
    return None


def match_local(frase):
    """Id do comando de mídia por palavras-chave, ou None (vai pro JEV)."""
    n = " " + normaliza(frase) + " "
    if any(k in n for k in ("pausar", "pausa", "pause", "para a musica", "parar a musica")):
        return "pausar_musica"
    if any(k in n for k in ("continuar", "retomar", "seguir tocando", "play ")):
        return "continuar_musica"
    if "anterior" in n:
        return "faixa_anterior"
    if any(k in n for k in ("proxima", "seguinte", "pular musica")):
        return "proxima_faixa"
    if any(k in n for k in ("aumentar", "subir", "mais alto")) and "volume" in n:
        return "volume_mais"
    if any(k in n for k in ("diminuir", "baixar", "abaixar", "mais baixo")) and "volume" in n:
        return "volume_menos"
    if any(k in n for k in ("mudo", "mutar", "sem som", "silenciar")):
        return "volume_mudo"
    return None


def _teclado(tecla):
    import keyboard
    keyboard.send(tecla)


def _abrir_url(url):
    webbrowser.open(url)


def executar(comando):
    """Executa um id de COMANDOS; devolve mensagem p/ a bandeja."""
    if comando in MIDIA:
        tecla, msg = MIDIA[comando]
        _teclado(tecla)
        return msg
    if comando in ABRIR:
        _abrir_url(ABRIR[comando])
        return f"{comando[6:].capitalize()} aberto"
    raise ValueError(f"comando desconhecido: {comando}")
```

Nota: os pontos de injeção `_teclado`/`_abrir_url` são atributos de módulo
propositalmente (testes monkeypatch sem dependência nova).

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m unittest discover -s tests` na raiz do projeto.
Expected: todos passam (77 existentes + 12 novos ≈ 89 OK).

- [ ] **Step 5: Commit**

```bash
git add comandos.py tests/test_comandos.py
git commit -m "comandos de voz: wake-strip, match local de mídia e executar"
```

### Task 2: `decidir` via JEV (Decisions API, falha aberta)

**Files:**
- Modify: `comandos.py` (acrescenta `decidir`, `limiar`, `JEV_URL`)
- Test: `tests/test_comandos.py` (acrescenta classe `TestDecidir`)

**Interfaces:**
- Consumes: `COMANDOS` da Task 1.
- Produces: `decidir(frase, chave, timeout=20) -> tuple[str, float, float | None] | None` = `(comando, confiança, custo_usd)` ou None; `limiar() -> float` (env `COMANDO_CONFIANCA`, padrão 0.6, piso 0.0/teto 1.0, inválido → padrão); `JEV_URL`, `JEV_MODEL` (env `JEV_MODEL`, padrão `typesafe/jev-1.13`).

- [ ] **Step 1: Escrever os testes que falham**

```python
from unittest import mock
import requests


class TestDecidir(unittest.TestCase):
    def _resp(self, answers, cost=0.00001, status=200):
        r = mock.Mock(status_code=status)
        r.json.return_value = {"answers": answers, "usage": {"cost": cost}}
        return r

    def test_comando_com_confianca(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube",
                            "confidence": 0.9}}
        with mock.patch("requests.post", return_value=self._resp(ans)) as p:
            out = comandos.decidir("abrir o youtube", "k")
        self.assertEqual(out, ("abrir_youtube", 0.9, 0.00001))
        corpo = p.call_args.kwargs["json"]
        self.assertEqual(corpo["model"], "typesafe/jev-1.13")
        self.assertIn("ditado", corpo["questions"]["intencao"]["criteria"])

    def test_rede_fora_devolve_none(self):
        with mock.patch("requests.post", side_effect=requests.RequestException("dns")):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_401_devolve_none(self):
        r = mock.Mock(status_code=401)
        r.text = "User not found."
        with mock.patch("requests.post", return_value=r):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_schema_estranho_devolve_none(self):
        r = mock.Mock(status_code=200)
        r.json.return_value = {"ops": 1}
        with mock.patch("requests.post", return_value=r):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_opcao_fora_da_lista_devolve_none(self):
        ans = {"intencao": {"type": "choice", "choice": "formatar_pc", "confidence": 1.0}}
        with mock.patch("requests.post", return_value=self._resp(ans)):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_limiar_padrao_e_env(self):
        self.assertAlmostEqual(comandos.limiar(), 0.6)
        with mock.patch.dict("os.environ", {"COMANDO_CONFIANCA": "0.85"}):
            self.assertAlmostEqual(comandos.limiar(), 0.85)
        with mock.patch.dict("os.environ", {"COMANDO_CONFIANCA": "banana"}):
            self.assertAlmostEqual(comandos.limiar(), 0.6)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m unittest tests.test_comandos.TestDecidir` na raiz do projeto.
Expected: `AttributeError: module 'comandos' has no attribute 'decidir'`.

- [ ] **Step 3: Implementação mínima (acrescentar a `comandos.py`)**

```python
JEV_URL = "https://openrouter.ai/api/alpha/decisions"


def _env_modelo():
    m = os.environ.get("JEV_MODEL", "typesafe/jev-1.13").strip()
    return m or "typesafe/jev-1.13"


def limiar():
    """Confiança mínima p/ executar (env COMANDO_CONFIANCA, padrão 0.6)."""
    try:
        v = float(os.environ.get("COMANDO_CONFIANCA", str(LIMIAR_PADRAO)))
    except (TypeError, ValueError):
        return LIMIAR_PADRAO
    return min(1.0, max(0.0, v))


def decidir(frase, chave, timeout=20):
    """Pergunta ao JEV qual comando a frase é (+ditado). Falha → None."""
    import requests
    pergunta = {"intencao": {
        "type": "choice",
        "instructions": "A frase e um comando de voz ou ditado normal?",
        "criteria": {
            "pausar_musica": "Pausar, parar ou continuar musica/audio.",
            "continuar_musica": "Retomar a reproducao de musica/audio.",
            "proxima_faixa": "Pular para a proxima faixa/musica.",
            "faixa_anterior": "Voltar para a faixa/musica anterior.",
            "volume_mais": "Aumentar o volume.",
            "volume_menos": "Diminuir o volume.",
            "volume_mudo": "Mutar/desmutar o som.",
            "abrir_youtube": "Abrir o site do YouTube.",
            "abrir_gmail": "Abrir o Gmail.",
            "abrir_whatsapp": "Abrir o WhatsApp Web.",
            "abrir_github": "Abrir o GitHub.",
            "ditado": "Texto comum para digitar, nao e comando.",
        }}}
    try:
        r = requests.post(JEV_URL,
                          headers={"Authorization": "Bearer " + chave,
                                   "Content-Type": "application/json"},
                          json={"model": _env_modelo(),
                                "state": {"frase": frase},
                                "questions": pergunta},
                          timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        js = r.json()
        a = js["answers"]["intencao"]
        if a.get("type") != "choice":
            return None
        cmd, conf = a.get("choice"), a.get("confidence", 0.0)
        if cmd not in COMANDOS or not isinstance(conf, (int, float)):
            return None
        return (cmd, float(conf), (js.get("usage") or {}).get("cost"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m unittest discover -s tests` na raiz do projeto.
Expected: todos passam.

- [ ] **Step 5: Commit**

```bash
git add comandos.py tests/test_comandos.py
git commit -m "comandos de voz: decidir via JEV com falha aberta"
```

### Task 3: `tratar` + gancho no `bombeia` + config

**Files:**
- Modify: `comandos.py` (acrescenta `tratar`, `wake()`)
- Modify: `vosk_global.py` (ramo `"texto"` do `bombeia`, ~linhas 125-136)
- Modify: `.env.example` (acrescenta bloco comentado `COMANDO_WAKE`/`COMANDO_CONFIANCA`/`JEV_MODEL`/`OPENROUTER_API_KEY=` sem segredo)
- Test: `tests/test_comandos.py` (acrescenta classe `TestTratar`)

**Interfaces:**
- Consumes: `tira_wake`, `match_local`, `decidir`, `limiar`, `executar`, `COMANDOS` (Tasks 1-2).
- Produces: `tratar(texto) -> tuple[bool, str]` = `(True, msg_status)` executou, ou `(False, texto_pra_ditar)` (wake removido se havia); `wake() -> str`.

- [ ] **Step 1: Escrever os testes que falham**

```python
class TestTratar(unittest.TestCase):
    def test_local_executa_sem_rede(self):
        with mock.patch("requests.post") as p:
            ok, msg = comandos.tratar("Computador, pausar música.")
        p.assert_not_called()
        self.assertTrue(ok)
        self.assertIn("paus", msg.lower())

    def test_jev_executa_abrir(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube", "confidence": 0.9}}
        r = mock.Mock(status_code=200)
        r.json.return_value = {"answers": ans, "usage": {"cost": 0.00001}}
        with mock.patch("requests.post", return_value=r):
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
                ok, msg = comandos.tratar("Computador, abre o youtube.")
        self.assertTrue(ok)
        self.assertIn("Youtube", msg)
        self.assertIn("custo", msg)

    def test_confianca_baixa_vira_ditado(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube", "confidence": 0.4}}
        r = mock.Mock(status_code=200)
        r.json.return_value = {"answers": ans, "usage": {"cost": 0.0}}
        with mock.patch("requests.post", return_value=r):
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
                ok, texto = comandos.tratar("Computador, abre o youtube.")
        self.assertFalse(ok)
        self.assertNotIn("omputador", texto.lower())

    def test_sem_wake_nem_toca(self):
        with mock.patch("requests.post") as p:
            ok, texto = comandos.tratar("preciso comprar pão.")
        p.assert_not_called()
        self.assertEqual((ok, texto), (False, "preciso comprar pão."))

    def test_sem_key_cai_pra_ditado(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("requests.post") as p:
                ok, texto = comandos.tratar("Computador, que horas são?")
        p.assert_not_called()
        self.assertEqual((ok, texto), (False, "que horas são?"))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m unittest tests.test_comandos.TestTratar` na raiz do projeto.
Expected: `AttributeError: module 'comandos' has no attribute 'tratar'`.

- [ ] **Step 3a: Implementação mínima (`tratar` + `wake` em `comandos.py`)**

```python
def wake():
    """Palavra de ativação (env COMANDO_WAKE, padrão 'computador')."""
    w = os.environ.get("COMANDO_WAKE", WAKE_PADRAO).strip()
    return w or WAKE_PADRAO


def tratar(texto):
    """Cascata wake → local → JEV. (True, status) executou;
    (False, texto) segue p/ ditado (wake removido se havia)."""
    resto = tira_wake(texto, wake())
    if resto is None:
        return (False, texto)
    cmd, custo = match_local(resto), None
    if cmd is None:
        chave = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if chave:
            r = decidir(resto, chave)
            if r and r[0] != "ditado" and r[1] >= limiar():
                cmd, custo = r[0], r[2]
    if cmd is None or cmd == "ditado":
        return (False, resto)
    try:
        msg = executar(cmd)
    except ValueError:
        return (False, resto)
    if custo is not None:
        msg += f" (custo ${custo:.5f})"
    return (True, msg)
```

- [ ] **Step 3b: Gancho no `bombeia` (`vosk_global.py`, ramo `"texto"`)**

Trocar isto:

```python
            if tipo == "texto":
                valor = text_delivery.pontuar(valor)
                if entrega.deliver(valor):
```

por isto:

```python
            if tipo == "texto":
                valor = text_delivery.pontuar(valor)
                import comandos
                executou, saida = comandos.tratar(valor)
                if executou:
                    tray.set_status(saida)
                    orb.flash()
                    estado["ultimo"] = valor
                    print("##", saida, flush=True)
                elif entrega.deliver(saida):
```

(O `elif` preserva o fluxo antigo byte a byte quando não há wake: `tratar`
devolve `(False, texto original)`. `import comandos` no topo de `_app`,
ao lado de `import text_delivery`, em vez de dentro do ramo.)

- [ ] **Step 3c: `.env.example` (só placeholders, sem segredo)**

```ini
# ---------- comandos de voz (cérebro JEV via OpenRouter) ----------
# Palavra de ativação ("computador, pausar música"); sem ela tudo é ditado.
# COMANDO_WAKE=computador
# Confiança mínima do JEV p/ executar (0–1); abaixo disso vira ditado.
# COMANDO_CONFIANCA=0.6
# Modelo de decisão (fixo p/ limiares estáveis).
# JEV_MODEL=typesafe/jev-1.13
# Chave do OpenRouter (só p/ comandos fora do match local; sem ela, mídia local segue offline).
# OPENROUTER_API_KEY=
```

- [ ] **Step 4: Rodar suíte + verificação manual ao vivo**

Run: `python -m unittest discover -s tests` na raiz do projeto.
Expected: todos passam.
Manual (dono, com o app reiniciado): ditar `computador, pausar música` com
música tocando → pausa + status na bandeja; ditar frase normal → cola como
antes; `.ditador_textos_pendentes.json` não cresce em uso normal.

- [ ] **Step 5: Commit**

```bash
git add comandos.py tests/test_comandos.py vosk_global.py .env.example
git commit -m "comandos de voz: tratar + gancho no ditador"
```

## Self-Review

- Spec coverage: cada seção tem task dona (wake/config→T1+T3, match local→T1, JEV+ditado→T2, mapa fechado→T1, gancho→T3b, falha aberta→T2/T3, limiar 0.6→T2/T3, custo→T3 via `tratar` embutindo `usage.cost` no status).
- Placeholder scan: nenhum TBD/TODO; cada step tem código/comando/esperado.
- Type consistency: `tratar -> tuple[bool, str]` igual em T3 steps e gancho; `decidir -> tuple|None` igual em T2/T3; `COMANDOS` inclui `"ditado"` (T1) e `decidir` valida contra ele (T2).
- Review Focus: 5 linhas, cada uma com teste nomeado na task dona (T1: pontuado/acentos; T3: confiança baixa; T2+T3: rede/401; T1+T3: fora-do-mapa).

---

## v2 (adendo aprovado 2026-10-09; spec §v2)

Tasks 4-6 no mesmo branch `comandos-voz`, mesmo runner, mesmo padrão TDD
(RED→GREEN, suite inteira verde por task, 1 commit por task). `executar`
ganha 2º parâmetro `executar(comando, argumento="")` — compatível com os
testes T1 (default). `tratar` passa o resto como argumento.

Review Focus v2 (cada item com teste na task dona):
- "toca queen" resolve ID e dispara mpv sem abrir browser — T4.
- mpv morto no pause → media key (sem exceção); mpv vivo → IPC — T4.
- extração falha → abre página de busca, nunca exceção — T4.
- `COMANDO_VOZ=0` → nada sintetiza; `_sintetizar` falhando → sem exceção — T5.
- "abre o banco" → ditado em todos os mapas — T6.
- JEV 0.4 em `tocar_musica` → ditado — T6.

### Task 4: tocar via yt-dlp+mpv e ponte de pause (IPC)

**Files:** Modify `comandos.py`, `tests/test_comandos.py`.

**Interfaces:** Produces `_yt_id(consulta)->str|None`; `_mpv_vivo()->bool`;
`_mpv_ensure()->bool`; `_mpv_cmd(obj)->None`; `tocar(consulta)->str`;
`_limpa_consulta(resto)->str`; `executar(comando, argumento="")`;
`MPV_PIPE = r"\\.\pipe\mpv-ditador"`. Seams de módulo (mockáveis):
`_yt_run`, `_mpv_spawn`, `_mpv_send` (igual `_teclado`/`_abrir_url`).

- [ ] **Step 1: testes RED** (acrescentar `TestTocar` antes do guard):

```python
class TestTocar(unittest.TestCase):
    def test_yt_id_primeira_linha(self):
        with mock.patch.object(comandos, "_yt_run", return_value="abc123\ndef456\n"):
            self.assertEqual(comandos._yt_id("queen"), "abc123")

    def test_yt_id_falha_e_none(self):
        with mock.patch.object(comandos, "_yt_run", side_effect=RuntimeError("rede")):
            self.assertIsNone(comandos._yt_id("queen"))

    def test_tocar_dispara_mpv(self):
        with mock.patch.object(comandos, "_yt_id", return_value="abc123"):
            with mock.patch.object(comandos, "_mpv_ensure", return_value=True) as e:
                with mock.patch.object(comandos, "_mpv_send") as s:
                    msg = comandos.tocar("queen")
        e.assert_called_once_with()
        s.assert_called_once()
        self.assertIn("queen", msg.lower())
        self.assertEqual(s.call_args.args[0], {"command": ["loadfile",
            "https://www.youtube.com/watch?v=abc123", "append-play"]})

    def test_tocar_sem_id_abre_busca(self):
        with mock.patch.object(comandos, "_yt_id", return_value=None):
            with mock.patch.object(comandos, "_abrir_url") as u:
                msg = comandos.tocar("queen")
        u.assert_called_once()
        self.assertIn("search_query=queen", u.call_args.args[0])
        self.assertIn("busca", msg.lower())

    def test_pause_vai_pro_ipc_quando_mpv_vivo(self):
        mpv = mock.Mock()
        mpv.poll.return_value = None
        with mock.patch.object(comandos, "_MPV", mpv):
            with mock.patch.object(comandos, "_mpv_send") as s:
                with mock.patch.object(comandos, "_teclado") as t:
                    comandos.executar("pausar_musica")
        s.assert_called_once_with({"command": ["cycle", "pause"]})
        t.assert_not_called()

    def test_pause_cai_pra_media_key_sem_mpv(self):
        with mock.patch.object(comandos, "_MPV", None):
            with mock.patch.object(comandos, "_mpv_send") as s:
                with mock.patch.object(comandos, "_teclado") as t:
                    comandos.executar("pausar_musica")
        s.assert_not_called()
        t.assert_called_once_with("play/pause media")

    def test_match_local_toca(self):
        self.assertEqual(comandos.match_local("toca bohemian rhapsody"), "tocar_musica")
        self.assertEqual(comandos.match_local("quero ouvir queen"), "tocar_musica")
```

- [ ] **Step 2: rodar e ver falhar.** Run `python -m unittest tests.test_comandos.TestTocar`. Expected: `AttributeError` (`_yt_id` etc. inexistentes).
- [ ] **Step 3: implementação** (acrescentar a `comandos.py`):

```python
MPV_PIPE = r"\\.\pipe\mpv-ditador"
_MPV = None  # Popen do demônio mpv (seam de módulo p/ testes)


def _yt_run(args, timeout=15):
    """Roda yt-dlp e devolve stdout; qualquer falha levanta."""
    import subprocess
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def _yt_id(consulta):
    """Primeiro ID de `ytsearch1` (keyless) ou None."""
    try:
        for linha in _yt_run(["yt-dlp", f"ytsearch1:{consulta}",
                              "--flat-playlist", "--print", "id",
                              "--no-warnings"]).splitlines():
            if linha.strip():
                return linha.strip()
    except Exception:
        pass
    return None


def _mpv_vivo():
    return _MPV is not None and _MPV.poll() is None


def _mpv_spawn():
    """Sobe o demônio mpv (áudio, idle, IPC). Devolve Popen."""
    import subprocess
    return subprocess.Popen(
        ["mpv", "--no-video", "--idle=yes", f"--input-ipc-server={MPV_PIPE}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


def _mpv_ensure():
    """Garante o demônio de pé; True se pronto."""
    global _MPV
    try:
        if not _mpv_vivo():
            _MPV = _mpv_spawn()
        return _mpv_vivo()
    except Exception:
        return False


def _mpv_send(obj):
    """Um comando JSON ao IPC (seam mockável; exceção escapa p/ o chamador)."""
    with open(MPV_PIPE, "r+b", buffering=0) as pipe:
        pipe.write((__import__("json").dumps(obj) + "\n").encode())
        pipe.readline()


def _limpa_consulta(resto):
    """Tira verbo líder ('toca X'→'X'); sem verbo, o resto inteiro."""
    n = normaliza(resto)
    for verbo in ("toca ", "tocar ", "ouve ", "ouvir ", "ponha ", "poe ",
                  "bota pra tocar ", "quero ouvir ", "quero escutar "):
        if n.startswith(verbo):
            return n[len(verbo):].strip() or n
    return resto.strip()


def tocar(consulta):
    """Toca o 1º resultado no mpv; sem ID, abre a busca (fail-open)."""
    import urllib.parse
    consulta = _limpa_consulta(consulta)
    vid = _yt_id(consulta)
    if vid and _mpv_ensure():
        try:
            _mpv_send({"command": ["loadfile",
                                   f"https://www.youtube.com/watch?v={vid}",
                                   "append-play"]})
            return f"Tocando {consulta}"
        except Exception:
            pass
    _abrir_url("https://www.youtube.com/results?search_query=" +
               urllib.parse.quote_plus(consulta))
    return f"Busca aberta: {consulta}"
```

Trocar em `match_local`, antes do `return None`: verbos de tocar e
`abre/abrir` com resolução em ABRIR+APPS (APPS definido na Task 6 — ordem:
implementar mapa APPS nesta task como `{}` vazio? Não — dependência
invertida. Ruling: Task 4 implementa só `toca/ouvir`; `abre/abrir` local
fica na Task 6 junto do mapa APPS. `match_local` de T4 acrescenta:)

```python
    n2 = normaliza(frase)
    if n2.startswith(("toca ", "tocar ", "ouve ", "ouvir ", "ponha ",
                      "poe ", "bota pra tocar ", "quero ouvir ",
                      "quero escutar ")):
        return "tocar_musica"
```

Trocar em `executar`: assinatura `(comando, argumento="")`; transporte
(pausar/continuar/próxima/anterior) tenta IPC primeiro:

```python
def executar(comando, argumento=""):
    if comando in MIDIA:
        if _mpv_vivo():
            try:
                _mpv_send({"command": ["cycle", "pause"]}
                          if comando in ("pausar_musica", "continuar_musica")
                          else {"command": ["playlist-next"]}
                          if comando == "proxima_faixa"
                          else {"command": ["playlist-prev"]})
                return MIDIA[comando][1]
            except Exception:
                pass
        tecla, msg = MIDIA[comando]
        _teclado(tecla)
        return msg
    if comando == "tocar_musica":
        return tocar(argumento)
    if comando == "tocar_musica":
        return tocar(argumento)
    ...
```

(Resto de `executar` e `tratar` → `executar(cmd, resto)`: na Task 6, que
centraliza a troca de assinatura. Ruling: para não quebrar T1-T3 agora, a
Task 4 mantém `executar(comando)` e roteia pause via `_mpv_vivo()` dentro
do ramo MIDIA sem argumento; `tocar` é chamado só via JEV em T4 com
argumento pelo caminho novo `executar(cmd, resto)`? Contradição. Decisão
final simples: Task 4 JÁ troca a assinatura para `(comando, argumento="")`
e atualiza `tratar`? `tratar` é Task 3 (commitada). Alterar `tratar` aqui
é permitido (tasks evoluem código anterior com testes verdes após).
`tratar`: `executar(cmd)` → `executar(cmd, resto)`. Testes T1/T2/T3 seguem
verdes (default + JEV-mock sem arg). Registrar no commit.)

- [ ] **Step 4: suite verde.** Run `python -m unittest discover -s tests`. Expected: tudo passa.
- [ ] **Step 5: commit.** `git add comandos.py tests/test_comandos.py` + `git commit -m "comandos de voz: tocar via yt-dlp+mpv e ponte de pause"`.

### Task 5: respostas faladas (Edge-TTS + playsound, em thread)

**Files:** Modify `comandos.py`, `tests/test_comandos.py`, `vosk_global.py` (1 linha no gancho), `requirements-*.txt` (verificar e acrescentar `playsound` + `edge-tts` se ausentes), `.env.example` (`COMANDO_VOZ`).

**Interfaces:** Produces `falar(texto)->None`; seams `_sintetizar(texto)->path`, `_tocar_arquivo(path)->None`; voz fixa `pt-BR-AntonioNeural`.

- [ ] **Step 1: testes RED:**

```python
class TestFalar(unittest.TestCase):
    def test_flag_desligada_nao_sintetiza(self):
        with mock.patch.dict("os.environ", {"COMANDO_VOZ": "0"}):
            with mock.patch.object(comandos, "_sintetizar") as s:
                comandos.falar("oi")
                s.assert_not_called()

    def test_falar_chama_sintese_e_toca(self):
        with mock.patch.dict("os.environ", {"COMANDO_VOZ": "1"}):
            with mock.patch.object(comandos, "_sintetizar", return_value="f.mp3") as s:
                with mock.patch.object(comandos, "_tocar_arquivo") as t:
                    with mock.patch("os.remove") as rm:
                        comandos._falar_sync("São 14h30.")
        s.assert_called_once_with("São 14h30.")
        t.assert_called_once_with("f.mp3")
        rm.assert_called_once()

    def test_sintese_falhando_nao_levanta(self):
        with mock.patch.object(comandos, "_sintetizar", side_effect=RuntimeError("rede")):
            comandos._falar_sync("oi")  # sem exceção = passou
```

- [ ] **Step 2: rodar e ver falhar** (`AttributeError: falar`).
- [ ] **Step 3: implementação:**

```python
def _voz_ativa():
    return os.environ.get("COMANDO_VOZ", "1").strip() not in ("0", "não", "nao", "off", "false")


def _sintetizar(texto):
    """mp3 temporário com Edge-TTS pt-BR; exceção escapa p/ _falar_sync."""
    import asyncio
    import edge_tts
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="ditador_")
    os.close(fd)
    asyncio.run(edge_tts.Communicate(texto, voice="pt-BR-AntonioNeural").save(path))
    return path


def _tocar_arquivo(path):
    import playsound
    playsound.playsound(path, block=True)


def _falar_sync(texto):
    try:
        path = _sintetizar(texto)
        try:
            _tocar_arquivo(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass  # voz nunca quebra o fluxo


def falar(texto):
    """Fala o status em thread daemon (nunca bloqueia a bandeja)."""
    if not texto or not _voz_ativa():
        return
    import threading
    threading.Thread(target=_falar_sync, args=(texto,), daemon=True).start()
```

Gancho (`vosk_global.py`, após `print("##", ...)`): `comandos.falar(saida)`.
`.env.example`: `# COMANDO_VOZ=1` (0 = mudo) no bloco comandos.
Requirements: ler `requirements.txt` e `requirements-vosk.txt`; acrescentar
`playsound` (e `edge-tts` se ausente) no arquivo do ditador.

- [ ] **Step 4: suite verde** + `py_compile` nos tocados.
- [ ] **Step 5: commit** (`comandos.py tests/test_comandos.py vosk_global.py .env.example requirements*.txt` conforme o caso).

### Task 6: abrir programas + hora + pesquisar + criteria JEV

**Files:** Modify `comandos.py`, `tests/test_comandos.py`, `.env.example` (nada novo; bloco já prevê).

**Interfaces:** `APPS: dict[str,str]`; `executar` ramos `abrir_programa/dizer_horas/pesquisar_web`; `decidir` criteria +4.

- [ ] **Step 1: testes RED:**

```python
class TestV2Extra(unittest.TestCase):
    def test_abrir_programa(self):
        with mock.patch.object(comandos, "_abrir_app") as a:
            msg = comandos.executar("abrir_programa", "a calculadora")
        a.assert_called_once_with("calc")
        self.assertIn("calculadora", msg.lower())

    def test_abrir_desconhecido_levanta(self):
        with self.assertRaises(ValueError):
            comandos.executar("abrir_programa", "o banco")

    def test_horas(self):
        msg = comandos.executar("dizer_horas")
        self.assertRegex(msg, r"São \d{1,2}h\d{2}")

    def test_pesquisar(self):
        with mock.patch.object(comandos, "_abrir_url") as u:
            comandos.executar("pesquisar_web", "bolo de cenoura")
        self.assertIn("bolo+de+cenoura", u.call_args.args[0])

    def test_match_abrir_local(self):
        self.assertEqual(comandos.match_local("abre o youtube"), "abrir_youtube")
        self.assertEqual(comandos.match_local("abrir a calculadora"), "abrir_programa")
        self.assertEqual(comandos.match_local("que horas são"), "dizer_horas")
        self.assertEqual(comandos.match_local("pesquisa bolo de cenoura"), "pesquisar_web")

    def test_tratar_abrir_fora_do_mapa_vira_ditado(self):
        ok, texto = comandos.tratar("Computador, abre o banco.")
        self.assertEqual((ok, texto), (False, "abre o banco."))
```

Atenção (ruling registrado): `test_jev_executa_abrir` (T3) passa a cair no
match local; trocar a frase dele para `"Computador, por favor acesse o
youtube."` (fora dos padrões locais, mantém a cobertura do ramo JEV).

- [ ] **Step 2: rodar e ver falhar** (6 `AttributeError`/mismatch).
- [ ] **Step 3: implementação:**

```python
APPS = {
    "bloco de notas": "notepad",
    "calculadora": "calc",
    "paint": "mspaint",
    "explorador": "explorer",
}


def _tira_artigo(nome):
    n = normaliza(nome)
    for art in ("o ", "a ", "os ", "as ", "um ", "uma "):
        if n.startswith(art):
            return n[len(art):]
    return n


def _abrir_app(executavel):
    import os as _os
    _os.startfile(executavel)
```

Em `executar`, após o ramo ABRIR:

```python
    if comando == "abrir_programa":
        nome = _tira_artigo(argumento)
        if nome not in APPS:
            raise ValueError(f"programa desconhecido: {argumento}")
        _abrir_app(APPS[nome])
        return f"{nome.capitalize()} aberto"
    if comando == "dizer_horas":
        import datetime
        agora = datetime.datetime.now()
        return f"São {agora.hour}h{agora.minute:02d}"
    if comando == "pesquisar_web":
        import urllib.parse
        q = (argumento or "").strip(" \t\n.,;:!?")
        if not q:
            raise ValueError("pesquisa vazia")
        _abrir_url("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
        return f"Pesquisando {q}"
```

Em `match_local`, antes do `return None` (ordem: específico antes do
genérico "abre"):

```python
    n2 = normaliza(frase)
    if n2.startswith(("que horas", "que hora e", "me diz as horas", "diga as horas")):
        return "dizer_horas"
    if n2.startswith(("pesquisa ", "pesquisar ", "busca ", "buscar ", "procura por ")):
        return "pesquisar_web"
    if n2.startswith(("abre ", "abrir ", "abra ")):
        return _resolve_abrir(n2.split(" ", 1)[1])  # None cai adiante
```

com o helper:

```python
_NOMES_SITES = {"youtube": "abrir_youtube", "gmail": "abrir_gmail",
                "whatsapp": "abrir_whatsapp", "github": "abrir_github"}


def _resolve_abrir(nome):
    nome = _tira_artigo(nome)
    for pedaco, cid in _NOMES_SITES.items():
        if pedaco in nome:
            return cid
    if nome in APPS:
        return "abrir_programa"
    return None
```

e `executar("abrir_youtube", ...)` ignora argumento (compat T1).

`COMANDOS`: acrescentar `{"abrir_programa", "tocar_musica", "dizer_horas", "pesquisar_web"}`.
`decidir` criteria: +4 entradas (`tocar_musica`: "Tocar uma música/áudio do YouTube.";
`abrir_programa`: "Abrir um programa do computador."; `dizer_horas`: "Dizer que horas são.";
`pesquisar_web`: "Pesquisar algo na web."). `tratar`: `executar(cmd)` →
`executar(cmd, resto)`; JEV-miss de programa (`abrir_programa` com resto
fora do mapa → `ValueError` → `(False, resto)`).

- [ ] **Step 4: suite verde** (inclui teste T3 ajustado).
- [ ] **Step 5: commit** (`comandos.py tests/test_comandos.py`).

## Self-Review (adendo v2)

- Type consistency: `executar(comando, argumento="")` — chamadas antigas
  (T1, gancho T3b) seguem válidas; `tratar` repassa `resto`.
- Ruling T4: `abre/abrir` local ficou na T6 (mapa APPS nasce lá); T4 cobre
  só `toca/ouvir`. Ruling T6: `test_jev_executa_abrir` muda a frase para
  fora do match local (intenção preservada: ramo JEV).
- Risco anotado: YouTube quebra extratores às vezes — fallback é a página
  de busca (fail-open), nunca exceção.
- Sem placeholders: comandos mpv exatos (`loadfile…append-play`,
  `cycle pause`, `playlist-next/prev`), pipe `\\.\pipe\mpv-ditador`,
  voz `pt-BR-AntonioNeural`.
