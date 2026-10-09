"""Ditador global offline (Whisper medium local): Ctrl+Alt+E ouve; ao pausar a fala,
cola a frase no app focado. Ctrl+Alt+Q encerra. --preview mostra só o orbe."""
import math
import os
import queue
import sys
import time

PREVIEW = "--preview" in sys.argv  # preview: só o orbe animado, sem modelo/mic


def _carrega_dotenv():
    try:
        from runtime_support import load_settings
        load_settings()
    except ImportError:
        pass  # .env continua opcional, como antes


def _preview():
    """Só o orbe: pausa > voz > confirmação > pausa simulados, diag a cada 2 s e
    saída sozinha em ~12 s. Sem microfone, modelo, bandeja ou trava de instância."""
    import tempfile
    from orb_view import Orb, _TOP, _BOT, orb_img

    orb = Orb()
    for _e, _t in ((0.05, 0.0), (0.6, 2.1), (1.2, 4.2)):  # amostras p/ conferir o render
        orb_img(_e, _TOP, _BOT, _t).save(
            os.path.join(tempfile.gettempdir(), f"orb_e{int(_e*100):03d}.png"))
    inicio = time.time()
    print("preview: pausa > voz > confirmacao > pausa (12s)", flush=True)
    piscou = []

    def simula():
        elapsed = time.time() - inicio
        orb.set_listening(2.0 <= elapsed < 9.5)
        envelope = max(0.0, math.sin((elapsed-2.0) * 1.4))
        orb.set_level(0.10 * envelope * (0.55 + 0.45*math.sin(elapsed*8.2)**2))
        if 8.6 <= elapsed < 9.0 and not piscou:
            piscou.append(True)
            orb.flash()
        if elapsed < 11.5:
            orb.root.after(16, simula)

    def diag():
        print(f"diag: ticks={orb.ticks}/s geom={orb.overlay.geometry()} "
              f"visible={orb.overlay.winfo_viewable()}", flush=True)
        orb.ticks = 0

    orb.root.after(1000, diag)
    orb.root.after(3000, diag)
    orb.root.after(16, simula)
    orb.schedule_close(12000)  # preview se fecha sozinho
    orb.run()
    return 0


def _app():
    """Modo normal: orbe + bandeja + hotkeys + engine, amarrados por uma fila de
    eventos — callbacks de thread nunca tocam o Tk direto (mesmo padrão do reuniao)."""
    import tkinter as tk
    from tkinter import messagebox
    import keyboard
    from desktop_tray import Tray
    from dictation_engine import DictationEngine
    from orb_view import Orb
    from runtime_support import AlreadyRunning, SingleInstance, save_microphone
    import text_delivery
    import comandos
    from text_delivery import Colagem

    eventos = queue.Queue()
    estado = {"ultimo": None, "fechando": False, "prazo": 0.0}

    def colar(texto):
        """Colagem na thread do Tk, como no tick antigo."""
        root.clipboard_clear(); root.clipboard_append(texto); root.update()
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        orb.flash()

    def alternar():
        if estado["fechando"]:
            return
        ouvindo = engine.toggle()  # carga do modelo fica na thread do worker
        orb.set_listening(ouvindo)
        if ouvindo:
            entrega.set_target()  # registra o app focado como destino do ditado
        print("OUVINDO (whisper medium)" if ouvindo else "pausado", flush=True)

    def escolher_mic(valor):
        try:
            save_microphone("VOSK_MIC", valor)
            engine.reabrir()  # o worker fecha e reabre o stream com o novo micro
        except Exception as erro:
            eventos.put(("erro", f"salvando microfone: {erro}"))

    def recuperar_pendente():
        texto = text_delivery.recuperar()
        if texto:
            root.clipboard_clear(); root.clipboard_append(texto); root.update()
            messagebox.showinfo("Ditador", "Texto pendente recuperado e copiado:",
                                detail=texto, parent=root)
        else:
            messagebox.showinfo("Ditador", "Nenhum texto pendente.", parent=root)

    def abrir_ultimo():
        texto = estado["ultimo"]
        if not texto:
            messagebox.showinfo("Ditador", "Nenhum texto transcrito ainda.", parent=root)
            return
        root.clipboard_clear(); root.clipboard_append(texto); root.update()
        messagebox.showinfo("Ditador", "Último texto transcrito (copiado):",
                            detail=texto, parent=root)

    def encerrar():
        if estado["fechando"]:
            return
        estado["fechando"] = True
        estado["prazo"] = time.time() + 3.0  # guarda de tempo p/ não travar no fim
        ouvindo = engine.listening
        orb.set_listening(False)
        tray.set_status("Salvando")
        engine.stop(flush=ouvindo)  # fala não entregue vira texto final via on_text

    def bombeia():
        while not eventos.empty():
            tipo, valor = eventos.get_nowait()
            if tipo == "texto":
                valor = text_delivery.pontuar(valor)
                executou, saida = comandos.tratar(valor)
                if executou:
                    tray.set_status(saida)
                    orb.flash()
                    estado["ultimo"] = valor
                    print("##", saida, flush=True)
                    comandos.falar(saida)
                elif entrega.deliver(saida):
                    entrega.set_target()  # logo após cada colagem bem-sucedida
                    estado["ultimo"] = valor
                    print(">>", valor, flush=True)
                else:
                    tray.set_status("Texto guardado: foco mudou")
                    print("texto guardado (foco mudou):", valor, flush=True)
            elif tipo == "nivel":
                orb.set_level(valor)
            elif tipo == "voz":
                entrega.set_target()  # voz começou: destino é a janela focada agora
            elif tipo == "estado":
                tray.set_status(valor)
            elif tipo == "erro":
                tray.set_status("Erro: " + valor)
            elif tipo == "alternar":
                alternar()
            elif tipo == "encerrar":
                encerrar()
        if estado["fechando"]:
            # espera o worker descarregar o texto final (ou até a guarda estourar)
            if time.time() >= estado["prazo"] or not engine.viva():
                tray.stop()
                root.destroy()
                return
        root.after(50, bombeia)

    try:
        # "reuniao" usa outro nome: ditador e gravador podem rodar juntos
        with SingleInstance("ditador"):
            orb = Orb()  # dono do root Tk oculto, compartilhado com a bandeja
            root = orb.root
            entrega = Colagem(colar)
            engine = DictationEngine(
                lambda t: eventos.put(("texto", t)),
                lambda n: eventos.put(("nivel", n)),
                lambda s: eventos.put(("estado", s)),
                lambda e: eventos.put(("erro", e)),
                lambda: eventos.put(("voz", None)))
            tray = Tray(root, "Ditador", alternar, encerrar, escolher_mic,
                        lambda: os.environ.get("VOSK_MIC"), abrir_ultimo,
                        on_recover=recuperar_pendente)
            hotkeys = [
                keyboard.add_hotkey("ctrl+alt+e", lambda: eventos.put(("alternar", None))),
                keyboard.add_hotkey("ctrl+alt+q", lambda: eventos.put(("encerrar", None))),
            ]
            print("Pronto. Ctrl+Alt+E ouve/pausa, Ctrl+Alt+Q encerra.", flush=True)
            tray.start()
            root.after(50, bombeia)
            try:
                orb.run()  # mainloop; a superfície do orbe fecha ao sair
            finally:
                engine.close()
                tray.stop()
                for tecla in hotkeys:
                    keyboard.remove_hotkey(tecla)
            return 0
    except AlreadyRunning as erro:
        print(erro, file=sys.stderr)
        return 1


def main():
    _carrega_dotenv()
    if PREVIEW:
        return _preview()
    return _app()


if __name__ == "__main__":
    sys.exit(main())
