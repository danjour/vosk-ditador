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
