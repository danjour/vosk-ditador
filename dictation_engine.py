"""Captura, segmentação e transcrição do ditador: faster-whisper medium local,
microfone selecionado por nome. Nenhum stream, modelo ou thread no nível do módulo."""
import array
import os
import queue
import threading
import time

from runtime_support import prepare_cuda, resolve_input_device

SR = 16000
_WHISPER = None


def get_model():
    """Carrega o medium uma vez: GPU cuda int8_float16 com fallback cpu int8.
    Chame da thread do worker — a carga pode levar vários segundos."""
    global _WHISPER
    if _WHISPER is None:
        import numpy as np
        from faster_whisper import WhisperModel
        nome = os.environ.get("DITADOR_MODEL", "medium").strip() or "medium"
        prepare_cuda()  # compartilhado com o gravador; conserva os handles de DLL
        for dev, ct in (("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                m = WhisperModel(nome, device=dev, compute_type=ct)
                # aquecimento CONSUMINDO o gerador: erro de kernel/GPU aparece aqui,
                # não na 1ª frase real.
                segs, _ = m.transcribe(np.zeros(SR // 2, dtype=np.float32), language="pt")
                list(segs)
                _WHISPER = m
                print(f"whisper {nome} em", dev, flush=True)
                break
            except Exception as e:
                print(f"whisper {dev} falhou ({str(e)[:80]}), tentando próximo...", flush=True)
        if _WHISPER is None:
            raise SystemExit("nenhum backend whisper disponível")
    return _WHISPER


def limiares(amb):
    """Calibração pura: pico do ruído medido vira limiar de silêncio e de voz."""
    sil = max(0.004, amb * 2.5)
    return sil, sil * 1.8


def fatia_por_silencio(lvl, sp, falando, silencio, duracao):
    """Decisão pura de fatiar (testável sem micro): 'voz' (acima do limiar),
    'fala' (pausa > 0,7 s depois de falar), 'descarga' (monólogo ~10 s) ou
    None (segue acumulando). silencio e duracao em segundos."""
    if lvl > sp:
        return "voz"
    if falando and silencio > 0.7 and duracao > 0.5:
        return "fala"
    if duracao > 10.0:
        return "descarga"
    return None


class DictationEngine:
    """Captura o micro, fatia por silêncio e transcreve. Os callbacks chegam em
    threads de fundo — quem os recebe é que decide como levar tudo à interface."""

    def __init__(self, on_text, on_level, on_state, on_error, on_voice=None):
        self.on_text, self.on_level = on_text, on_level
        self.on_state, self.on_error = on_state, on_error
        self.on_voice = on_voice  # dispara 1x por fala, ao começar a voz
        self.listening = False
        self.audio_q = queue.Queue()
        self.buf = bytearray()  # áudio da frase em curso (int16 mono 16k)
        self.level_raw = 0.0
        self.sil, self.sp = 0.006, 0.012
        self.calib, self.amb = None, None
        self.falando, self.ult = False, 0.0
        self._ativa = False       # transição de escuta já vista pela thread do worker
        self._flush = True
        self._parar = threading.Event()
        self._reabrir = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="ditador-mic", daemon=True)
        self._thread.start()

    def viva(self):
        return self._thread.is_alive()

    def toggle(self):
        """Liga/pausa; a calibração e a carga do modelo acontecem na thread do worker
        (nunca na thread do Tk)."""
        self.listening = not self.listening
        return self.listening

    def stop(self, flush=True):
        """Encerra; com flush, a fala não entregue que sobrou no buffer é transcrita
        via on_text antes de parar. O primeiro stop decide o flush."""
        if not self._parar.is_set():
            self._flush = flush
        self.listening = False
        self._parar.set()

    def reabrir(self):
        """Fecha o stream atual; o worker reabre resolvendo o microfone de novo
        (troca de microfone pela bandeja sem reiniciar o app)."""
        self._reabrir.set()

    def close(self):
        if not self._parar.is_set():
            self.stop(flush=False)
        self._thread.join(timeout=2)

    def _estado(self, texto):
        self.on_state(texto)

    def _erro(self, texto):
        print(texto, flush=True)
        self.on_error(texto)

    def _transcreve(self, buf):
        if len(buf) < SR * 2 * 0.3:  # menos de 0.3s de áudio: ruído/click
            return
        import numpy as np
        pcm = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
        self._estado("Transcrevendo…")
        txt = ""
        try:
            segs, _ = get_model().transcribe(pcm, language="pt", beam_size=1,
                                             vad_filter=True,  # ponytail: greedy; beam 3 se a frase piorar
                                             condition_on_previous_text=False,
                                             without_timestamps=True)
            txt = " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:
            print("whisper falhou:", str(e)[:100], flush=True)
            self._erro(f"transcrição: {str(e)[:80]}")
        if txt:
            self.on_text(txt)
            print(">>", txt, flush=True)
        self._estado("Ouvindo" if self.listening else "Pausado")

    def _worker(self):
        import sounddevice as sd

        def cb(indata, frames, tinfo, status):
            if self.listening:
                pcm = bytes(indata)  # cdata de char: sem bytes() o array quebra por elemento
                self.audio_q.put(pcm)
                m = array.array("h", pcm)[::8]  # RMS de 1/8 das amostras, só p/ orbe+VAD
                acc = 0
                for v in m:
                    acc += v * v
                self.level_raw = (acc / len(m)) ** 0.5 / 32768
                self.on_level(self.level_raw)

        while not self._parar.is_set():
            stream = None  # BT dorme — mic por nome; falhou, avisa e tenta de novo em 3 s
            while stream is None and not self._parar.is_set():
                try:
                    dev = resolve_input_device(os.environ.get("VOSK_MIC"))
                    s = sd.RawInputStream(samplerate=SR, blocksize=4000, dtype="int16",
                                          channels=1, device=dev, callback=cb)
                    s.start()  # é aqui que o BT morto falha, não no construtor
                    stream = s
                    print("mic:", dev if dev is not None else "default", flush=True)
                    self._estado("Ouvindo" if self.listening else "Pausado")
                except Exception as e:
                    self._erro(f"microfone: {str(e)[:80]}")
                    self._parar.wait(3)
            if stream is None:
                break
            try:
                with stream:
                    while (not self._parar.is_set() and not self._reabrir.is_set()
                           and stream.active):
                        self._passo()
            except Exception as e:
                self._erro(f"microfone: {str(e)[:80]}")
                self._parar.wait(3)
            finally:
                self._reabrir.clear()  # stream fechado: reabre resolvendo o mic de novo
        if self._flush:
            self._drena()
            self._transcreve(self.buf)  # fala não entregue no encerramento
        self.buf.clear()

    def _drena(self):
        while True:
            try:
                self.buf += self.audio_q.get_nowait()
            except queue.Empty:
                return

    def _passo(self):
        if self.listening != self._ativa:
            if self.listening:  # começo da escuta: modelo na thread do worker + calibração
                if _WHISPER is None:
                    self._estado("Carregando modelo…")
                    get_model()
                self.amb, self.calib = [], time.time() + 0.6
                self.buf.clear()
                while not self.audio_q.empty():
                    self.audio_q.get()  # descarta áudio velho
                self._estado("Ouvindo")
            else:
                self._estado("Pausado")
            self._ativa = self.listening
        drenou = False
        try:
            while True:
                self.buf += self.audio_q.get_nowait()
                drenou = True
        except queue.Empty:
            pass
        if not drenou:
            time.sleep(0.02)
        lvl, agora = self.level_raw, time.time()
        if not self.listening:
            if len(self.buf) > SR * 2 * 0.5:  # sobrou fala ao pausar: cospe antes de parar
                self._transcreve(self.buf)
            self.buf.clear()
            self.falando = False
            return
        if self.calib:  # primeiros 0.6s medem o silêncio deste micro
            if agora < self.calib:
                self.amb.append(lvl)
                self.buf.clear()
                return
            self.sil, self.sp = limiares(max(self.amb or [0.0]))
            self.calib = None
            print(f"calibrado: silêncio < {self.sil:.4f}", flush=True)
            return
        passo = fatia_por_silencio(lvl, self.sp, self.falando, agora - self.ult,
                                   len(self.buf) / (SR * 2))
        if passo == "voz":
            if not self.falando and self.on_voice is not None:
                # começou a falar: o destino é a janela focada AGORA (o usuário
                # costuma clicar no destino depois do hotkey). Falha segura: se
                # errar a janela, o deliver guarda como pendente, nunca cola errado.
                try:
                    self.on_voice()
                except Exception:
                    pass
            self.falando = True
            self.ult = agora
        elif passo == "fala":  # pausa > 0,7 s: a frase acabou
            self._transcreve(self.buf); self.buf.clear(); self.falando = False
        elif passo == "descarga":  # monólogo longo: descarrega a cada ~10s
            self._transcreve(self.buf); self.buf.clear(); self.falando = False
